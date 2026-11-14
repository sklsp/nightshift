"""Tests for the nightshift doctor command."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift.cli import main


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _test_words() -> list[str]:
    """A test command built from the running interpreter so doctor always finds it."""
    return [Path(sys.executable).as_posix(), "-m", "pytest", "-q"]


def _healthy_run(tmp_path: Path) -> Path:
    """Create a run dir with valid config and items pointing at a fresh git repo."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "doc"\nrepo = "{repo.as_posix()}"\n[check]\n'
        f"test = {repr(_test_words())}\nwait_seconds = 25\ntimeout_hint_seconds = 30\n",
        encoding="utf-8",
    )
    (run_dir / "items.md").write_text(
        "# Items for the doc run\n\n## one item\nDo the thing.\n", encoding="utf-8"
    )
    return run_dir


def _rewrite_toml(run_dir: Path, old: str, new: str) -> None:
    """Replace one line of the run's run.toml."""
    path = run_dir / "run.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_doctor_healthy_run_is_all_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == ["ok"] * 5


def test_doctor_missing_test_program_reports_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, repr(_test_words()), repr(["no-such-program-xyz"]))

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert any(line.startswith("problem:") and "no-such-program-xyz" in line for line in lines)


def test_doctor_wait_seconds_not_below_timeout_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, "wait_seconds = 25", "wait_seconds = 30")

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert any(line.startswith("problem:") and "wait_seconds" in line for line in lines)

