"""Tests for the supervise loop."""

import itertools
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

import nightshift.agent as agent_module
from nightshift.cli import main
from nightshift.config import RunConfig
from nightshift.supervise import _pid_alive, describe_repo_change, mark_item_blocked, supervise, uncommitted_paths


class Clock:
    """A fake clock whose sleep advances time by exactly the requested seconds."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds
        self.sleeps.append(seconds)


_ITEMS_MD = """\
# Items for the supervise test

## first item
Do the first thing and tick it.

## second item
Do the second thing and tick it.
"""

_UNTICKED = "- [ ] T1. first item\n- [ ] T2. second item\n"
_TICKED = "- [x] T1. first item (1 passed in 0.01s)\n- [x] T2. second item (2 passed in 0.01s)\n"
_PARTIAL_TICKED = "- [x] T1. first item (1 passed in 0.01s)\n- [ ] T2. second item\n"




def _setup(
    tmp_path: Path,
    progress_text: str,
    max_sessions: int,
    *,
    idle_minutes: int = 60,
    item_minutes: int = 0,
    stop: list[str] | None = None,
    launch_backoff_seconds: int = 60,
    max_launch_failures: int = 6,
    branch: str = "",
) -> tuple[RunConfig, Path]:
    """Build a repo with a progress file and a run dir holding two items."""
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    repo.mkdir()
    run_dir.mkdir()
    (run_dir / "items.md").write_text(_ITEMS_MD, encoding="utf-8")
    (repo / "PROGRESS-sup.md").write_text(progress_text, encoding="utf-8")
    config = RunConfig(
        name="sup",
        repo=repo,
        test=[sys.executable, "-c", "print('1 passed in 0.01s')"],
        launch=["fake-launch"],
        status=["fake-status"],
        max_sessions=max_sessions,
        idle_minutes=idle_minutes,
        item_minutes=item_minutes,
        stop=list(stop or []),
        launch_backoff_seconds=launch_backoff_seconds,
        max_launch_failures=max_launch_failures,
        branch=branch,
    )
    return config, run_dir


def _expected_message(run_dir: Path) -> str:
    """The exact launch message for a two item run."""
    return (
        f"Read {run_dir.resolve().as_posix()}/00-start.md and follow it. "
        "Resume at the first item that is not ticked. "
        "Do NOT call attempt_completion until item 2 is done."
    )


def no_fingerprint(repo: Path) -> str | None:
    """Stand in for git.fingerprint where a test's repo is not a git repository."""
    return None


def no_git_dir(repo: Path) -> Path | None:
    """Stand in for git.git_dir where a test's repo is not a git repository."""
    return None


def test_supervise_finishes_when_progress_gets_ticked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    assert launches == [_expected_message(run_dir)]
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_supervise_stops_at_max_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    launches: list[str] = []

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    assert len(launches) == 2
    assert "stopped after 2 sessions" in logs[-1]


def test_supervise_stops_after_three_empty_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=10)
    clock = Clock()
    launches: list[str] = []
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        # Each session starts (two failed polls then a running one) but never ticks an item.
        calls["n"] += 1
        return calls["n"] % 4 == 3

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    # Three sessions ran and ended without progress; the fourth launch never happened.
    assert len(launches) == 3
    assert logs[-1] == "agent stuck: no new item done three times in a row; a human should look"


def test_supervise_does_not_launch_while_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    events: list[str] = []
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        events.append("status")
        return calls["n"] <= 2

    def fake_launch(cfg: RunConfig, message: str) -> int:
        events.append("launch")
        return 0

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    assert events.count("launch") == 1
    # the first two status checks saw a running agent, so no relaunch happened then
    assert events.index("launch") > 2


def test_supervise_never_polls_faster_than_poll_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1)
    clock = Clock()
    times: list[float] = []
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        times.append(clock.time())
        return calls["n"] <= 2

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: None)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert all(gap >= config.poll_seconds - 1e-9 for gap in gaps)


def test_grace_period_waits_for_agent_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 5:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (4, 5)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_grace_period_logs_when_agent_never_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    # Every launch failed to start; the backoff doubled between sessions.
    assert logs.count("agent did not start") == 2
    assert clock.sleeps.count(60) == 1
    assert clock.sleeps.count(120) == 1


def test_fast_agent_that_finishes_before_any_poll_ends_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent never reports running: it finishes all the work before any poll sees it.
        calls["n"] += 1
        if calls["n"] == 3:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return False

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    # The finished work is treated as a normal session end: no relaunch and no backoff.
    assert "agent did not start" not in logs
    assert logs[-1] == "session 1 ended: 2/2 done, 0 blocked"


def test_failed_start_backs_off_then_the_next_session_finishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    launches: list[str] = []
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        calls["n"] += 1
        if calls["n"] == 8:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (7, 8)

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    # The first launch never came up and backed off; the second one finished the run.
    assert len(launches) == 2
    assert clock.sleeps.count(60) == 1


def test_repeated_failed_starts_end_with_exit_code_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=10)
    clock = Clock()
    launches: list[str] = []

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 2
    # The default cap is six failed starts; the supervisor gives up and asks for a human.
    assert len(launches) == 6
    assert logs[-1] == "agent cannot be launched after 6 tries; a human should look"


def test_launch_code_in_ok_codes_is_not_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    config = replace(config, launch_ok_codes=[0, 1])
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    # Cline exits with code 1 even when the session starts fine; [0, 1] keeps that out of the log.
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 1)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_launch_code_outside_ok_codes_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    # The default ok codes are [0], so a launch that exits with code 1 is logged as before.
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 1)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    assert logs == ["agent launch exited with code 1", "session 1 ended: 2/2 done, 0 blocked"]


def test_launch_backoff_is_capped_at_fifteen_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=10, launch_backoff_seconds=600, max_launch_failures=3)
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 2
    # The backoff doubles (600 then 1200) but never exceeds the fifteen minute cap.
    assert clock.sleeps.count(900) == 1
    assert all(s <= 900 for s in clock.sleeps)


def test_changing_fingerprint_never_triggers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5, idle_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 4:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] <= 5

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    # A fingerprint that changes on every poll, even though the session runs
    # far longer than idle_minutes.
    def fingerprint(repo: Path) -> str:
        return f"fp-{calls['n']}"

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=fingerprint,
        git_dir=no_git_dir
    )

    assert code == 0
    assert not any("agent looks stuck" in line for line in logs)


def test_unchanged_fingerprint_triggers_and_runs_stop_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5, idle_minutes=1, stop=["fake-stop"])
    clock = Clock()
    calls = {"n": 0}
    stops: list[RunConfig] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        return calls["n"] <= 6

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same",
        git_dir=no_git_dir
    )

    assert code == 1
    # The stuck session was logged once and stopped exactly once...
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1
    assert len(stops) == 1
    # ...and every later launch failed to start, so the run ended at the session cap.
    assert logs[-1] == "stopped after 5 sessions without finishing; a human should look"


def test_unchanged_fingerprint_without_stop_only_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2, idle_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        return calls["n"] <= 4

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same",
        git_dir=no_git_dir
    )

    assert code == 1
    # Without a stop command the supervisor only reports and lets the session end.
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1
    assert "stopped after 2 sessions" in logs[-1]


def test_frozen_repo_is_reported_stuck_after_idle_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2, idle_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent keeps running the whole time while the repo stays frozen.
        calls["n"] += 1
        return calls["n"] <= 4

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stop_times: list[float] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stop_times.append(clock.t))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same",
        git_dir=no_git_dir
    )

    assert code == 1
    # The idle clock starts at the first readable fingerprint (t=30), so one minute is up at t=90.
    assert stop_times == [90.0]
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1


def test_item_limit_below_idle_blocks_the_item_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2, idle_minutes=2, item_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent keeps running on the first item while the repo stays frozen.
        calls["n"] += 1
        return calls["n"] <= 6

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops_at: list[float] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops_at.append(clock.t))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same",
        git_dir=no_git_dir
    )

    assert code == 0
    # The item limit (one minute) fires before the idle limit (two minutes): T1 is blocked at t=90,
    # the stuck report lands at t=150, and T2 (next since t=120) hits its own one-minute window
    # on the poll after the stop-wait loop, so both items end up resolved.
    assert logs == [
        "item T1 blocked by the supervisor after 1 minutes",
        "agent looks stuck: no commit and no file change for 2 minutes",
        "item T2 blocked by the supervisor after 1 minutes",
        "session 1 ended: 0/2 done, 2 blocked",
    ]
    # One stop from each item block, one from the stuck report.
    assert stops_at == [90.0, 150.0, 180.0]
    lines = (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8").splitlines()
    assert "- [ ] T1. first item BLOCKED" in lines
    assert "- [ ] T2. second item BLOCKED" in lines


def test_item_limit_above_idle_reports_stuck_before_blocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2, idle_minutes=1, item_minutes=2)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent keeps running on the first item while the repo stays frozen.
        calls["n"] += 1
        return calls["n"] <= 4

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops: list[RunConfig] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same",
        git_dir=no_git_dir
    )

    assert code == 1
    # The idle limit (one minute) fires before the item limit (two minutes), so nothing gets blocked.
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1
    assert not any("blocked by the supervisor" in line for line in logs)
    assert "BLOCKED" not in (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8")
    assert len(stops) == 1


def test_none_fingerprint_does_not_reset_the_idle_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2, idle_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent keeps running while git fails on every other poll.
        calls["n"] += 1
        return calls["n"] <= 4

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stop_times: list[float] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stop_times.append(clock.t))

    def fingerprint(repo: Path) -> str | None:
        # Odd polls stand in for a git failure; the readable ones never change.
        return "same" if calls["n"] % 2 == 0 else None

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=fingerprint,
        git_dir=no_git_dir
    )

    assert code == 1
    # The failed polls must not restart the idle clock: one minute after the first readable
    # fingerprint (t=30) is t=90, and that is when the stop lands.
    assert stop_times == [90.0]
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1


def test_describe_repo_change_says_what_changed() -> None:
    # A moved HEAD is reported as such, whatever the status lines do.
    assert describe_repo_change("aaa\n M a.txt", "bbb\n?? b.txt") == "HEAD moved"
    # With the same HEAD, the porcelain lines that are new or gone are listed, new first.
    assert (
        describe_repo_change("aaa\n M a.txt\n?? b.txt", "aaa\n M a.txt\nM c.txt")
        == "M c.txt; ?? b.txt"
    )
    # More than three changed lines are cut to three with the rest counted.
    old = "aaa\n"
    new = "aaa\n M a.txt\n M b.txt\n?? c.txt\nA d.txt\nD e.txt\n"
    assert describe_repo_change(old, new) == " M a.txt;  M b.txt; ?? c.txt; and 2 more"
    # The same lines in another order still say something.
    assert describe_repo_change("aaa\n M a\n M b", "aaa\n M b\n M a") == (
        "the status text changed but no line is new or gone"
    )


def test_repo_change_after_a_quiet_stretch_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5, idle_minutes=90)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent ticks both items when the commit lands and keeps running one poll longer.
        calls["n"] += 1
        if calls["n"] == 142:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] <= 143

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    # The repo is quiet for seventy minutes (HEAD aaa), then a commit lands and moves HEAD to bbb.
    def fingerprint(repo: Path) -> str:
        if clock.t < 4230:
            return "aaa\n M a.txt"
        return "bbb\n M a.txt"

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=fingerprint,
        git_dir=no_git_dir
    )

    assert code == 0
    # The first readable fingerprint lands at t=30, so the change at t=4230 follows seventy quiet minutes.
    assert logs.count("repo changed after 70 minutes quiet: HEAD moved") == 1


def test_repo_change_before_ten_quiet_minutes_is_not_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent ticks both items when the commit lands and keeps running one poll longer.
        calls["n"] += 1
        if calls["n"] == 11:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] <= 12

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    # The commit lands five minutes after the first readable fingerprint, below the ten minute mark.
    def fingerprint(repo: Path) -> str:
        if clock.t < 300:
            return "aaa\n M a.txt"
        return "bbb\n M a.txt"

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=fingerprint,
        git_dir=no_git_dir
    )

    assert code == 0
    assert not any("repo changed" in line for line in logs)


class _FlushRecorder:
    """Stands in for stdout redirected to a file; records the content after every flush."""

    def __init__(self) -> None:
        self.content = ""
        self.snapshots: list[str] = []

    def write(self, text: str) -> int:
        self.content += text
        return len(text)

    def flush(self) -> None:
        self.snapshots.append(self.content)


def test_supervise_flushes_log_lines_through_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    repo.mkdir()
    run_dir.mkdir()
    (run_dir / "items.md").write_text(_ITEMS_MD, encoding="utf-8")
    (repo / "PROGRESS-sup.md").write_text(_UNTICKED, encoding="utf-8")
    toml = f"""[run]
name = "sup"
repo = "{repo.as_posix()}"

[check]
test = [{sys.executable!r}, "-c", "print('1 passed in 0.01s')"]

[agent]
launch = ["fake-launch"]
status = ["fake-status"]
poll_seconds = 0
"""
    (run_dir / "run.toml").write_text(toml, encoding="utf-8")

    recorder = _FlushRecorder()
    monkeypatch.setattr(sys, "stdout", recorder)
    calls = {"n": 0}
    seen: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 4:
            # We are still inside main(); the session one line must already be flushed.
            seen.extend(recorder.snapshots)
        if calls["n"] == 6:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 5)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    code = main(["supervise", str(run_dir)])

    assert code == 0
    # The session line reached the redirected stream while the process was still running.
    assert any("session 1 ended" in snapshot for snapshot in seen)


def test_supervise_copies_progress_to_gate_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    copy = run_dir / ".gate" / "progress.md"
    assert copy.is_file()
    # The repo file was still there at session end, so it was read directly and the copy kept current.
    assert copy.read_text(encoding="utf-8") == _TICKED
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_supervise_uses_gate_copy_when_repo_progress_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        if calls["n"] == 4:
            # A normal cleanup step before merging removes the progress file.
            (cfg.repo / "PROGRESS-sup.md").unlink()
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    # The finished state survived the cleanup because the supervisor read its own copy.
    assert (run_dir / ".gate" / "progress.md").read_text(encoding="utf-8") == _TICKED
    assert logs.count("repo progress file missing; reading the .gate copy") == 1
    assert logs[-1] == "session 1 ended: 2/2 done, 0 blocked"


def test_supervise_missing_progress_everywhere_means_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    (config.repo / "PROGRESS-sup.md").unlink()  # no repo file and no .gate copy yet
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    # Nothing was copied because there was nothing to copy.
    assert not (run_dir / ".gate" / "progress.md").exists()
    for line in logs:
        if line.startswith("session"):
            assert "0/2 done" in line


def test_unknown_status_keeps_session_going_and_logs_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1)
    clock = Clock()
    statuses = [None, None, True, False]
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        return statuses.pop(0)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    # The unknown polls did not end the session and did not trigger a launch; only the clean False ended it.
    assert launches == []
    assert statuses == []
    assert "agent status readable again after 2 failed polls" in logs
    assert not any("treating the session as ended" in line for line in logs)


def test_ten_unknown_statuses_end_the_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1)
    clock = Clock()
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        return None

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    assert launches == []
    assert logs.count("agent status unknown for 10 polls; treating the session as ended") == 1


def test_a_session_after_an_unknown_give_up_gets_a_fresh_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    # Session 1: eleven unknowns end it. Session 2: running, one unknown, running again, then stopped.
    statuses = iter([None] * 11 + [True, None, True, False])

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        return next(statuses, False)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    # Without the reset, session 2's first unknown poll would end it at once and never log the recovery.
    assert "agent status readable again after 1 failed polls" in logs


def test_finished_run_writes_a_timestamped_event_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        calls["n"] += 1
        if calls["n"] == 4:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return [None, None, True, False][calls["n"] - 1]

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 0
    events_path = run_dir / ".gate" / "supervise.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    # One event per logged line, in order; the stamps are the fake clock at each log call (polls of 30 s).
    stamp_60 = datetime.fromtimestamp(60).isoformat(timespec="seconds")
    stamp_90 = datetime.fromtimestamp(90).isoformat(timespec="seconds")
    assert [json.loads(line) for line in lines] == [
        {"t": stamp_60, "msg": "agent status readable again after 2 failed polls"},
        {"t": stamp_90, "msg": "session 1 ended: 2/2 done, 0 blocked"},
    ]


def test_unwritable_gate_does_not_stop_the_supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1)
    (run_dir / ".gate").write_text("not a directory", encoding="utf-8")  # .gate as a file blocks every write into it
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    # The supervisor kept running and logging even though .gate cannot hold the event log or the progress copy.
    assert "agent did not start" in logs
    assert logs[-1] == "stopped after 1 sessions without finishing; a human should look"


def _git_init(repo: Path) -> None:
    """Make repo a git repository so the supervisor lock has a home."""
    subprocess.run(
        ["git", "init", "-q"], cwd=repo, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )


def test_pid_alive_reports_the_current_process() -> None:
    assert _pid_alive(os.getpid())


def test_lock_exists_during_run_and_is_removed_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    _git_init(config.repo)
    lock_path = config.repo / ".git" / "nightshift-supervise.lock"
    clock = Clock()
    calls = {"n": 0}
    seen: dict[str, bool] = {}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            # We are still inside supervise(); the lock must be held right now.
            seen["during"] = lock_path.is_file()
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert seen["during"] is True
    assert not lock_path.exists()


def test_live_lock_refuses_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    _git_init(config.repo)
    other_pid = 4242  # any pid: the injected alive fake says it is running
    (config.repo / ".git" / "nightshift-supervise.lock").write_text(
        json.dumps({"pid": other_pid, "run": str(run_dir)}), encoding="utf-8"
    )
    clock = Clock()
    launches: list[str] = []

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, alive=lambda pid: True)

    assert code == 2
    # The supervisor must refuse before doing anything else.
    assert not launches
    assert logs == [f"another supervisor (pid {other_pid}) is running {run_dir!s}; refusing to start"]


def test_stale_lock_is_removed_and_taken_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    lock_path = config.repo / ".git" / "nightshift-supervise.lock"
    dead_pid = 4243  # any pid: the injected alive fake says it is gone
    _git_init(config.repo)
    lock_path.write_text(json.dumps({"pid": dead_pid, "run": str(run_dir)}), encoding="utf-8")
    clock = Clock()
    calls = {"n": 0}
    seen: dict[str, int] = {}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            # The stale lock must have been replaced with our own pid by now.
            seen["pid"] = json.loads(lock_path.read_text(encoding="utf-8"))["pid"]
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, alive=lambda pid: False)

    assert code == 0
    assert f"removing a stale supervisor lock (pid {dead_pid})" in logs
    assert seen["pid"] == os.getpid()


def test_refuses_to_start_on_the_wrong_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5, branch="main")
    _git_init(config.repo)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "feature"],
        cwd=config.repo,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    clock = Clock()
    launches: list[str] = []

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 2
    # The supervisor must refuse before taking the lock or launching anything.
    assert not launches
    assert not (config.repo / ".git" / "nightshift-supervise.lock").exists()
    assert logs == ["repo is on branch feature, this run needs main; refusing to start"]


def test_starts_when_on_the_right_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5, branch="night")
    _git_init(config.repo)
    subprocess.run(
        ["git", "checkout", "-q", "-b", "night"],
        cwd=config.repo,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def _git_commit(repo: Path, file_name: str, text: str, message: str) -> tuple[str, int]:
    """Commit one file in repo and return the commit's short hash and unix time."""
    (repo / file_name).write_text(text, encoding="utf-8")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="nightshift test",
        GIT_AUTHOR_EMAIL="test@nightshift.local",
        GIT_COMMITTER_NAME="nightshift test",
        GIT_COMMITTER_EMAIL="test@nightshift.local",
    )
    subprocess.run(
        ["git", "add", file_name], cwd=repo, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    result = subprocess.run(
        ["git", "log", "-1", "--format=%h %ct"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    short_hash, _, unix_time = result.stdout.strip().partition(" ")
    return short_hash, int(unix_time)


def test_newly_ticked_items_record_their_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    _git_init(config.repo)
    # A base commit first: the run's range starts after it, so both item commits are inside.
    _git_commit(config.repo, "base.txt", "base", "chore: start")
    first_hash, first_time = _git_commit(
        config.repo, "one.txt", "one", f"{config.prefix}1: do the first thing"
    )
    second_hash, second_time = _git_commit(
        config.repo, "two.txt", "two", f"{config.prefix}2: do the second thing"
    )
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert f"item {config.prefix}1 committed {first_hash} at {first_time}" in logs
    assert f"item {config.prefix}2 committed {second_hash} at {second_time}" in logs


def test_item_committed_before_the_run_is_not_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    _git_init(config.repo)
    # The item was committed before the run started: it is not in the run's range.
    _git_commit(config.repo, "one.txt", "one", f"{config.prefix}1: do the first thing")
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_PARTIAL_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    # The pre-run commit is outside the range, so no item event may be logged for it.
    assert not any(f"item {config.prefix}1 committed" in line for line in logs)


def test_an_item_ticked_before_its_commit_is_recorded_once_the_commit_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    _git_init(config.repo)
    _git_commit(config.repo, "base.txt", "base", "chore: start")
    clock = Clock()
    calls = {"n": 0}
    hashes: dict[str, tuple[str, int]] = {}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        n = calls["n"]
        if n == 3:  # session 1 ends with T1 ticked but its commit not made yet
            (cfg.repo / "PROGRESS-sup.md").write_text(_PARTIAL_TICKED, encoding="utf-8")
        if n == 5:  # session 2: both commits land, then both are ticked
            hashes["1"] = _git_commit(cfg.repo, "one.txt", "one", f"{cfg.prefix}1: do the first thing")
            hashes["2"] = _git_commit(cfg.repo, "two.txt", "two", f"{cfg.prefix}2: do the second thing")
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return n in (2, 5)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    first = [line for line in logs if line.startswith(f"item {config.prefix}1 committed")]
    assert first == [f"item {config.prefix}1 committed {hashes['1'][0]} at {hashes['1'][1]}"]


def test_no_item_limit_when_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent runs far longer than any limit would allow.
        calls["n"] += 1
        return calls["n"] <= 8

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops: list[RunConfig] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    progress_text = (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8")
    assert "BLOCKED" not in progress_text
    assert not any("blocked by the supervisor" in line for line in logs)
    assert stops == []


def test_item_past_the_limit_gets_blocked_and_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=3, item_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent keeps working on the first item until it has been next for a full minute.
        calls["n"] += 1
        return calls["n"] <= 5

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops: list[RunConfig] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    lines = (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8").splitlines()
    assert "- [ ] T1. first item BLOCKED" in lines
    assert "- [ ] T2. second item" in lines
    assert logs.count("item T1 blocked by the supervisor after 1 minutes") == 1


def test_item_clock_resets_when_the_next_item_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1, item_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The first item is ticked mid-session; by the third poll a full minute has passed since
        # the run started, but only half of it on the second item. Without the reset the second
        # item would be blocked here.
        calls["n"] += 1
        if calls["n"] == 4:
            (cfg.repo / "PROGRESS-sup.md").write_text(_PARTIAL_TICKED, encoding="utf-8")
        return calls["n"] <= 5

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops: list[RunConfig] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    assert code == 1
    progress_text = (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8")
    assert "BLOCKED" not in progress_text
    assert not any("blocked by the supervisor" in line for line in logs)
    assert stops == []


def test_mark_item_blocked_appends_the_marker_once(tmp_path: Path) -> None:
    progress_file = tmp_path / "PROGRESS-T.md"
    progress_file.write_text(_UNTICKED, encoding="utf-8")
    mark_item_blocked(progress_file, "T", 1)
    lines = progress_file.read_text(encoding="utf-8").splitlines()
    assert "- [ ] T1. first item BLOCKED" in lines
    assert "- [ ] T2. second item" in lines
    # A second call must not double-mark the line.
    mark_item_blocked(progress_file, "T", 1)
    assert progress_file.read_text(encoding="utf-8").splitlines() == lines




def test_already_blocked_item_is_not_stopped_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    progress = "- [ ] T1. first item BLOCKED\n- [ ] T2. second item\n"
    config, run_dir = _setup(tmp_path, progress, max_sessions=5, item_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The agent works on the second item until it has been next for a full minute.
        calls["n"] += 1
        return calls["n"] <= 5

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops: list[RunConfig] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    # Both items are resolved (one pre-blocked, one blocked by the supervisor), so the run ends.
    assert code == 0
    lines = (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8").splitlines()
    assert "- [ ] T1. first item BLOCKED" in lines
    assert "- [ ] T2. second item BLOCKED" in lines
    # Only the second item was blocked by the supervisor; the pre-blocked one is not touched.
    assert logs.count("item T2 blocked by the supervisor after 1 minutes") == 1
    assert len(stops) == 1

    assert len(stops) == 1




def test_a_done_item_is_never_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5, item_minutes=1)
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        # The first item is ticked mid-session; the agent then stalls on the second one until it
        # passes the limit. More than a minute has passed since the run started when that happens.
        calls["n"] += 1
        if calls["n"] == 4:
            (cfg.repo / "PROGRESS-sup.md").write_text(_PARTIAL_TICKED, encoding="utf-8")
        return calls["n"] <= 6

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)
    stops: list[RunConfig] = []
    monkeypatch.setattr(agent_module, "stop", lambda cfg: stops.append(cfg))

    logs: list[str] = []
    code = supervise(
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append,
        fingerprint=no_fingerprint, git_dir=no_git_dir
    )

    # The second item is blocked and both items are resolved, so the run ends. Only the current
    # item carries the marker; the done first item keeps its line exactly as it was ticked.
    assert code == 0
    lines = (config.repo / "PROGRESS-sup.md").read_text(encoding="utf-8").splitlines()
    assert "- [x] T1. first item (1 passed in 0.01s)" in lines
    assert "- [ ] T2. second item BLOCKED" in lines
    assert logs.count("item T2 blocked by the supervisor after 1 minutes") == 1
    assert len(stops) == 1


def _git_dirty_repo(tmp_path: Path) -> tuple[RunConfig, Path]:
    """A real git repo with one committed tracked file plus a run dir holding two unticked items."""
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    _git_init(config.repo)
    _git_commit(config.repo, "one.txt", "one", "chore: start")
    return config, run_dir


def test_uncommitted_paths_lists_modified_tracked_files_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _git_commit(repo, "one.txt", "one", "T1: first item")
    (repo / "stray.txt").write_text("new", encoding="utf-8")  # untracked: does not count
    assert uncommitted_paths(repo) == []
    (repo / "one.txt").write_text("changed", encoding="utf-8")
    assert uncommitted_paths(repo) == ["one.txt"]


def test_uncommitted_paths_skips_ignored_paths_and_non_repos(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _git_commit(repo, "one.txt", "one", "T1: first item")
    # The progress file is committed with each item and edited after every one, so it never counts as dirty work.
    _git_commit(repo, "PROGRESS-sup.md", "- [ ] T1. first item\n", "T2: tick the progress file")
    (repo / "PROGRESS-sup.md").write_text("- [x] T1. first item\n", encoding="utf-8")
    (repo / "one.txt").write_text("changed", encoding="utf-8")
    assert uncommitted_paths(repo, ignore=("PROGRESS-sup.md",)) == ["one.txt"]
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("x", encoding="utf-8")
    # git cannot answer in a directory that is not a repository; the finish check must count it clean.
    assert uncommitted_paths(plain) == []


def test_finished_clean_repo_returns_zero_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _git_dirty_repo(tmp_path)
    clock = Clock()
    calls = {"n": 0}
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    # A clean finished tree ends the run at once; no extra session is launched.
    assert code == 0
    assert launches == [_expected_message(run_dir)]
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_finished_dirty_repo_gets_exactly_one_more_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _git_dirty_repo(tmp_path)
    (config.repo / "one.txt").write_text("changed", encoding="utf-8")  # ticked work left uncommitted
    clock = Clock()
    calls = {"n": 0}
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        if calls["n"] == 5:
            # The extra session commits the work of the last ticked item.
            _git_commit(cfg.repo, "one.txt", "changed", f"{cfg.prefix}1: first item")
        return calls["n"] in (2, 3, 5)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert len(launches) == 2
    assert (
        launches[1]
        == "All items are ticked in PROGRESS-sup.md but its work is not committed. "
        "Commit the work of the last ticked item now: stage only that item's files and "
        "PROGRESS-sup.md, then stop."
    )
    assert logs == [
        "session 1 ended: 2/2 done, 0 blocked",
        "every item is ticked but the repo has uncommitted changes: one.txt",
    ]


def test_extra_session_that_starts_slowly_is_waited_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _git_dirty_repo(tmp_path)
    (config.repo / "one.txt").write_text("changed", encoding="utf-8")  # ticked work left uncommitted
    clock = Clock()
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        if calls["n"] == 6:
            _git_commit(cfg.repo, "one.txt", "changed", f"{cfg.prefix}1: first item")
        # poll 5 is the extra session not showing up yet: a slow start, not an ended session
        return calls["n"] in (2, 3, 6)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert "still uncommitted after one more session; a human should look" not in logs


def test_still_dirty_after_extra_session_returns_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _git_dirty_repo(tmp_path)
    (config.repo / "one.txt").write_text("changed", encoding="utf-8")  # ticked work left uncommitted
    clock = Clock()
    calls = {"n": 0}
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3, 5)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    # The extra session ran but committed nothing; the supervisor gives up with a human-facing line.
    assert code == 1
    assert len(launches) == 2
    assert logs[-1] == "still uncommitted after one more session; a human should look"


def test_untracked_file_alone_does_not_count_as_dirty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, run_dir = _git_dirty_repo(tmp_path)
    (config.repo / "stray.txt").write_text("new", encoding="utf-8")  # untracked only
    clock = Clock()
    calls = {"n": 0}
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (cfg.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    # Untracked lines do not count as uncommitted work; the run ends at once with no extra session.
    assert code == 0
    assert launches == [_expected_message(run_dir)]
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]

