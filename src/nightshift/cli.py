"""Command line interface for nightshift."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from nightshift import __version__
from nightshift.agent import AgentError
from nightshift.config import ConfigError, load_config
from nightshift.gate import parse_summary
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

