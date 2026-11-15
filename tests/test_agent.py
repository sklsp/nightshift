"""Tests for the agent launch and status adapters."""

import sys
from pathlib import Path

import pytest

from nightshift.agent import AgentError, _format_exit_code, is_running, launch
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


def test_failed_status_check_is_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "import sys; sys.exit(3)"],
    )

    assert is_running(config) is None
    err = capsys.readouterr().err
    assert "agent status command exited with code 3" in err


def test_status_timeout_is_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import nightshift.agent as agent_module

    monkeypatch.setattr(agent_module, "_STATUS_TIMEOUT_SECONDS", 0.2)
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "import time; time.sleep(5)"],
    )

    assert is_running(config) is None
    err = capsys.readouterr().err
    assert "agent" in err.lower()


def test_missing_executable_is_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(
        tmp_path,
        status=[str(tmp_path / "no-such-agent")],
    )

    assert is_running(config) is None
    err = capsys.readouterr().err
    assert "agent" in err.lower()


def test_format_exit_code_adds_hex_only_for_large_or_negative_codes() -> None:
    assert _format_exit_code(3) == "3"
    assert _format_exit_code(0x7FFFFFFF) == "2147483647"
    assert _format_exit_code(3221225794) == "3221225794 (0xC0000142)"
    assert _format_exit_code(-1073741502) == "-1073741502 (0xC0000142)"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="POSIX truncates exit codes to 8 bits")
def test_exit_code_above_int32_prints_hex(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "import sys; sys.exit(3221225794)"],
    )

    assert is_running(config) is None
    err = capsys.readouterr().err
    assert "agent status command exited with code 3221225794 (0xC0000142)" in err


def test_launch_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match="agent.launch"):
        launch(_config(tmp_path), "hello")


def test_status_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match="agent.status"):
        is_running(_config(tmp_path))
