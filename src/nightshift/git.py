"""Small helpers around the git calls nightshift makes."""

import subprocess
from pathlib import Path

GIT_TIMEOUT_SECONDS = 60


def run_git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    """Run one git command inside repo and capture its output; a git that hangs counts as failed (code 124).

    Without the timeout one hanging `git status` (a pipe left open by another process, seen on Windows in the
    end-to-end test) would stop the supervisor for ever.
    """
    command = ["git", "-C", str(repo), *args]
    try:
        # git always prints UTF-8; the locale code page must not decide (cp1252 on Windows raised on file
        # contents and commit messages it cannot map)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"git timed out after {timeout} s")


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
        # --no-optional-locks: a poll must never take the index lock the agent's commit needs (that deadlock hung
        # the end-to-end test 3 times in 10 on Windows)
        status = run_git(repo, "--no-optional-locks", "status", "--porcelain")
    except OSError:
        return None
    if head.returncode != 0 or status.returncode != 0:
        return None
    return f"{head.stdout.strip()}\n{status.stdout}"


def commit_log(repo: Path, base: str | None = None, tip: str = "HEAD") -> list[tuple[str, int, str]]:
    """Return (hash, unix time, subject), newest first, of the commits reachable from tip (after base if given)."""
    try:
        if base is None:
            result = run_git(repo, "log", tip, "--format=%h %ct %s")
        else:
            result = run_git(repo, "log", f"{base}..{tip}", "--format=%h %ct %s")
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
