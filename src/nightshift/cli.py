"""Command line interface for nightshift."""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from nightshift import __version__
from nightshift.agent import AgentError
from nightshift.commits import CommitError, commit_report
from nightshift.config import ConfigError, RunConfig, load_config
from nightshift.gate import clean, file, last_gate_line, start, state, wait
from nightshift.git import run_git
from nightshift.items import Item, ItemsError, load_items
from nightshift.progress import Progress, read_progress
from nightshift.render import RenderError, render_run
from nightshift.report import build_report, format_minutes
from nightshift.status import current_item_seconds
from nightshift.supervise import read_supervisor_events, supervise
from nightshift.watch import watch

_EXAMPLE_RUN_TOML = """\
# nightshift run configuration. Every key is shown below; the required keys
# are name, repo and check.test, the rest fall back to their commented defaults.

[run]
name = "example"      # short id for PROGRESS-<name>.md and commit ids (letters, digits, dashes)
repo = ".."           # path to the repo root, relative to this run dir or absolute
# branch = ""         # when set, refuse to start unless the repo is on this branch
# prefix = "T"        # item id prefix: T1, T2, ...
# command = ["nightshift"]  # program the spec tells the agent to run; full path plus -m nightshift when not on PATH
# templates = ""      # directory of your own start.md / item.md templates, relative to this run dir or absolute

[check]
test = ["pytest"]        # test command run from the repo root; no -q here, see "Never -q twice" in docs/LESSONS.md
# lint = []               # optional lint command before the tests, e.g. ["ruff", "check"]
# pre = []                # optional commands run after lint and before the tests (list of lists)
# min_passed = 1          # minimum passed tests for a green gate
# wait_seconds = 25       # how long one gate wait call may block
# timeout_hint_seconds = 30  # command limit the agent works under

[agent]
# launch = []                 # optional command to start the agent (supervisor)
# launch_ok_codes = [0]       # exit codes of the launch command that are not worth a log line
# status = []                 # optional command that prints agent status lines
# running_pattern = "running"  # substring in status output meaning the agent is alive
# poll_seconds = 30           # supervisor poll interval
# max_sessions = 20           # restart limit before giving up
# launch_grace_polls = 4      # polls to wait for a launched agent to report running
# status_retry_polls = 10     # unreadable status polls in a row before a session counts as ended
# launch_backoff_seconds = 60  # wait before relaunching a failed start; doubles, at most 900
# max_launch_failures = 6     # failed starts in a row before giving up with exit code 2
# idle_minutes = 60           # no commit and no file change this long means stuck
# stop = []                   # optional command run once when a session looks stuck
"""

_EXAMPLE_ITEMS_MD = """\
# Items for the example run

## first item
Do the first thing, commit it and tick it in PROGRESS-example.md.

## second item
Do the second thing, then send the final message described in 00-start.md.
"""


def build_parser() -> argparse.ArgumentParser:
    """Build the top level argument parser."""
    parser = argparse.ArgumentParser(
        prog="nightshift",
        description="Run a local LLM coding agent unattended.",
    )
    parser.add_argument("--version", action="version", version=f"nightshift {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create a run directory with example files")
    init_parser.add_argument("run_dir", type=Path, help="path of the new run directory")

    build_parser = subparsers.add_parser("build", help="render the spec files into the run directory")
    build_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")

    supervise_parser = subparsers.add_parser(
        "supervise", help="relaunch the agent until every item is done or a stop condition hits"
    )
    supervise_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")

    queue_parser = subparsers.add_parser(
        "queue", help="supervise several run dirs back to back, in order"
    )
    queue_parser.add_argument("run_dirs", nargs="+", type=Path, help="run directories, supervised in order")

    status_parser = subparsers.add_parser("status", help="print a one-screen summary of the run")
    status_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")
    status_parser.add_argument("--json", action="store_true", help="print the summary as one JSON object")

    watch_parser = subparsers.add_parser(
        "watch", help="print new commits and supervisor events as they appear"
    )
    watch_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")
    watch_parser.add_argument(
        "--every", type=float, default=20.0, help="seconds between polls (default 20, at least 1)"
    )
    watch_parser.add_argument("--once", action="store_true", help="print what exists now and exit")

    doctor_parser = subparsers.add_parser(
        "doctor", help="check that the run dir is ready for an unattended run"
    )
    doctor_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")

    report_parser = subparsers.add_parser(
        "report", help="print a markdown summary of the run for a pull request body"
    )
    report_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")
    report_parser.add_argument(
        "--out", type=Path, default=None, help="write the summary to this file instead of stdout"
    )

    commits_parser = subparsers.add_parser(
        "commits", help="check that each item has exactly one well-formed commit"
    )
    commits_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")
    commits_parser.add_argument(
        "--base", default=None, help="ref to start the range at; default is the merge-base with run.branch"
    )

    gate_parser = subparsers.add_parser("gate", help="run a check behind the detached gate")
    gate_sub = gate_parser.add_subparsers(dest="gate_command", required=True)
    gate_start = gate_sub.add_parser("start", help="run lint and pre checks, then start the test command detached")
    gate_start.add_argument("run_dir", type=Path, help="path of an existing run directory")
    gate_wait = gate_sub.add_parser("wait", help="wait for the gated run and report pass or fail")
    gate_wait.add_argument("run_dir", type=Path, help="path of an existing run directory")
    gate_wait.add_argument("n", type=int, help="call number; use a new one on every call")
    gate_file = gate_sub.add_parser("file", help="start one test file with coverage of one module")
    gate_file.add_argument("run_dir", type=Path, help="path of an existing run directory")
    gate_file.add_argument("test_file", help="path to the test file, relative to the repo root")
    gate_file.add_argument("module", help="module name used for the coverage report line")
    gate_clean = gate_sub.add_parser(
        "clean", help="delete <run dir>/.gate/ and print what it removed"
    )
    gate_clean.add_argument("run_dir", type=Path, help="path of an existing run directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch; return the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return _init(args.run_dir)
    if args.command == "build":
        return _build(args.run_dir)
    if args.command == "supervise":
        return _supervise(args.run_dir)
    if args.command == "queue":
        return _queue(args.run_dirs)
    if args.command == "status":
        return _status(args.run_dir, as_json=args.json)
    if args.command == "watch":
        return _watch(args.run_dir, every=args.every, once=args.once)
    if args.command == "doctor":
        return _doctor(args.run_dir)
    if args.command == "report":
        return _report(args.run_dir, out=args.out)
    if args.command == "commits":
        return _commits(args.run_dir, base=args.base)
    if args.command == "gate":
        return _gate(args)
    parser.print_help()
    return 2


def _init(run_dir: Path) -> int:
    """Create a new run directory with example run.toml and items.md."""
    for path in (run_dir / "run.toml", run_dir / "items.md"):
        if path.exists():
            print(f"error: {path} already exists", file=sys.stderr)
            return 1
    if run_dir.exists() and not run_dir.is_dir():
        print(f"error: {run_dir} is not a directory", file=sys.stderr)
        return 1
    run_dir.mkdir(parents=True, exist_ok=True)
    toml_path = run_dir / "run.toml"
    items_path = run_dir / "items.md"
    toml_path.write_text(_EXAMPLE_RUN_TOML, encoding="utf-8", newline="\n")
    items_path.write_text(_EXAMPLE_ITEMS_MD, encoding="utf-8", newline="\n")
    print(toml_path)
    print(items_path)
    return 0


def _build(run_dir: Path) -> int:
    """Load config and items, render into the run directory."""
    try:
        config = load_config(run_dir)
        items = load_items(run_dir)
        written = render_run(config, items, run_dir)
    except (ConfigError, ItemsError, RenderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


def _supervise(run_dir: Path) -> int:
    """Load config and supervise the agent until every item is done."""
    try:
        config = load_config(run_dir)
        return supervise(config, run_dir)
    except (ConfigError, ItemsError, AgentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _queue(run_dirs: list[Path]) -> int:
    """Supervise each run dir in order; stop at the first non-zero code."""
    for run_dir in run_dirs:
        if not run_dir.is_dir() or not (run_dir / "run.toml").is_file():
            print(f"error: {run_dir} does not exist or has no run.toml", file=sys.stderr)
            return 2
    for run_dir in run_dirs:
        code = _supervise(run_dir)
        print(f"queue: {run_dir} finished with {code}", flush=True)
        if code != 0:
            return code
    print(f"queue: all {len(run_dirs)} runs finished", flush=True)
    return 0


def _doctor(run_dir: Path) -> int:
    """Check that the run dir is ready for an unattended run; exit 1 on any problem."""
    problems = 0

    def report(check: str, ok: bool, problem: str = "") -> None:
        nonlocal problems
        if ok:
            print(f"ok: {check}")
        else:
            problems += 1
            print(f"problem: {check}: {problem}")

    config: RunConfig | None = None
    try:
        config = load_config(run_dir)
        report("run.toml", True)
    except ConfigError as exc:
        report("run.toml", False, str(exc))
    if config is None:
        return 1

    items: list[Item] | None = None
    try:
        items = load_items(run_dir)
        with TemporaryDirectory() as tmp:
            render_run(config, items, Path(tmp), run_dir=run_dir)
        report("items.md", True)
    except (ItemsError, RenderError) as exc:
        report("items.md", False, str(exc))

    if config.templates and items is not None:
        templates_dir = Path(config.templates)
        if not templates_dir.is_absolute():
            templates_dir = run_dir / templates_dir
        if not templates_dir.is_dir():
            report("templates", False, f"directory missing: {templates_dir}")
        else:
            try:
                with TemporaryDirectory() as tmp:
                    render_run(config, items, Path(tmp), run_dir=run_dir)
                report("templates", True)
            except RenderError as exc:
                report("templates", False, str(exc))

    repo_result = _repo_check(config.repo)
    if repo_result is None:
        print("note: skipped the git work tree check because git is not on PATH")
    else:
        ok, problem = repo_result
        report("repo", ok, problem)

    if config.branch:
        ok, problem = _branch_check(config.repo, config.branch)
        report("branch", ok, problem)

    for check, program in _program_checks(config):
        if shutil.which(program) is not None or Path(program).is_file():
            report(check, True)
        else:
            report(check, False, f"install {program} or put it on PATH")

    if config.wait_seconds < config.timeout_hint_seconds:
        report("timing", True)
    else:
        report("timing", False, "lower check.wait_seconds below check.timeout_hint_seconds")

    return 0 if problems == 0 else 1


def _repo_check(repo: Path) -> tuple[bool, str] | None:
    """Check that repo exists and is a git work tree; return (ok, problem), or None when git is missing."""
    if not repo.is_dir():
        return False, f"create the repo folder {repo}"
    try:
        result = run_git(repo, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return None
    if result.returncode == 0 and result.stdout.strip() == "true":
        return True, ""
    return False, f"run git init in {repo}"


def _branch_check(repo: Path, wanted: str) -> tuple[bool, str]:
    """Check that repo sits on the branch named by the run; return (ok, problem)."""
    try:
        result = run_git(repo, "branch", "--show-current")
    except FileNotFoundError:
        return False, "install git or put it on PATH"
    actual = result.stdout.strip() if result.returncode == 0 else ""
    if actual == wanted:
        return True, ""
    return False, f"repo is on branch {actual}, this run needs {wanted}"


def _program_checks(config: RunConfig) -> list[tuple[str, str]]:
    """Pairs of (check name, program word) doctor must find on PATH."""
    checks: list[tuple[str, str]] = []
    if config.test:
        checks.append(("test command", config.test[0]))
    if config.lint:
        checks.append(("lint command", config.lint[0]))
    for index, command in enumerate(config.pre):
        if command:
            checks.append((f"pre check {index + 1}", command[0]))
    if config.launch:
        checks.append(("agent launch", config.launch[0]))
    if config.status:
        checks.append(("agent status", config.status[0]))
    return checks


def _report(run_dir: Path, out: Path | None = None) -> int:
    """Print the markdown summary of a run, or write it to out when given."""
    try:
        text = build_report(run_dir)
    except (ConfigError, ItemsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if out is not None:
        try:
            out.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"error: could not write {out}: {exc}", file=sys.stderr)
            return 1
        return 0
    sys.stdout.write(text)
    return 0


def _commits(run_dir: Path, base: str | None = None) -> int:
    """Report commits that break the one-commit-per-item rule; exit 1 when any are found."""
    try:
        config = load_config(run_dir)
        items = load_items(run_dir)
    except (ConfigError, ItemsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if shutil.which("git") is None:
        print("note: skipped because git is not on PATH")
        return 0
    try:
        lines = commit_report(config, items, base=base)
    except CommitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0 if not lines else 1


def _gate(args: argparse.Namespace) -> int:
    """Run one gate subcommand against an existing run directory."""
    if args.gate_command == "clean":
        return clean(args.run_dir)
    try:
        config = load_config(args.run_dir)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.gate_command == "start":
        return start(config, args.run_dir)
    if args.gate_command == "wait":
        return wait(config, args.run_dir, args.n)
    return file(config, args.run_dir, args.test_file, args.module)


def _print_supervisor_events(run_dir: Path) -> None:
    """Print the last five supervisor events as '  HH:MM:SS <msg>', or nothing when there are none."""
    events = read_supervisor_events(run_dir)[-5:]
    if not events:
        return
    print("Supervisor:")
    for event in events:
        time_part = event["t"].rsplit("T", 1)[-1]
        print(f"  {time_part} {event['msg']}")


def _status(run_dir: Path, as_json: bool = False) -> int:
    """Print a one-screen summary of the run and exit 0."""
    try:
        config = load_config(run_dir)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    progress = read_progress(progress_path, config.prefix)
    if as_json:
        payload = _status_payload(run_dir, config, progress, progress_path)
        print(json.dumps(payload))
        return 0

    print(f"{config.name}: {len(progress.done)}/{progress.total} done")
    if progress.blocked:
        print("blocked: " + ", ".join(f"{config.prefix}{n}" for n in progress.blocked))
    else:
        print("blocked: none")

    if progress.next_item is None:
        print("next: none")
    else:
        title = _next_title(progress_path, config.prefix, progress.next_item)
        suffix = f" {title}" if title else ""
        print(f"next: {config.prefix}{progress.next_item}{suffix}")

    seconds = current_item_seconds(run_dir, config, progress)
    if progress.next_item is not None and seconds is not None:
        print(f"on {config.prefix}{progress.next_item} for {format_minutes(seconds)}")

    print(last_gate_line(run_dir))
    _print_commits(config.repo)
    _print_supervisor_events(run_dir)
    return 0


def _watch(run_dir: Path, every: float = 20.0, once: bool = False) -> int:
    """Load config and print new commits and supervisor events until the run is finished."""
    try:
        config = load_config(run_dir)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return watch(config, run_dir, every=every, once=once)


def _next_title(progress_path: Path, prefix: str, number: int) -> str | None:
    """Return the title of one item line from the progress file."""
    pattern = re.compile(rf"^- \[( |x|X)\] {re.escape(prefix)}{number}\. (.+)$")
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(2).strip()
    return None


def _status_payload(
    run_dir: Path, config: RunConfig, progress: Progress, progress_path: Path
) -> dict[str, object]:
    """Build the JSON summary of one run; every key is always present."""
    next_item = progress.next_item
    if next_item is None:
        next_title: str | None = None
    else:
        next_title = _next_title(progress_path, config.prefix, next_item)
    seconds = current_item_seconds(run_dir, config, progress)
    return {
        "name": config.name,
        "total": progress.total,
        "done": list(progress.done),
        "blocked": list(progress.blocked),
        "next_item": next_item,
        "next_title": next_title,
        # whole minutes on the current item, same rounding as report.format_minutes
        "current_minutes": None if seconds is None else (seconds + 30) // 60,
        "gate": state(run_dir),
        "commits": _commit_dicts(config.repo) or [],
        "events": read_supervisor_events(run_dir)[-5:],
    }


def _commit_dicts(repo: Path) -> list[dict[str, str]] | None:
    """Return the last five commits of repo as {hash, subject}, or None when git is unavailable."""
    try:
        result = run_git(repo, "log", "-5", "--format=%h %s")
    except FileNotFoundError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return []
    commits = []
    for line in result.stdout.rstrip("\n").splitlines():
        commit_hash, _, subject = line.partition(" ")
        commits.append({"hash": commit_hash, "subject": subject})
    return commits


def _print_commits(repo: Path) -> None:
    """Print the last five commit subjects of repo, or a note when that is impossible."""
    commits = _commit_dicts(repo)
    if commits is None:
        print("commits: skipped (git not on PATH)")
        return
    if not commits:
        print("commits: none")
        return
    for commit in commits:
        print(f"{commit['hash']} {commit['subject']}")
