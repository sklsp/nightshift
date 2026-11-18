"""Tests for the commit format check."""

import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.cli import main
from nightshift.commits import commit_report, resolve_base
from nightshift.config import RunConfig, load_config
from nightshift.items import Item, load_items

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(repo: Path, *args: str) -> str:
    """Run one git command in repo and return its stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, subject: str, body: str | None = "5 passed in 0.1s") -> str:
    """Make one empty commit and return its full hash."""
    if body is None:
        _git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    else:
        _git(repo, "commit", "-q", "--allow-empty", "-m", subject, "-m", body)
    return _git(repo, "rev-parse", "HEAD")


def _file_commit(repo: Path, rel_path: str, content: str, subject: str) -> str:
    """Write one file in repo and commit it; return its full hash."""
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", rel_path)
    _git(repo, "commit", "-q", "-m", subject, "-m", "5 passed in 0.1s")
    return _git(repo, "rev-parse", "HEAD")


def _make_run(
    tmp_path: Path, template: Path, config_branch: str = "main", protect: list[str] | None = None
) -> tuple[Path, Path]:
    """Create a git repo on branch main with one root commit plus a run dir."""
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    shutil.copytree(template, repo)
    run_dir.mkdir()
    _commit(repo, "root: initial import")
    protect_line = ""
    if protect is not None:
        values = ", ".join(f'"{value}"' for value in protect)
        protect_line = f"protect = [{values}]\n"
    toml_text = (
        f'[run]\nname = "c"\nrepo = "../repo"\nbranch = "{config_branch}"\nprefix = "T"\n{protect_line}\n'
        '[check]\ntest = ["pytest"]\n'
    )
    (run_dir / "run.toml").write_text(toml_text, encoding="utf-8")
    (run_dir / "items.md").write_text(
        "# Items\n\n## one\nDo the first thing.\n\n## two\nDo the second thing.\n\n"
        "## three\nDo the third thing.\n",
        encoding="utf-8",
    )
    return repo, run_dir


def _config_and_items(run_dir: Path) -> tuple[RunConfig, list[Item]]:
    """Load the config and items of one run dir."""
    return load_config(run_dir), load_items(run_dir)


def test_clean_run(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == []
    assert main(["commits", str(run_dir), "--base", root]) == 0


def test_missing_prefix_is_reported(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    bad = _commit(repo, "wip: half done")
    config, items = _config_and_items(run_dir)
    lines = commit_report(config, items, base=root)
    assert lines == [f"{bad[:7]} bad subject: 'wip: half done' (expected 'T<n>: ...')"]
    assert main(["commits", str(run_dir), "--base", root]) == 1


def test_split_item_is_reported(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    first = _commit(repo, "T1: item one")
    second = _commit(repo, "T1: fix the tick")
    _commit(repo, "T2: item two")
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    lines = commit_report(config, items, base=root)
    assert lines == [f"T1 has 2 commits ({second[:7]}, {first[:7]})"]


def test_missing_item_is_reported(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _commit(repo, "T1: item one")
    _commit(repo, "T2: item two")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == ["T3 has no commit"]


def test_empty_body_is_reported(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _commit(repo, "T1: item one")
    bare = _commit(repo, "T2: item two", body=None)
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == [f"{bare[:7]} has an empty body"]


def test_default_base_uses_merge_base(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _git(repo, "checkout", "-q", "-b", "work")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    config, items = _config_and_items(run_dir)
    assert resolve_base(config) == root
    assert commit_report(config, items) == []


def test_default_base_falls_back_to_first_commit(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template, config_branch="nope")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    config, items = _config_and_items(run_dir)
    assert resolve_base(config) == root
    # the range starts after the root commit, so all item commits are checked and clean
    assert commit_report(config, items) == []


def test_bad_base_is_an_error(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    assert main(["commits", str(run_dir), "--base", "no-such-ref"]) == 1


def test_protected_file_is_reported(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template, protect=[".github/"])
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    bad = _file_commit(repo, ".github/ci.yml", "jobs: {}\n", "T1: item one")
    _commit(repo, "T2: item two")
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == [f"{bad[:7]} touches protected .github/ci.yml"]


def test_unprotected_files_are_not_reported(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template, protect=[".github/"])
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _file_commit(repo, "src/app.py", "x = 1\n", "T1: item one")
    _commit(repo, "T2: item two")
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == []


def test_empty_protect_reports_nothing_new(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _file_commit(repo, ".github/ci.yml", "jobs: {}\n", "T1: item one")
    _commit(repo, "T2: item two")
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == []


def test_protected_lines_follow_commit_order(tmp_path: Path, git_template: Path) -> None:
    repo, run_dir = _make_run(tmp_path, git_template, protect=[".github/"])
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    first = _file_commit(repo, ".github/ci.yml", "a\n", "T1: item one")
    second = _file_commit(repo, ".github/workflows/build.yml", "b\n", "T2: item two")
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == [
        f"{second[:7]} touches protected .github/workflows/build.yml",
        f"{first[:7]} touches protected .github/ci.yml",
    ]
