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


def _make_run(tmp_path: Path, config_branch: str = "main") -> tuple[Path, Path]:
    """Create a git repo on branch main with one root commit plus a run dir."""
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    repo.mkdir()
    run_dir.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "night@test")
    _git(repo, "config", "user.name", "Night")
    _commit(repo, "root: initial import")
    toml_text = (
        f'[run]\nname = "c"\nrepo = "../repo"\nbranch = "{config_branch}"\nprefix = "T"\n\n'
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


def test_clean_run(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == []
    assert main(["commits", str(run_dir), "--base", root]) == 0


def test_missing_prefix_is_reported(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    bad = _commit(repo, "wip: half done")
    config, items = _config_and_items(run_dir)
    lines = commit_report(config, items, base=root)
    assert lines == [f"{bad[:7]} bad subject: 'wip: half done' (expected 'T<n>: ...')"]
    assert main(["commits", str(run_dir), "--base", root]) == 1


def test_split_item_is_reported(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    first = _commit(repo, "T1: item one")
    second = _commit(repo, "T1: fix the tick")
    _commit(repo, "T2: item two")
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    lines = commit_report(config, items, base=root)
    assert lines == [f"T1 has 2 commits ({second[:7]}, {first[:7]})"]


def test_missing_item_is_reported(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _commit(repo, "T1: item one")
    _commit(repo, "T2: item two")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == ["T3 has no commit"]


def test_empty_body_is_reported(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _commit(repo, "T1: item one")
    bare = _commit(repo, "T2: item two", body=None)
    _commit(repo, "T3: item three")
    config, items = _config_and_items(run_dir)
    assert commit_report(config, items, base=root) == [f"{bare[:7]} has an empty body"]


def test_default_base_uses_merge_base(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _git(repo, "checkout", "-q", "-b", "work")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    config, items = _config_and_items(run_dir)
    assert resolve_base(config) == root
    assert commit_report(config, items) == []


def test_default_base_falls_back_to_first_commit(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path, config_branch="nope")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    for number in (1, 2, 3):
        _commit(repo, f"T{number}: item {number}")
    config, items = _config_and_items(run_dir)
    assert resolve_base(config) == root
    # the range starts after the root commit, so all item commits are checked and clean
    assert commit_report(config, items) == []


def test_bad_base_is_an_error(tmp_path: Path) -> None:
    repo, run_dir = _make_run(tmp_path)
    assert main(["commits", str(run_dir), "--base", "no-such-ref"]) == 1
