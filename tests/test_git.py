"""Tests for the git helpers."""

import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.git import commit_log, current_branch, fingerprint, git_dir

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(repo: Path, *args: str) -> str:
    """Run one git command in repo and return its stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _make_repo(tmp_path: Path, template: Path) -> Path:
    """Create a git repo on branch main with one root commit and one tracked file."""
    repo = tmp_path / "repo"
    shutil.copytree(template, repo)
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "root: initial import")
    return repo


def test_current_branch_reports_a_fresh_branch(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    _git(repo, "checkout", "-q", "-b", "feature")
    assert current_branch(repo) == "feature"


def test_current_branch_is_empty_outside_a_repo(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    assert current_branch(folder) == ""


def test_git_dir_is_absolute(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    result = git_dir(repo)
    assert result is not None
    assert result.is_absolute()
    assert result == repo / ".git"


def test_fingerprint_changes_after_edit_and_commit(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    head = _git(repo, "rev-parse", "HEAD")
    before = fingerprint(repo)
    assert before == f"{head}\n"  # clean tree: HEAD plus empty status
    (repo / "file.txt").write_text("one\ntwo\n", encoding="utf-8")
    after_edit = fingerprint(repo)
    assert after_edit is not None and after_edit != before
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "M1: add a line")
    new_head = _git(repo, "rev-parse", "HEAD")
    after_commit = fingerprint(repo)
    assert after_commit == f"{new_head}\n"


def test_commit_log_keeps_a_commit_with_an_empty_subject(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    _git(repo, "commit", "-q", "--allow-empty", "--allow-empty-message", "-m", "")
    log = commit_log(repo)
    assert [subject for _, _, subject in log] == ["", "root: initial import"]
