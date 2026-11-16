"""Tests for the supervise loop."""

import sys
from pathlib import Path

import nightshift.agent as agent_module
from nightshift.cli import main
from nightshift.config import RunConfig
from nightshift.supervise import supervise


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


def _setup(
    tmp_path: Path,
    progress_text: str,
    max_sessions: int,
    *,
    idle_minutes=60,
    stop=None,
    launch_backoff_seconds=60,
    max_launch_failures=6,
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
        stop=list(stop or []),
        launch_backoff_seconds=launch_backoff_seconds,
        max_launch_failures=max_launch_failures,
    )
    return config, run_dir


def _expected_message(run_dir: Path) -> str:
    """The exact launch message for a two item run."""
    return (
        f"Read {run_dir.resolve().as_posix()}/00-start.md and follow it. "
        "Resume at the first item that is not ticked. "
        "Do NOT call attempt_completion until item 2 is done."
    )


def test_supervise_finishes_when_progress_gets_ticked(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert launches == [_expected_message(run_dir)]
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_supervise_stops_at_max_sessions(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    launches: list[str] = []

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    assert len(launches) == 2
    assert "stopped after 2 sessions" in logs[-1]


def test_supervise_stops_after_three_empty_sessions(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    # Three sessions ran and ended without progress; the fourth launch never happened.
    assert len(launches) == 3
    assert logs[-1] == "agent stuck: no new item done three times in a row; a human should look"


def test_supervise_does_not_launch_while_running(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    assert events.count("launch") == 1
    # the first two status checks saw a running agent, so no relaunch happened then
    assert events.index("launch") > 2


def test_supervise_never_polls_faster_than_poll_seconds(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(gap >= config.poll_seconds - 1e-9 for gap in gaps)


def test_grace_period_waits_for_agent_to_start(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_grace_period_logs_when_agent_never_starts(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    # Every launch failed to start; the backoff doubled between sessions.
    assert logs.count("agent did not start") == 2
    assert clock.sleeps.count(60) == 1
    assert clock.sleeps.count(120) == 1


def test_failed_start_backs_off_then_the_next_session_finishes(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    # The first launch never came up and backed off; the second one finished the run.
    assert len(launches) == 2
    assert clock.sleeps.count(60) == 1


def test_repeated_failed_starts_end_with_exit_code_two(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=10)
    clock = Clock()
    launches: list[str] = []

    def fake_launch(cfg: RunConfig, message: str) -> int:
        launches.append(message)
        return 0

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", fake_launch)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 2
    # The default cap is six failed starts; the supervisor gives up and asks for a human.
    assert len(launches) == 6
    assert logs[-1] == "agent cannot be launched after 6 tries; a human should look"


def test_launch_backoff_is_capped_at_fifteen_minutes(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(
        tmp_path, _UNTICKED, max_sessions=10, launch_backoff_seconds=600, max_launch_failures=3
    )
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 2
    # The backoff doubles (600 then 1200) but never exceeds the fifteen minute cap.
    assert clock.sleeps.count(900) == 1
    assert all(s <= 900 for s in clock.sleeps)


def test_changing_fingerprint_never_triggers(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=fingerprint)

    assert code == 0
    assert not any("agent looks stuck" in line for line in logs)


def test_unchanged_fingerprint_triggers_and_runs_stop_once(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(
        tmp_path, _UNTICKED, max_sessions=5, idle_minutes=1, stop=["fake-stop"]
    )
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
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same"
    )

    assert code == 1
    # The stuck session was logged once and stopped exactly once...
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1
    assert len(stops) == 1
    # ...and every later launch failed to start, so the run ended at the session cap.
    assert logs[-1] == "stopped after 5 sessions without finishing; a human should look"


def test_unchanged_fingerprint_without_stop_only_logs(tmp_path: Path, monkeypatch) -> None:
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
        config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append, fingerprint=lambda repo: "same"
    )

    assert code == 1
    # Without a stop command the supervisor only reports and lets the session end.
    assert logs.count("agent looks stuck: no commit and no file change for 1 minutes") == 1
    assert "stopped after 2 sessions" in logs[-1]


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


def test_supervise_flushes_log_lines_through_main(tmp_path: Path, monkeypatch) -> None:
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


def test_supervise_copies_progress_to_gate_dir(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    copy = run_dir / ".gate" / "progress.md"
    assert copy.is_file()
    # The repo file was still there at session end, so it was read directly and the copy kept current.
    assert copy.read_text(encoding="utf-8") == _TICKED
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_supervise_uses_gate_copy_when_repo_progress_deleted(tmp_path: Path, monkeypatch) -> None:
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
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    # The finished state survived the cleanup because the supervisor read its own copy.
    assert (run_dir / ".gate" / "progress.md").read_text(encoding="utf-8") == _TICKED
    assert logs.count("repo progress file missing; reading the .gate copy") == 1
    assert logs[-1] == "session 1 ended: 2/2 done, 0 blocked"


def test_supervise_missing_progress_everywhere_means_not_started(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    (config.repo / "PROGRESS-sup.md").unlink()  # no repo file and no .gate copy yet
    clock = Clock()

    monkeypatch.setattr(agent_module, "is_running", lambda cfg, quiet=False: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: 0)

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    # Nothing was copied because there was nothing to copy.
    assert not (run_dir / ".gate" / "progress.md").exists()
    for line in logs:
        if line.startswith("session"):
            assert "0/2 done" in line


def test_unknown_status_keeps_session_going_and_logs_recovery(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1)
    clock = Clock()
    statuses = [None, None, True, False]
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        return statuses.pop(0)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    # The unknown polls did not end the session and did not trigger a launch; only the clean False ended it.
    assert launches == []
    assert statuses == []
    assert "agent status readable again after 2 failed polls" in logs
    assert not any("treating the session as ended" in line for line in logs)


def test_ten_unknown_statuses_end_the_session(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=1)
    clock = Clock()
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig, quiet: bool = False) -> bool | None:
        return None

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    assert launches == []
    assert logs.count("agent status unknown for 10 polls; treating the session as ended") == 1
