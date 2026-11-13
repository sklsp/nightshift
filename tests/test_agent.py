"""Tests for the agent launch and status adapters."""

import sys
from pathlib import Path

import pytest

from nightshift.agent import AgentError, is_running, launch
from nightshift.config import RunConfig


def _config(tmp_path: Path, launch=None, status=None, pattern="running") -> RunConfig:
    """Build a config whose repo and commands live under tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return RunConfig(
        name="agent-test",
        repo=repo,
        test=[sys.executable, "-c", "print('1 passed in 0.01s')"],
        launch=list(launch or []),
        status=list(status or []),
        running_pattern=pattern,
    )


def test_launch_replaces_message_placeholder(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        launch=[
            sys.executable,
            "-c",
            "import sys; open('launched.txt', 'w').write(sys.argv[1] + '|' + sys.argv[2])",
            "{message}",
            "x-{message}-y",
        ],
    )

    launch(config, "hello")

    assert (config.repo / "launched.txt").read_text(encoding="utf-8") == "hello|x-hello-y"


def test_is_running_matches_pattern(tmp_path: Path) -> None:
    payload = '{"sessions":[{"id":1,"status":"running"}]}'
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", f"print({payload!r})"],
        pattern='"status":"running"',
    )

    assert is_running(config) is True


def test_is_running_no_match(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "print('agent status: idle')"],
    )

    assert is_running(config) is False


def test_failing_status_is_not_running(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "raise SystemExit(2)"],
    )

    assert is_running(config) is False
    err = capsys.readouterr().err
    assert "agent" in err.lower()


def test_status_timeout_is_not_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import nightshift.agent as agent_module

    monkeypatch.setattr(agent_module, "_STATUS_TIMEOUT_SECONDS", 0.2)
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "import time; time.sleep(5)"],
    )

    assert is_running(config) is False
    err = capsys.readouterr().err
    assert "agent" in err.lower()


def test_launch_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match="agent.launch"):
        launch(_config(tmp_path), "hello")


def test_status_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match="agent.status"):
        is_running(_config(tmp_path))
