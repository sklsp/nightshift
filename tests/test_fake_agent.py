"""Tests for tests/fake_agent.py, the fake agent that does real work in a git repo."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

_PROGRESS = "- [ ] T1. first item\n- [ ] T2. second item\n- [ ] T3. third item BLOCKED\n"


def _repo(git_template: Path, tmp_path: Path) -> Path:
    """A fresh git repo copied from the session template with an unticked progress file."""
    repo = tmp_path / "repo"
    shutil.copytree(git_template, repo)
    (repo / "PROGRESS-fake.md").write_text(_PROGRESS, encoding="utf-8")
    return repo


def _run_agent(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the fake agent script once in the foreground."""
    return subprocess.run(
        [sys.executable, str(FAKE_AGENT), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
    )


def _status_command(repo: Path) -> list[str]:
    """The exact status command a supervisor uses for this fake agent."""
    return [sys.executable, "-c", f"print(open(r'{(repo / '.fake-agent-status').as_posix()}').read())"]


def _wait_for_status(repo: Path, expected: str, timeout: float = 5.0) -> None:
    """Poll the status file until it holds expected or fail."""
    deadline = time.monotonic() + timeout
    path = repo / ".fake-agent-status"
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text(encoding="utf-8") == expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"status never became {expected!r}")


def _git_out(repo: Path, *args: str) -> str:
    """Run one git command in repo and return its stdout."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


def test_work_ticks_and_commits_items_in_order_and_leaves_blocked_unticked(git_template: Path, tmp_path: Path) -> None:
    repo = _repo(git_template, tmp_path)

    started = time.monotonic()
    result = _run_agent("work", str(repo), "T", "PROGRESS-fake.md")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    # The two unticked items sleep 0.2 s between them; the run must take at least that long.
    assert elapsed >= 0.2

    lines = (repo / "PROGRESS-fake.md").read_text(encoding="utf-8").splitlines()
    assert "- [x] T1. first item (1 passed)" in lines
    assert "- [x] T2. second item (1 passed)" in lines
    # The blocked item waits for a human; the agent leaves it exactly as it found it.
    assert "- [ ] T3. third item BLOCKED" in lines
    assert (repo / "work.txt").read_text(encoding="utf-8") == "1\n2\n"
    assert (repo / ".fake-agent-status").read_text(encoding="utf-8") == "done"

    subjects = _git_out(repo, "log", "--format=%s").splitlines()
    assert subjects == ["T2: second item", "T1: first item"]
    # Each commit holds both files; the messages above plus work.txt ending at "1\n2\n"
    # pin which number went into which commit.
    first_files = sorted(_git_out(repo, "show", "--format=", "--name-only", "HEAD~1").splitlines())
    assert first_files == ["PROGRESS-fake.md", "work.txt"]


def test_launch_reports_running_and_the_detached_worker_finishes(git_template: Path, tmp_path: Path) -> None:
    repo = _repo(git_template, tmp_path)
    (repo / "PROGRESS-fake.md").write_text("- [ ] T1. first item\n", encoding="utf-8")

    result = _run_agent("launch", str(repo), "T", "PROGRESS-fake.md")
    assert result.returncode == 0, result.stderr
    # launch exits at once; the status already says running and the worker is still to come.
    assert (repo / ".fake-agent-status").read_text(encoding="utf-8") == "running"

    _wait_for_status(repo, "done")
    lines = (repo / "PROGRESS-fake.md").read_text(encoding="utf-8").splitlines()
    assert "- [x] T1. first item (1 passed)" in lines
    subjects = _git_out(repo, "log", "--format=%s").splitlines()
    assert subjects == ["T1: first item"]
    # The supervisor's exact status command sees the finished state too.
    result = subprocess.run(_status_command(repo), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0
    assert result.stdout.strip() == "done"


def test_work_with_no_unticked_items_reports_done_without_committing(git_template: Path, tmp_path: Path) -> None:
    repo = _repo(git_template, tmp_path)
    (repo / "PROGRESS-fake.md").write_text("- [x] T1. first item (1 passed)\n", encoding="utf-8")

    result = _run_agent("work", str(repo), "T", "PROGRESS-fake.md")

    assert result.returncode == 0, result.stderr
    assert (repo / ".fake-agent-status").read_text(encoding="utf-8") == "done"
    assert not (repo / "work.txt").exists()
    assert _git_out(repo, "rev-list", "--count", "--all").strip() == "0"
