"""Tests for starting and waiting on the test gate."""

import sys
from pathlib import Path

import pytest

from nightshift.config import RunConfig
from nightshift.gate import start, wait

PASS_CMD = [sys.executable, "-c", "print('3 passed in 0.01s')"]
FAIL_CMD = [sys.executable, "-c", "print('1 failed, 2 passed in 0.01s'); raise SystemExit(1)"]
LINT_FAIL_CMD = [sys.executable, "-c", "print('lint problem here'); raise SystemExit(3)"]


def _config(tmp_path: Path, test=PASS_CMD, lint=None) -> RunConfig:
    """Build a config whose repo and commands live under tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return RunConfig(
        name="gate-test",
        repo=repo,
        test=list(test),
        lint=list(lint or []),
        min_passed=1,
        wait_seconds=2,
    )


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return run_dir


def test_start_then_wait_gives_gate_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    run_dir = _run_dir(tmp_path)

    assert start(config, run_dir) == 0
    out = capsys.readouterr().out
    assert "GATE STARTED" in out
    assert f"nightshift gate wait {run_dir} 1" in out

    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "3 passed in 0.01s" in out
    assert "GATE PASS" in out


def test_failing_command_gives_gate_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path, test=FAIL_CMD)
    run_dir = _run_dir(tmp_path)

    assert start(config, run_dir) == 0
    capsys.readouterr()

    assert wait(config, run_dir, 1) == 1
    out = capsys.readouterr().out
    assert "1 failed, 2 passed in 0.01s" in out
    assert "GATE FAIL" in out


def test_lint_failure_does_not_start(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path, lint=LINT_FAIL_CMD)
    run_dir = _run_dir(tmp_path)

    assert start(config, run_dir) == 1
    err = capsys.readouterr().err
    assert "lint problem here" in err
    assert "GATE NOT STARTED: lint failed" in err
    assert not (run_dir / ".gate" / "log").exists()


def test_second_start_while_running_does_not_launch_again(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    run_dir = _run_dir(tmp_path)
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    log_path = gate_dir / "log"
    # A fresh log without a done file is exactly what start() sees while a run is going.
    log_path.write_text("tick\n", encoding="utf-8")

    assert start(config, run_dir) == 0
    out = capsys.readouterr().out
    assert "GATE ALREADY RUNNING" in out
    # A second launch would truncate the log and create a done file; neither happened.
    assert log_path.read_text(encoding="utf-8") == "tick\n"
    assert not (gate_dir / "done").exists()
