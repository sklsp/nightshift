"""Small helpers around the git calls nightshift makes."""

import subprocess
from pathlib import Path


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command inside repo and capture its output."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def current_branch(repo: Path) -> str:
    """Return the repo's current branch name, or an empty string when git cannot tell."""
    try:
        result = run_git(repo, "branch", "--show-current")
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_dir(repo: Path) -> Path | None:
    """Return the repo's git directory, or None when git cannot answer."""
    try:
        result = run_git(repo, "rev-parse", "--git-dir")
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = repo / path
    return path


def fingerprint(repo: Path) -> str | None:
    """Return HEAD's hash plus git status porcelain text, or None when git cannot answer."""
    try:
        head = run_git(repo, "rev-parse", "HEAD")
        status = run_git(repo, "status", "--porcelain")
    except OSError:
        return None
    if head.returncode != 0 or status.returncode != 0:
        return None
    return f"{head.stdout.strip()}\n{status.stdout}"


def commit_log(repo: Path) -> list[tuple[str, int, str]]:
    """Return (hash, unix time, subject) for every commit of repo, newest first; empty when git is missing."""
    try:
        result = run_git(repo, "log", "--format=%h %ct %s")
    except OSError:
        return []
    if result.returncode != 0:
        return []
    lines: list[tuple[str, int, str]] = []
    for line in result.stdout.splitlines():
        short_hash, _, rest = line.partition(" ")
        unix_time, _, subject = rest.partition(" ")  # partition: an empty subject is allowed
        lines.append((short_hash, int(unix_time), subject))
    return lines
