"""Tests for starting and waiting on the test gate."""

import os
import sys
import time
from pathlib import Path

import pytest

from nightshift.cli import main
from nightshift.config import RunConfig
from nightshift.gate import clean, file, parse_summary, start, wait

PASS_CMD = [sys.executable, "-c", "print('3 passed in 0.01s')"]
FAIL_CMD = [sys.executable, "-c", "print('1 failed, 2 passed in 0.01s'); raise SystemExit(1)"]
LINT_FAIL_CMD = [sys.executable, "-c", "print('lint problem here'); raise SystemExit(3)"]


def _config(tmp_path: Path, test=PASS_CMD, lint=None, pre=None) -> RunConfig:
    """Build a config whose repo and commands live under tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return RunConfig(
        name="gate-test",
        repo=repo,
        test=list(test),
        lint=list(lint or []),
        pre=[list(command) for command in (pre or [])],
        min_passed=1,
        wait_seconds=2,
    )


def _marker(path: Path) -> list[str]:
    """A fake passing command that writes a marker file."""
    return [sys.executable, "-c", f"open({str(path)!r}, 'w').write('x')"]


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


def test_two_pre_checks_pass_then_suite_starts_and_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    config = _config(tmp_path, pre=[_marker(one), _marker(two)])
    run_dir = _run_dir(tmp_path)

    assert start(config, run_dir) == 0
    out = capsys.readouterr().out
    assert "GATE STARTED" in out
    # Both pre checks ran before the suite launched.
    assert one.is_file() and two.is_file()

    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "3 passed in 0.01s" in out
    assert "GATE PASS" in out


def test_second_pre_check_failure_stops_gate_with_its_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    failing = [
        sys.executable,
        "-c",
        f"print('second pre check blew up'); open({str(two)!r}, 'w').write('x'); raise SystemExit(2)",
    ]
    config = _config(tmp_path, pre=[_marker(one), failing])
    run_dir = _run_dir(tmp_path)

    assert start(config, run_dir) == 1
    err = capsys.readouterr().err
    # The tail of the failed command and its name are reported.
    assert "second pre check blew up" in err
    assert f"GATE NOT STARTED: {sys.executable}" in err
    # Both pre checks ran, in order; the suite never started.
    assert one.is_file() and two.is_file()
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


def test_parse_summary_plain() -> None:
    summary = parse_summary("5 passed in 1.2s")
    assert (summary.passed, summary.failed, summary.errors) == (5, 0, 0)
    assert (summary.skipped, summary.xfailed, summary.xpassed) == (0, 0, 0)
    assert summary.line == "5 passed in 1.2s"


def test_parse_summary_mixed() -> None:
    summary = parse_summary("1 failed, 4 passed, 2 skipped, 1 xfailed")
    assert (summary.passed, summary.failed, summary.errors) == (4, 1, 0)
    assert (summary.skipped, summary.xfailed, summary.xpassed) == (2, 1, 0)


def test_parse_summary_errors_plural_and_singular() -> None:
    plural = parse_summary("==== 2 errors in 0.5s ====")
    assert plural.errors == 2
    singular = parse_summary("1 error in 0.1s")
    assert singular.errors == 1


def test_parse_summary_ignores_framing_and_duration() -> None:
    summary = parse_summary("===== 5 passed, 1 xpassed in 2.0s =====")
    assert (summary.passed, summary.xpassed) == (5, 1)
    assert summary.line == "5 passed, 1 xpassed in 2.0s"


def test_parse_summary_last_line_wins() -> None:
    log = "3 passed in 1.0s\n.....\n7 failed, 2 passed in 2.0s"
    summary = parse_summary(log)
    assert (summary.passed, summary.failed) == (2, 7)
    assert summary.line == "7 failed, 2 passed in 2.0s"


def test_parse_summary_none() -> None:
    summary = parse_summary(".....\n")
    assert summary.line is None
    assert (summary.passed, summary.failed, summary.errors) == (0, 0, 0)


def test_wait_without_summary_line_fails_with_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    run_dir = _run_dir(tmp_path)
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text(".....\n", encoding="utf-8")
    (gate_dir / "done").write_text("0\n", encoding="utf-8")

    assert wait(config, run_dir, 1) == 1
    out = capsys.readouterr().out
    assert "no pytest summary line: is -q passed twice? (pytest -qq hides it)" in out
    assert "GATE FAIL" in out


_COV_LOG = """\
tests/test_gate.py .                                                    [100%]
============================= coverage: platform win32 =============================
Name                    Stmts   Miss  Cover   Missing
-----------------------------------------------------
src/nightshift/gate.py     120     8    93%   45-47, 60
src/nightshift/cli.py       80     0   100%
-----------------------------------------------------
TOTAL                      200     8    96%

============================== 5 passed in 1.2s ===============================
"""


def _file_run(base: Path, log_text: str, module: str) -> tuple[RunConfig, Path]:
    """Build a finished file-mode gate run with the given fake log text."""
    base.mkdir(parents=True, exist_ok=True)
    config = _config(base)
    run_dir = _run_dir(base)
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text(log_text, encoding="utf-8")
    (gate_dir / "done").write_text("0\n", encoding="utf-8")
    (gate_dir / "mode").write_text(f"file {module}\npytest tests/test_gate.py --cov", encoding="utf-8")
    return config, run_dir


def test_file_mode_prints_module_coverage_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config, run_dir = _file_run(tmp_path, _COV_LOG, "src/nightshift/gate.py")

    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "93%" in out
    assert "(fully covered)" not in out
    assert "GATE PASS" in out


def test_file_mode_missing_module_means_fully_covered(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config, run_dir = _file_run(tmp_path, _COV_LOG, "src/nightshift/render.py")

    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "src/nightshift/render.py 100% (fully covered)" in out
    assert "GATE PASS" in out


def test_file_mode_matches_both_separators(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    backslash_log = _COV_LOG.replace("src/nightshift/gate.py", "src\\nightshift\\gate.py")

    config, run_dir = _file_run(tmp_path / "a", backslash_log, "src/nightshift/gate.py")
    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "93%" in out
    assert "(fully covered)" not in out

    config2, run_dir2 = _file_run(tmp_path / "b", _COV_LOG, "src\\nightshift\\gate.py")
    assert wait(config2, run_dir2, 1) == 0
    out2 = capsys.readouterr().out
    assert "93%" in out2
    assert "(fully covered)" not in out2


def test_file_mode_fails_when_tests_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failing_log = _COV_LOG.replace("5 passed in 1.2s", "2 failed, 3 passed in 1.2s")
    config, run_dir = _file_run(tmp_path, failing_log, "src/nightshift/gate.py")

    assert wait(config, run_dir, 1) == 1
    out = capsys.readouterr().out
    assert "GATE FAIL" in out


def test_file_refuses_when_lint_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path, lint=LINT_FAIL_CMD)
    run_dir = _run_dir(tmp_path)

    assert file(config, run_dir, "tests/test_gate.py", "src/nightshift/gate") == 1
    err = capsys.readouterr().err
    assert "GATE NOT STARTED: lint failed" in err
    assert not (run_dir / ".gate" / "log").exists()


def test_file_start_then_wait_reports_fully_covered(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    run_dir = _run_dir(tmp_path)

    assert file(config, run_dir, "tests/test_gate.py", "src/nightshift/gate") == 0
    out = capsys.readouterr().out
    assert "GATE STARTED" in out

    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "3 passed in 0.01s" in out
    assert "src/nightshift/gate 100% (fully covered)" in out
    assert "GATE PASS" in out


def _stale_log(run_dir: Path) -> None:
    """Create a .gate/log whose mtime is older than the fresh window, with no done file."""
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    log_path = gate_dir / "log"
    log_path.write_text("old output\n", encoding="utf-8")
    old = time.time() - 300
    os.utime(log_path, (old, old))


def test_clean_removes_gate_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _run_dir(tmp_path)
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text("tick\n", encoding="utf-8")
    (gate_dir / "mode").write_text("start\npytest -q", encoding="utf-8")

    assert clean(run_dir) == 0
    out = capsys.readouterr().out
    assert not gate_dir.exists()
    assert "log" in out
    assert "mode" in out


def test_clean_without_gate_folder_is_a_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _run_dir(tmp_path)

    assert clean(run_dir) == 0
    out = capsys.readouterr().out
    assert "nothing to remove" in out


def test_clean_via_cli_removes_gate_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _run_dir(tmp_path)
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text("tick\n", encoding="utf-8")

    assert main(["gate", "clean", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert not gate_dir.exists()
    assert "log" in out


def test_stale_log_makes_start_launch_again(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    run_dir = _run_dir(tmp_path)
    _stale_log(run_dir)

    assert start(config, run_dir) == 0
    out = capsys.readouterr().out
    assert "previous gate run looks stale; starting a new one" in out
    assert "GATE STARTED" in out
    # The relaunch wrote a fresh mode file and the detached run finishes on its own.
    assert (run_dir / ".gate" / "mode").is_file()

    assert wait(config, run_dir, 1) == 0
    out = capsys.readouterr().out
    assert "GATE PASS" in out


def test_stale_log_makes_wait_report_gate_stale(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(tmp_path)
    run_dir = _run_dir(tmp_path)
    _stale_log(run_dir)

    assert wait(config, run_dir, 1) == 1
    out = capsys.readouterr().out
    assert f"GATE STALE: run nightshift gate start {run_dir} again" in out
