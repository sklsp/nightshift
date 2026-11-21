"""Tests for the git helpers."""

import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.git import commit_log, current_branch, fingerprint, git_dir, run_git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(repo: Path, *args: str) -> str:
    """Run one git command in repo and return its stripped stdout."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
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


def test_a_git_that_hangs_counts_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def hang(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    result = run_git(tmp_path, "status", timeout=0.5)
    assert result.returncode == 124
    assert result.stderr == "git timed out after 0.5 s"
    assert fingerprint(tmp_path) is None


def test_run_git_returns_text_for_bytes_the_locale_cannot_decode(tmp_path: Path, git_template: Path) -> None:
    repo = _make_repo(tmp_path, git_template)
    # a Dutch word plus an emoji written as UTF-8, with U+0081 (C2 81 in UTF-8): byte 0x81 does not exist in
    # cp1252, so a locale that decodes git output with cp1252 raises instead of returning text
    content = "sneeuw \U0001F600\u0081\n"
    (repo / "utf8.txt").write_bytes(content.encode("utf-8"))
    _git(repo, "add", "utf8.txt")
    _git(repo, "commit", "-q", "-m", "M2: add a file with utf-8 bytes cp1252 cannot decode")

    result = run_git(repo, "show", "HEAD:utf8.txt")

    assert result.returncode == 0
    assert isinstance(result.stdout, str)
    assert result.stdout == content


def test_review_counts_tests_in_a_file_with_bytes_the_locale_cannot_decode(
    tmp_path: Path, git_template: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from nightshift.cli import main

    repo = _make_repo(tmp_path, git_template)
    # the test file holds U+0081 (C2 81 in UTF-8), a byte cp1252 cannot decode; review's counting reads it
    # back with `git show` and must count its tests instead of raising
    content = "sneeuw \U0001F600\u0081\n\ndef test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
    (repo / "tests").mkdir()
    (repo / "tests" / "test_utf8.py").write_bytes(content.encode("utf-8"))
    _git(repo, "add", "tests/test_utf8.py")
    _git(repo, "commit", "-q", "-m", "Q1: first item", "-m", "2 passed in 0.1s")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "utf8"\nrepo = "{repo.as_posix()}"\nprefix = "Q"\n[check]\ntest = []\n', encoding="utf-8"
    )
    (run_dir / "items.md").write_text("# Items\n\n## first item\nDo the thing.\n", encoding="utf-8")

    code = main(["review", str(run_dir)])

    out = capsys.readouterr().out
    commit_hash = _git(repo, "rev-parse", "--short", "HEAD")
    assert code == 0
    assert out == (
        f"Q1 first item ({commit_hash})\n"
        "  files: 1 (+8 -0)  tests/test_utf8.py\n"
        "  tests: 0 -> 2\n"
    )
