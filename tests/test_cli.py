"""Tests for the nightshift command line interface."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift.cli import main


def _make_run(tmp_path: Path, repo: Path, test: list[str] | None = None) -> Path:
    """Create a run dir whose config points at repo with prefix N."""
    if test is None:
        test = ["pytest"]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "st"\nrepo = "{repo.as_posix()}"\nprefix = "N"\n[check]\ntest = {test!r}\n',
        encoding="utf-8",
    )
    return run_dir


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def test_status_prints_progress_gate_and_commits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "first commit")

    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text(
        "- [x] N1. first item\n- [ ] N2. second item BLOCKED\n- [ ] N3. third item\n", encoding="utf-8"
    )
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text("collected 1 item\n= 1 passed in 0.01s =\n", encoding="utf-8")
    (gate_dir / "done").write_text("0\n", encoding="utf-8")

    assert main(["status", str(run_dir)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "st: 1/3 done"
    assert "blocked: N2" in lines
    assert "next: N3 third item" in lines
    assert "last gate: exit 0: 1 passed in 0.01s" in lines
    assert any(line.endswith(" first commit") for line in lines)


def test_status_without_progress_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)

    assert main(["status", str(run_dir)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "st: 0/0 done"
    assert "blocked: none" in lines
    assert "next: none" in lines
    assert "last gate: none" in lines
    assert lines[-1].startswith("commits:")


def test_status_json_with_progress(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "first commit")

    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text(
        "- [x] N1. first item\n- [ ] N2. second item BLOCKED\n- [ ] N3. third item\n", encoding="utf-8"
    )
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text("collected 1 item\n= 1 passed in 0.01s =\n", encoding="utf-8")
    (gate_dir / "done").write_text("0\n", encoding="utf-8")

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "name", "total", "done", "blocked", "next_item", "next_title", "gate", "commits"
    }
    assert payload["name"] == "st"
    assert payload["total"] == 3
    assert payload["done"] == [1]
    assert payload["blocked"] == [2]
    assert payload["next_item"] == 3
    assert payload["next_title"] == "third item"
    assert payload["gate"] == "pass"
    commits = payload["commits"]
    assert len(commits) == 1
    assert commits[0]["subject"] == "first commit"
    assert len(commits[0]["hash"]) > 0


def test_status_json_fresh_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "name", "total", "done", "blocked", "next_item", "next_title", "gate", "commits"
    }
    assert payload["name"] == "st"
    assert payload["total"] == 0
    assert payload["done"] == []
    assert payload["blocked"] == []
    assert payload["next_item"] is None
    assert payload["next_title"] is None
    assert payload["gate"] == "none"
    assert payload["commits"] == []


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "nightshift 0.1.0" in capsys.readouterr().out


def test_gate_start_and_wait_via_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast = [sys.executable, "-c", "pass"]
    run_dir = _make_run(tmp_path, repo, test=fast)
    assert main(["gate", "start", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "GATE STARTED" in out
    assert f"nightshift gate wait {run_dir} 1" in out
    # the child finishes at once; wait blocks until done, then reports no summary line
    assert main(["gate", "wait", str(run_dir), "1"]) == 1
    out = capsys.readouterr().out
    assert "no pytest summary line" in out


def test_gate_file_via_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast = [sys.executable, "-c", "pass"]
    run_dir = _make_run(tmp_path, repo, test=fast)
    assert main(["gate", "file", str(run_dir), "tests/test_x.py", "nightshift.x"]) == 0
    out = capsys.readouterr().out
    assert "GATE STARTED" in out


def test_gate_with_missing_config_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["gate", "wait", str(empty), "1"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err


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
