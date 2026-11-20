"""Drop the progress file once a run is over."""

import sys
from pathlib import Path

from nightshift.config import RunConfig
from nightshift.git import current_branch, run_git
from nightshift.progress import read_progress
from nightshift.supervise import supervisor_lock_held


def progress_path(config: RunConfig) -> Path:
    """Return the repo path of this run's progress file."""
    return config.repo / f"PROGRESS-{config.name}.md"


def run_finish(config: RunConfig) -> int:
    """Drop PROGRESS-<name>.md once every item is done or blocked; refuse while the run is still live.

    The refusals, in order: a live supervisor holds the repo lock, an item is not ticked or blocked,
    tracked files have uncommitted changes, and (when branch is set) the repo is on another branch.
    A missing progress file means finish already ran; that prints a note and exits 0 without a commit.
    """
    path = progress_path(config)
    if not path.is_file():
        print(f"{path.name} already gone; nothing to do")
        return 0
    if supervisor_lock_held(config.repo):
        print("a live supervisor holds the repo lock; refusing to finish", file=sys.stderr)
        return 1
    progress = read_progress(path, config.prefix)
    if not progress.finished:
        resolved = set(progress.done) | set(progress.blocked)
        open_items = [f"{config.prefix}{number}" for number in range(1, progress.total + 1) if number not in resolved]
        detail = ", ".join(open_items) if open_items else "no items are listed"
        print(f"not every item is ticked or blocked ({detail}); refusing to finish", file=sys.stderr)
        return 1
    status = run_git(config.repo, "--no-optional-locks", "status", "--porcelain")
    if status.returncode != 0:
        print(f"git status failed: {status.stderr.strip()}", file=sys.stderr)
        return 1
    dirty = [line for line in status.stdout.splitlines() if line[:2] != "??"]
    if dirty:
        print("the repo has uncommitted changes to tracked files; refusing to finish", file=sys.stderr)
        return 1
    if config.branch:
        actual = current_branch(config.repo)
        if actual != config.branch:
            print(f"repo is on branch {actual}, this run needs {config.branch}; refusing to finish", file=sys.stderr)
            return 1
    removed = run_git(config.repo, "rm", "-q", path.name)
    if removed.returncode != 0:
        print(f"git rm failed: {removed.stderr.strip()}", file=sys.stderr)
        return 1
    committed = run_git(config.repo, "commit", "-m", f"chore: drop the {config.name} progress file")
    if committed.returncode != 0:
        print(f"git commit failed: {committed.stderr.strip()}", file=sys.stderr)
        return 1
    head = run_git(config.repo, "rev-parse", "HEAD")
    print(head.stdout.strip())
    return 0
