"""Tests for the nightshift review command."""

import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.cli import main


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _short_hash(repo: Path, ref: str) -> str:
    """Return the short hash of one revision in repo."""
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", ref], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _run_dir(tmp_path: Path, items_md: str, protect: list[str] | None = None) -> tuple[Path, Path]:
    """Create a fresh git repo on main and a run dir pointing at it; return (repo, run_dir)."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "review@example.com")
    _git(repo, "config", "user.name", "Review Test")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    protect_line = ""
    if protect is not None:
        values = ", ".join(f'"{value}"' for value in protect)
        protect_line = f"protect = [{values}]\n"
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "rev"\nrepo = "{repo.as_posix()}"\nprefix = "Q"\n{protect_line}[check]\ntest = []\n',
        encoding="utf-8",
    )
    (run_dir / "items.md").write_text(items_md, encoding="utf-8")
    return repo, run_dir


def _commit(repo: Path, files: dict[str, str], subject: str) -> None:
    """Write the given files into repo and commit them with the given subject."""
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", *files)
    _git(repo, "commit", "-q", "-m", subject, "-m", "1 passed in 0.1s")


_ONE_ITEM = "# Items\n\n## first item\nDo the first thing.\n"

_TWO_ITEMS = (
    "# Items\n\n## first item\nDo the first thing.\n\n## second item\nDo the second thing.\n"
)


def test_review_prints_the_exact_block(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _ONE_ITEM)
    _commit(repo, {"src/app.py": "x = 1\ny = 2\nz = 3\n"}, "root: initial import")
    _commit(
        repo,
        {
            "src/app.py": "x = 1\ny = 2\nz = 3\nw = 4\nv = 5\n",
            "tests/test_app.py": "def test_one():\n    assert 1 == 1\n\n\ndef test_two():\n    assert 2 == 2\n",
        },
        "Q1: first item",
    )
    commit_hash = _short_hash(repo, "HEAD")

    code = main(["review", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        f"Q1 first item ({commit_hash})\n"
        "  files: 2 (+8 -0)  src/app.py, tests/test_app.py\n"
        "  tests: 0 -> 2\n"
    )


def test_review_warns_when_tests_shrink(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _ONE_ITEM)
    three_tests = "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n\n\ndef test_three():\n    pass\n"
    two_tests = "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
    _commit(repo, {"tests/test_app.py": three_tests}, "root: initial import")
    _commit(repo, {"tests/test_app.py": two_tests}, "Q1: first item")
    commit_hash = _short_hash(repo, "HEAD")

    code = main(["review", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        f"Q1 first item ({commit_hash})\n"
        "  files: 1 (+0 -4)  tests/test_app.py\n"
        "  tests: 3 -> 2\n"
        "  ! fewer tests in tests/test_app.py (3 -> 2)\n"
    )


def test_review_warns_on_protected_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _ONE_ITEM, protect=[".github/"])
    _commit(repo, {"src/app.py": "x = 1\n"}, "root: initial import")
    _commit(repo, {".github/ci.yml": "jobs: {}\n"}, "Q1: first item")
    commit_hash = _short_hash(repo, "HEAD")

    code = main(["review", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        f"Q1 first item ({commit_hash})\n"
        "  files: 1 (+1 -0)  .github/ci.yml\n"
        "  tests: 0 -> 0\n"
        "  ! touches protected .github/ci.yml\n"
    )


def test_review_warns_on_many_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _ONE_ITEM)
    names = [f"file{i:02d}.txt" for i in range(16)]
    _commit(repo, {"seed.txt": "start\n"}, "root: initial import")
    _commit(repo, {name: f"{i}\n" for i, name in enumerate(names)}, "Q1: first item")
    commit_hash = _short_hash(repo, "HEAD")

    code = main(["review", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        f"Q1 first item ({commit_hash})\n"
        f"  files: 16 (+16 -0)  {', '.join(names)}\n"
        "  tests: 0 -> 0\n"
        "  ! more than 15 files (16)\n"
    )


def test_review_says_no_commit_for_missing_items(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _TWO_ITEMS)
    _commit(repo, {"src/app.py": "x = 1\n"}, "root: initial import")
    _commit(repo, {"src/app.py": "x = 2\n"}, "Q1: first item")
    commit_hash = _short_hash(repo, "HEAD")

    code = main(["review", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        f"Q1 first item ({commit_hash})\n"
        "  files: 1 (+1 -1)  src/app.py\n"
        "  tests: 0 -> 0\n"
        "Q2 second item: no commit\n"
    )
