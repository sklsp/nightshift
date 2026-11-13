"""Tests for the supervise loop."""

import sys
from pathlib import Path

import nightshift.agent as agent_module
from nightshift.config import RunConfig
from nightshift.supervise import supervise


class Clock:
    """A fake clock whose sleep advances time by exactly the requested seconds."""

    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


_ITEMS_MD = """\
# Items for the supervise test

## first item
Do the first thing and tick it.

## second item
Do the second thing and tick it.
"""

_UNTICKED = "- [ ] T1. first item\n- [ ] T2. second item\n"
_TICKED = "- [x] T1. first item (1 passed in 0.01s)\n- [x] T2. second item (2 passed in 0.01s)\n"


def _setup(tmp_path: Path, progress_text: str, max_sessions: int) -> tuple[RunConfig, Path]:
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
    )
    return config, run_dir


def _expected_message(run_dir: Path) -> str:
    """The exact launch message for a two item run."""
    return (
        f"Read {run_dir.as_posix()}/00-start.md and follow it. "
        "Resume at the first item that is not ticked. "
        "Do NOT call attempt_completion until item 2 is done."
    )


def test_supervise_finishes_when_progress_gets_ticked(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=5)
    clock = Clock()
    calls = {"n": 0}
    launches: list[str] = []

    def fake_is_running(cfg: RunConfig) -> bool:
        calls["n"] += 1
        if calls["n"] == 3:
            (config.repo / "PROGRESS-sup.md").write_text(_TICKED, encoding="utf-8")
        return calls["n"] in (2, 3)

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 0
    assert launches == [_expected_message(run_dir)]
    assert logs == ["session 1 ended: 2/2 done, 0 blocked"]


def test_supervise_stops_at_max_sessions(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    launches: list[str] = []

    monkeypatch.setattr(agent_module, "is_running", lambda cfg: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    assert len(launches) == 2
    assert "stopped after 2 sessions" in logs[-1]


def test_supervise_stops_after_three_empty_sessions(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=10)
    clock = Clock()
    launches: list[str] = []

    monkeypatch.setattr(agent_module, "is_running", lambda cfg: False)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: launches.append(message))

    logs: list[str] = []
    code = supervise(config, run_dir, sleep=clock.sleep, now=clock.time, log=logs.append)

    assert code == 1
    assert len(launches) == 3
    assert logs[-1] == "agent stuck: no new item done three times in a row; a human should look"


def test_supervise_does_not_launch_while_running(tmp_path: Path, monkeypatch) -> None:
    config, run_dir = _setup(tmp_path, _UNTICKED, max_sessions=2)
    clock = Clock()
    events: list[str] = []
    calls = {"n": 0}

    def fake_is_running(cfg: RunConfig) -> bool:
        calls["n"] += 1
        events.append("status")
        return calls["n"] <= 2

    monkeypatch.setattr(agent_module, "is_running", fake_is_running)
    monkeypatch.setattr(agent_module, "launch", lambda cfg, message: events.append("launch"))

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

    def fake_is_running(cfg: RunConfig) -> bool:
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
