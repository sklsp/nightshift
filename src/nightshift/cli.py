"""Command line interface for nightshift."""

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from nightshift import __version__
from nightshift.agent import AgentError
from nightshift.config import ConfigError, RunConfig, load_config
from nightshift.gate import file, parse_summary, start, wait
from nightshift.items import ItemsError, load_items
from nightshift.progress import read_progress
from nightshift.render import RenderError, render_run
from nightshift.supervise import supervise

_EXAMPLE_RUN_TOML = """\
# nightshift run configuration. Every key is shown below; the required keys
# are name, repo and check.test, the rest fall back to their commented defaults.

[run]
name = "example"      # short id for PROGRESS-<name>.md and commit ids (letters, digits, dashes)
repo = ".."           # path to the repo root, relative to this run dir or absolute
# branch = "main"     # branch to work on; never pushed
# prefix = "T"        # item id prefix: T1, T2, ...

[check]
test = ["pytest", "-q"]  # test command run from the repo root
# lint = []               # optional lint command before the tests, e.g. ["ruff", "check"]
# min_passed = 1          # minimum passed tests for a green gate
# wait_seconds = 25       # how long one gate wait call may block
# timeout_hint_seconds = 30  # command limit the agent works under

[agent]
# launch = []                 # optional command to start the agent (supervisor)
# status = []                 # optional command that prints agent status lines
# running_pattern = "running"  # substring in status output meaning the agent is alive
# poll_seconds = 30           # supervisor poll interval
# max_sessions = 20           # restart limit before giving up
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

    status_parser = subparsers.add_parser("status", help="print a one-screen summary of the run")
    status_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")

    doctor_parser = subparsers.add_parser(
        "doctor", help="check that the run dir is ready for an unattended run"
    )
    doctor_parser.add_argument("run_dir", type=Path, help="path of an existing run directory")

    gate_parser = subparsers.add_parser("gate", help="run a check behind the detached gate")
    gate_sub = gate_parser.add_subparsers(dest="gate_command", required=True)
    gate_start = gate_sub.add_parser("start", help="run lint, then start the test command detached")
    gate_start.add_argument("run_dir", type=Path, help="path of an existing run directory")
    gate_wait = gate_sub.add_parser("wait", help="wait for the gated run and report pass or fail")
    gate_wait.add_argument("run_dir", type=Path, help="path of an existing run directory")
    gate_wait.add_argument("n", type=int, help="call number; use a new one on every call")
    gate_file = gate_sub.add_parser("file", help="start one test file with coverage of one module")
    gate_file.add_argument("run_dir", type=Path, help="path of an existing run directory")
    gate_file.add_argument("test_file", help="path to the test file, relative to the repo root")
    gate_file.add_argument("module", help="module name used for the coverage report line")

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
    if args.command == "status":
        return _status(args.run_dir)
    if args.command == "doctor":
        return _doctor(args.run_dir)
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


def _doctor(run_dir: Path) -> int:
    """Check that the run dir is ready for an unattended run; exit 1 on any problem."""
    problems = 0

    def report(ok: bool, problem: str = "") -> None:
        nonlocal problems
        if ok:
            print("ok")
        else:
            problems += 1
            print(f"problem: {problem}")

    config: RunConfig | None = None
    try:
        config = load_config(run_dir)
        report(True)
    except ConfigError as exc:
        report(False, f"fix run.toml ({exc})")
    if config is None:
        return 1

    try:
        items = load_items(run_dir)
        with TemporaryDirectory() as tmp:
            render_run(config, items, Path(tmp))
        report(True)
    except (ItemsError, RenderError) as exc:
        report(False, f"fix items.md ({exc})")

    _check_repo(report, config.repo)

    for program in _programs_to_check(config):
        if shutil.which(program) is not None or Path(program).is_file():
            report(True)
        else:
            report(False, f"install {program} or put it on PATH")

    if config.wait_seconds < config.timeout_hint_seconds:
        report(True)
    else:
        report(False, "lower check.wait_seconds below check.timeout_hint_seconds")

    return 0 if problems == 0 else 1


def _check_repo(report: Callable[[bool, str], None], repo: Path) -> None:
    """Report whether repo exists and is a git work tree; skip with a note when git is missing."""
    if not repo.is_dir():
        report(False, f"create the repo folder {repo}")
        return
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("note: skipped the git work tree check because git is not on PATH")
        return
    if result.returncode == 0 and result.stdout.strip() == "true":
        report(True)
    else:
        report(False, f"run git init in {repo}")


def _programs_to_check(config: RunConfig) -> list[str]:
    """Program words doctor must find: test first word, lint word 0, launch and status programs."""
    programs: list[str] = []
    if config.test:
        programs.append(config.test[0])
    if config.lint:
        programs.append(config.lint[0])
    if config.launch:
        programs.append(config.launch[0])
    if config.status:
        programs.append(config.status[0])
    return programs


def _gate(args: argparse.Namespace) -> int:
    """Run one gate subcommand against an existing run directory."""
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


def _status(run_dir: Path) -> int:
    """Print a one-screen summary of the run and exit 0."""
    try:
        config = load_config(run_dir)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    progress = read_progress(progress_path, config.prefix)
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

    print(_last_gate_line(run_dir))
    _print_commits(config.repo)
    return 0


def _next_title(progress_path: Path, prefix: str, number: int) -> str | None:
    """Return the title of one item line from the progress file."""
    pattern = re.compile(rf"^- \[( |x|X)\] {re.escape(prefix)}{number}\. (.+)$")
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(2).strip()
    return None


def _last_gate_line(run_dir: Path) -> str:
    """One line describing the last gated test run, if any."""
    log_path = run_dir / ".gate" / "log"
    done_path = run_dir / ".gate" / "done"
    if not log_path.is_file():
        return "last gate: none"
    if not done_path.is_file():
        return "last gate: running"
    code_text = done_path.read_text(encoding="utf-8", errors="replace").strip() or "?"
    summary = parse_summary(log_path.read_text(encoding="utf-8", errors="replace"))
    if summary.line is None:
        return f"last gate: exit {code_text} (no pytest summary line)"
    return f"last gate: exit {code_text}: {summary.line}"


def _print_commits(repo: Path) -> None:
    """Print the last five commit subjects of repo, or a note when that is impossible."""
    try:
        result = subprocess.run(
            ["git", "log", "-5", "--format=%h %s"],
            cwd=repo,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("commits: skipped (git not on PATH)")
        return
    if result.returncode != 0 or not result.stdout.strip():
        print("commits: none")
        return
    for line in result.stdout.rstrip("\n").splitlines():
        print(line)
