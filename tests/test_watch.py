"""Tests for the watch command."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.cli import main
from nightshift.config import RunConfig, load_config
from nightshift.git import commit_log
from nightshift.watch import watch


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _commit(repo: Path, message: str) -> None:
    """Add one empty commit with the given subject to repo."""
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", message)


def _make_repo(tmp_path: Path) -> Path:
    """Create a git repo with one initial commit and return its path."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", ".")
    _commit(repo, "first commit")
    return repo


def _make_run(tmp_path: Path, repo: Path) -> tuple[Path, RunConfig]:
    """Create a run dir whose config points at repo with name st and prefix N."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "st"\nrepo = "{repo.as_posix()}"\nprefix = "N"\n[check]\ntest = ["pytest"]\n',
        encoding="utf-8",
    )
    return run_dir, load_config(run_dir)


def _event(stamp: str, msg: str) -> str:
    """One line of .gate/supervise.jsonl."""
    return json.dumps({"t": stamp, "msg": msg}) + "\n"


def test_first_poll_prints_nothing_old_and_ctrl_c_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    run_dir, config = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text("- [ ] N1. one item\n", encoding="utf-8")
    gate = run_dir / ".gate"
    gate.mkdir()
    (gate / "supervise.jsonl").write_text(_event("2026-01-01T10:00:00", "old event"), encoding="utf-8")

    def fake_sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    code = watch(config, run_dir, sleep=fake_sleep)

    assert code == 0
    assert capsys.readouterr().out == ""


def test_new_commit_and_event_printed_exactly_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    run_dir, config = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text("- [ ] N1. one item\n- [ ] N2. two items\n", encoding="utf-8")
    gate = run_dir / ".gate"
    gate.mkdir()
    (gate / "supervise.jsonl").write_text(_event("2026-01-01T10:00:00", "old event"), encoding="utf-8")

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            _commit(repo, "second commit")
            with (gate / "supervise.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(_event("2026-01-01T10:05:00", "new event"))
        else:
            (repo / "PROGRESS-st.md").write_text("- [x] N1. one item\n- [x] N2. two items\n", encoding="utf-8")

    code = watch(config, run_dir, sleep=fake_sleep)

    new_hash = commit_log(repo)[0][0]
    assert code == 0
    assert sleeps == [20.0, 20.0]
    assert capsys.readouterr().out.splitlines() == [
        f"commit {new_hash} second commit",
        "10:05:00 new event",
        "watch: run finished",
    ]


def test_finished_progress_ends_loop_with_final_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    run_dir, config = _make_run(tmp_path, repo)
    gate = run_dir / ".gate"
    gate.mkdir()
    (gate / "progress.md").write_text("- [x] N1. one item\n", encoding="utf-8")

    def fake_sleep(seconds: float) -> None:
        raise AssertionError("sleep must not be called when the run is already finished")

    code = watch(config, run_dir, sleep=fake_sleep)

    assert code == 0
    assert capsys.readouterr().out.splitlines() == ["watch: run finished"]


def test_once_prints_all_events_and_last_five_commits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path)
    for number in range(2, 8):
        _commit(repo, f"commit {number}")
    run_dir, config = _make_run(tmp_path, repo)
    gate = run_dir / ".gate"
    gate.mkdir()
    (gate / "supervise.jsonl").write_text(
        _event("2026-01-01T10:00:00", "first event") + _event("2026-01-01T10:01:00", "second event"),
        encoding="utf-8",
    )

    code = watch(config, run_dir, once=True)

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[:2] == ["10:00:00 first event", "10:01:00 second event"]
    # the five NEWEST of the seven commits, oldest first
    assert [line.split(" ", 2)[2] for line in lines[2:]] == [f"commit {n}" for n in range(3, 8)]


def test_every_is_clamped_to_at_least_one_second(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    run_dir, config = _make_run(tmp_path, repo)
    (repo / "PROGRESS-st.md").write_text("- [ ] N1. one item\n", encoding="utf-8")

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            raise KeyboardInterrupt

    code = watch(config, run_dir, every=0.5, sleep=fake_sleep)

    assert code == 0
    assert sleeps == [1]


def test_cli_watch_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_repo(tmp_path)
    run_dir, _config = _make_run(tmp_path, repo)
    gate = run_dir / ".gate"
    gate.mkdir()
    (gate / "supervise.jsonl").write_text(_event("2026-01-01T09:30:00", "booted"), encoding="utf-8")

    code = main(["watch", str(run_dir), "--once"])

    first_hash = commit_log(repo)[0][0]
    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        "09:30:00 booted",
        f"commit {first_hash} first commit",
    ]


def test_cli_watch_missing_run_toml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope"

    code = main(["watch", str(missing)])

    assert code == 1
    assert capsys.readouterr().err.strip() == f"error: missing run.toml in {missing}"

