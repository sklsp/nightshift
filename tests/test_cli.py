"""Tests for the nightshift command line interface."""

import pytest

from nightshift.cli import main


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "nightshift 0.1.0" in capsys.readouterr().out


def test_no_command_prints_help_and_returns_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out
