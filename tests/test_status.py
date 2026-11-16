"""Tests for the time-on-current-item part of nightshift status."""

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import nightshift.cli as cli_module
from nightshift.cli import main
from nightshift.config import load_config
from nightshift.progress import read_progress
from nightshift.status import current_item_seconds


def _make_run(tmp_path: Path, repo: Path) -> Path:
    """Create a run dir whose config points at repo with prefix T."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "st"\nrepo = "{repo.as_posix()}"\nprefix = "T"\n[check]\ntest = ["pytest"]\n',
        encoding="utf-8",
    )
    return run_dir


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def test_current_item_seconds_since_newest_matching_commit(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("one", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", ".")
    first_date = {"GIT_AUTHOR_DATE": "2026-11-15T23:00:00", "GIT_COMMITTER_DATE": "2026-11-15T23:00:00"}
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "T1: first item", env=first_date)
    (repo / "b.txt").write_text("two", encoding="utf-8")
    second_date = {"GIT_AUTHOR_DATE": "2026-11-16T00:58:00", "GIT_COMMITTER_DATE": "2026-11-16T00:58:00"}
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "T2: second item", env=second_date)

    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text(
        "- [x] T1. first item\n- [x] T2. second item\n- [ ] T3. third item\n", encoding="utf-8"
    )
    config = load_config(run_dir)
    progress = read_progress(repo / "PROGRESS-st.md", config.prefix)
    now = datetime(2026, 11, 16, 1, 38).timestamp()
    assert current_item_seconds(run_dir, config, progress, now=lambda: now) == 2400


def test_current_item_seconds_uses_first_event_when_no_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text("- [ ] T1. first item\n", encoding="utf-8")
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    first_event = {"t": "2026-11-15T23:00:00", "msg": "session 1 ended: 0/1 done, 0 blocked"}
    second_event = {"t": "2026-11-15T23:30:00", "msg": "agent did not start"}
    (gate_dir / "supervise.jsonl").write_text(
        json.dumps(first_event) + "\n" + json.dumps(second_event) + "\n", encoding="utf-8"
    )

    config = load_config(run_dir)
    progress = read_progress(repo / "PROGRESS-st.md", config.prefix)
    now = datetime(2026, 11, 15, 23, 38).timestamp()
    assert current_item_seconds(run_dir, config, progress, now=lambda: now) == 2280


def test_status_prints_time_on_current_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text(
        "- [x] T1. first item\n- [ ] T2. second item\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli_module, "current_item_seconds", lambda run_dir, config, progress: 9480)

    assert main(["status", str(run_dir)]) == 0
    lines = capsys.readouterr().out.splitlines()
    next_index = lines.index("next: T2 second item")
    assert lines[next_index + 1] == "on T2 for 2 h 38 min"


def test_status_json_reports_current_minutes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text(
        "- [x] T1. first item\n- [ ] T2. second item\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli_module, "current_item_seconds", lambda run_dir, config, progress: 9480)

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["next_item"] == 2
    assert payload["current_minutes"] == 158


def test_finished_run_has_no_time_on_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text("- [x] T1. first item\n", encoding="utf-8")

    config = load_config(run_dir)
    progress = read_progress(repo / "PROGRESS-st.md", config.prefix)
    assert current_item_seconds(run_dir, config, progress, now=lambda: 0.0) is None

    assert main(["status", str(run_dir)]) == 0
    out_lines = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("on ") for line in out_lines)

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current_minutes"] is None
