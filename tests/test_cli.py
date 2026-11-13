"""Tests for the nightshift command line interface."""

from pathlib import Path

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


def test_init_then_build_works_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = tmp_path / "run"
    assert main(["init", str(run_dir)]) == 0
    init_out = capsys.readouterr().out
    assert (run_dir / "run.toml").is_file()
    assert (run_dir / "items.md").is_file()
    assert "run.toml" in init_out
    assert "items.md" in init_out

    assert main(["build", str(run_dir)]) == 0
    build_out = capsys.readouterr().out
    for name in ("00-start.md", "item-01.md", "item-02.md", "checklist.txt"):
        assert (run_dir / name).is_file()
        assert any(name in line for line in build_out.splitlines())


def test_init_refuses_existing_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = tmp_path / "run"
    assert main(["init", str(run_dir)]) == 0
    capsys.readouterr()
    assert main(["init", str(run_dir)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "already exists" in err


def test_build_reports_bad_config_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        '[run]\nname = "bad name"\nrepo = "."\n[check]\ntest = ["pytest"]\n', encoding="utf-8"
    )
    assert main(["build", str(run_dir)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "run.name" in err
