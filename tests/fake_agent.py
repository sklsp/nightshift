"""A fake coding agent for tests that does real work in a git repo.

Two modes, chosen by argparse:

    launch <repo> <prefix> <progress file name>
        Starts this script again in work mode as a detached background process and
        reports running at once, like `cline -z`. Exits 0 immediately.

    work <repo> <prefix> <progress file name>
        For every unticked item line of the progress file, in order: appends the
        item number to work.txt, ticks the line as done with a passing test count,
        and commits both files. Writes done when finished. Blocked lines are left
        for a human.

A supervisor polls it with the status command
`[sys.executable, "-c", "print(open(r'<repo>/.fake-agent-status').read())"]`
and running_pattern `running`. The agent never touches anything outside the repo.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from nightshift.gate import _detach_kwargs

_STATUS_NAME = ".fake-agent-status"
_WORK_NAME = "work.txt"


def _status_path(repo: Path) -> Path:
    """The status file a supervisor reads to see whether the agent is running."""
    return repo / _STATUS_NAME


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and raise when it fails."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")


def _unticked_items(progress_path: Path, prefix: str) -> list[tuple[int, str]]:
    """Return (number, title) for every unticked item line of this run, in file order."""
    pattern = re.compile(rf"^- \[ \] {re.escape(prefix)}(\d+)\. (.+)$")
    items: list[tuple[int, str]] = []
    for line in progress_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        title = match.group(2)
        if title.endswith(" BLOCKED"):
            continue  # a blocked item waits for a human; the agent does not tick it
        items.append((int(match.group(1)), title))
    return items


def _tick(progress_path: Path, prefix: str, number: int, title: str) -> None:
    """Tick one unticked item line in place as done with a passing test count."""
    lines = progress_path.read_text(encoding="utf-8").splitlines()
    old = f"- [ ] {prefix}{number}. {title}"
    new = f"- [x] {prefix}{number}. {title} (1 passed)"
    for index, line in enumerate(lines):
        if line.strip() == old:
            lines[index] = new
            break
    else:
        raise RuntimeError(f"unticked item not found: {old}")
    progress_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def launch(repo: Path, prefix: str, progress_name: str) -> int:
    """Start a detached worker for this repo and report running at once."""
    script = Path(__file__).resolve()
    subprocess.Popen(
        [sys.executable, str(script), "work", str(repo), prefix, progress_name],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_detach_kwargs(),
    )
    _status_path(repo).write_text("running", encoding="utf-8")
    return 0


def work(repo: Path, prefix: str, progress_name: str) -> int:
    """Tick and commit every unticked item of the progress file, then report done."""
    _status_path(repo).write_text("running", encoding="utf-8")
    progress_path = repo / progress_name
    work_path = repo / _WORK_NAME
    items = _unticked_items(progress_path, prefix)
    for index, (number, title) in enumerate(items):
        with work_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{number}\n")
        _tick(progress_path, prefix, number, title)
        _git(repo, "add", "--", _WORK_NAME, progress_name)
        _git(repo, "commit", "-m", f"{prefix}{number}: {title}")
        if index + 1 < len(items):
            time.sleep(0.2)
    _status_path(repo).write_text("done", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry point for the fake agent."""
    parser = argparse.ArgumentParser(prog="fake_agent", description="A fake coding agent that ticks and commits items.")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for name, help_text in (
        ("launch", "start a detached worker and report running at once"),
        ("work", "tick and commit every unticked item, then report done"),
    ):
        mode_parser = subparsers.add_parser(name, help=help_text)
        mode_parser.add_argument("repo", type=Path)
        mode_parser.add_argument("prefix")
        mode_parser.add_argument("progress_name")
    args = parser.parse_args(argv)
    if args.mode == "launch":
        return launch(args.repo, args.prefix, args.progress_name)
    return work(args.repo, args.prefix, args.progress_name)


if __name__ == "__main__":
    raise SystemExit(main())
