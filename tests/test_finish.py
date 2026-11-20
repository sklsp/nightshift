"""Tests for dropping the progress file once a run is over."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift.config import RunConfig
from nightshift.finish import run_finish
from nightshift.supervise import _LOCK_NAME, supervisor_lock_held

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_TICKED = "- [x] T1. first item (1 passed in 0.01s)\n- [x] T2. second item (2 passed in 0.01s)\n"
_PARTIAL = "- [x] T1. first item (1 passed in 0.01s)\n- [ ] T2. second item\n"


def _git(repo: Path, *args: str) -> str:
    """Run one git command in repo and return its stripped stdout."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _make_repo(tmp_path: Path, template: Path, progress_text: str | None = _TICKED) -> Path:
    """Create a repo on main with one tracked file and, when given, a committed progress file."""
    repo = tmp_path / "repo"
    shutil.copytree(template, repo)
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    if progress_text is not None:
        (repo / "PROGRESS-fin.md").write_text(progress_text, encoding="utf-8")
    _git(repo, "add", "file.txt")
    if progress_text is not None:
        _git(repo, "add", "PROGRESS-fin.md")
    _git(repo, "commit", "-q", "-m", "root: initial import")
    return repo


def _config(tmp_path: Path, branch: str = "") -> RunConfig:
    """Build a config for the temp repo named fin."""
    return RunConfig(name="fin", repo=tmp_path / "repo", test=["true"], branch=branch)


def _dead_pid() -> int:
    """Return the pid of a process that has already exited."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_supervisor_lock_held_true_for_a_live_pid(tmp_path: Path) -> None:
    (tmp_path / _LOCK_NAME).write_text(json.dumps({"pid": os.getpid(), "run": "/run"}), encoding="utf-8")
    assert supervisor_lock_held(tmp_path, git_dir=lambda repo: tmp_path) is True


def test_supervisor_lock_held_false_for_a_dead_pid(tmp_path: Path) -> None:
    (tmp_path / _LOCK_NAME).write_text(json.dumps({"pid": _dead_pid(), "run": "/run"}), encoding="utf-8")
    assert supervisor_lock_held(tmp_path, git_dir=lambda repo: tmp_path) is False


def test_supervisor_lock_held_false_for_an_unreadable_lock(tmp_path: Path) -> None:
    (tmp_path / _LOCK_NAME).write_text("not json", encoding="utf-8")
    assert supervisor_lock_held(tmp_path, git_dir=lambda repo: tmp_path) is False


def test_supervisor_lock_held_false_without_a_git_dir(tmp_path: Path) -> None:
    assert supervisor_lock_held(tmp_path) is False


def test_finish_drops_the_file_and_commits(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template)
    before = _git(repo, "rev-parse", "HEAD")

    assert run_finish(_config(tmp_path)) == 0
    out = capsys.readouterr().out.strip()

    assert not (repo / "PROGRESS-fin.md").exists()
    after = _git(repo, "rev-parse", "HEAD")
    assert after != before
    assert _git(repo, "rev-list", "--count", f"{before}..{after}") == "1"
    assert out == after
    assert _git(repo, "log", "-1", "--format=%s") == "chore: drop the fin progress file"


def test_finish_accepts_the_run_branch_when_it_matches(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template)
    _git(repo, "checkout", "-q", "-b", "nightshift-27")

    assert run_finish(_config(tmp_path, branch="nightshift-27")) == 0
    capsys.readouterr()
    assert not (repo / "PROGRESS-fin.md").exists()


def test_finish_refuses_while_a_live_supervisor_holds_the_lock(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template)
    (repo / ".git" / _LOCK_NAME).write_text(json.dumps({"pid": os.getpid(), "run": "/run"}), encoding="utf-8")

    assert run_finish(_config(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "a live supervisor holds the repo lock; refusing to finish" in err
    assert (repo / "PROGRESS-fin.md").is_file()


def test_finish_ignores_a_stale_supervisor_lock(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    (repo / ".git" / _LOCK_NAME).write_text(json.dumps({"pid": _dead_pid(), "run": "/run"}), encoding="utf-8")

    assert run_finish(_config(tmp_path)) == 0
    assert not (repo / "PROGRESS-fin.md").exists()


def test_finish_refuses_when_an_item_is_not_ticked(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template, progress_text=_PARTIAL)

    assert run_finish(_config(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "not every item is ticked or blocked (T2); refusing to finish" in err
    assert (repo / "PROGRESS-fin.md").is_file()


def test_finish_refuses_with_uncommitted_changes_to_tracked_files(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template)
    (repo / "file.txt").write_text("one\ntwo\n", encoding="utf-8")

    assert run_finish(_config(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "the repo has uncommitted changes to tracked files; refusing to finish" in err
    assert (repo / "PROGRESS-fin.md").is_file()


def test_untracked_files_do_not_block_finish(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    (repo / "stray.txt").write_text("x\n", encoding="utf-8")

    assert run_finish(_config(tmp_path)) == 0
    assert not (repo / "PROGRESS-fin.md").exists()


def test_finish_refuses_on_the_wrong_branch(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template)

    assert run_finish(_config(tmp_path, branch="nightshift-27")) == 1
    err = capsys.readouterr().err
    assert "repo is on branch main, this run needs nightshift-27; refusing to finish" in err
    assert (repo / "PROGRESS-fin.md").is_file()


def test_finish_when_the_file_is_already_gone(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, git_template, progress_text=None)
    before = _git(repo, "rev-parse", "HEAD")

    assert run_finish(_config(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "PROGRESS-fin.md already gone; nothing to do" in out
    assert _git(repo, "rev-parse", "HEAD") == before
