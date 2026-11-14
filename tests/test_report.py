"""Tests for the nightshift report command."""

import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.cli import main


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _short_hash(repo: Path, ref: str) -> str:
    """Return the short hash of one revision in repo."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", ref], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _run_dir(tmp_path: Path, items_md: str, progress_md: str | None = None) -> tuple[Path, Path]:
    """Create a fresh git repo and a run dir pointing at it; return (repo, run_dir)."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "report@example.com")
    _git(repo, "config", "user.name", "Report Test")
    if progress_md is not None:
        (repo / "PROGRESS-rep.md").write_text(progress_md, encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "rep"\nrepo = "{repo.as_posix()}"\nprefix = "T"\n[check]\ntest = []\n',
        encoding="utf-8",
    )
    (run_dir / "items.md").write_text(items_md, encoding="utf-8")
    return repo, run_dir


_ITEMS_MD = """# Items for the rep run

## first item
Do the first thing.

## second item
Do the second thing.

## third item
Do the third thing.
"""

_PROGRESS_MD = (
    "# nightshift run rep progress\n"
    "\n"
    "- [x] T1. first item\n"
    "- [x] T2. second item\n"
    "- [ ] T3. third item BLOCKED\n"
)


def test_report_lists_items_with_commits_and_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(tmp_path, _ITEMS_MD, _PROGRESS_MD)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "one.txt")
    _git(repo, "commit", "-q", "-m", "T1: first item")
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "two.txt")
    _git(repo, "commit", "-q", "-m", "T2: second item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 2/3 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD~1')}\n"
        f"- T2 second item: {_short_hash(repo, 'HEAD')}\n"
        "- T3 third item: BLOCKED\n"
    )


def test_report_without_commits_says_not_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n\n## second item\nDo that.\n",
        "- [ ] T1. first item\n- [ ] T2. second item\n",
    )

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 0/2 items\n"
        "- T1 first item: not committed\n"
        "- T2 second item: not committed\n"
    )


def test_report_out_writes_file_instead_of_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    out_file = tmp_path / "report.md"

    code = main(["report", str(run_dir), "--out", str(out_file)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert (
        out_file.read_text(encoding="utf-8")
        == "# rep: 1/1 items\n- T1 first item: not committed\n"
    )


def test_report_includes_last_gate_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text("collected 3 items\n3 passed in 0.12s\n", encoding="utf-8")
    (gate_dir / "done").write_text("0\n", encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        "- T1 first item: not committed\n"
        "last gate: exit 0: 3 passed in 0.12s\n"
    )


def test_report_appends_bugs_and_ideas_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    (run_dir / "BUGS.md").write_text("# Bugs\n\ncrash on empty input\nslow gate wait\n", encoding="utf-8")
    (run_dir / "IDEAS.md").write_text("# Ideas\nAdd a watch mode.\n", encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        "- T1 first item: not committed\n"
        "\n"
        "## Bugs found\n"
        "crash on empty input\n"
        "slow gate wait\n"
        "\n"
        "## Ideas\n"
        "Add a watch mode.\n"
    )


def test_report_empty_notes_files_give_no_sections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    (run_dir / "BUGS.md").write_text("", encoding="utf-8")
    (run_dir / "IDEAS.md").write_text("# Ideas\n\n", encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == "# rep: 1/1 items\n- T1 first item: not committed\n"
