"""Tests for the agent launch and status adapters."""

import subprocess
import sys
from pathlib import Path

import pytest

from nightshift.agent import AgentError, format_exit_code, is_running, launch, session_id, stop
from nightshift.config import RunConfig


def _config(
    tmp_path: Path,
    launch: list[str] | None = None,
    status: list[str] | None = None,
    pattern: str = "running",
    stop_command: list[str] | None = None,
    session_pattern: str = "",
) -> RunConfig:
    """Build a config whose repo and commands live under tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return RunConfig(
        name="agent-test",
        repo=repo,
        test=[sys.executable, "-c", "print('1 passed in 0.01s')"],
        launch=list(launch or []),
        status=list(status or []),
        running_pattern=pattern,
        stop=list(stop_command or []),
        session_pattern=session_pattern,
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

    assert launch(config, "hello") == 0

    assert (config.repo / "launched.txt").read_text(encoding="utf-8") == "hello|x-hello-y"


def test_launch_returns_nonzero_exit_code(tmp_path: Path) -> None:
    config = _config(tmp_path, launch=[sys.executable, "-c", "import sys; sys.exit(7)"])

    assert launch(config, "hello") == 7


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


def test_quiet_failed_status_prints_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(
        tmp_path,
        status=[sys.executable, "-c", "import sys; sys.exit(3)"],
    )

    assert is_running(config, quiet=True) is None
    err = capsys.readouterr().err
    assert err == ""


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
    assert format_exit_code(3) == "3"
    assert format_exit_code(0x7FFFFFFF) == "2147483647"
    assert format_exit_code(3221225794) == "3221225794 (0xC0000142)"
    assert format_exit_code(-1073741502) == "-1073741502 (0xC0000142)"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="POSIX truncates exit codes to 8 bits")
def test_exit_code_above_int32_prints_hex(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _config(
        tmp_path,
        # ExitProcess takes the full 32-bit code; sys.exit with a value this large gives -1 on some Python builds
        status=[
            sys.executable,
            "-c",
            "import ctypes; k = ctypes.windll.kernel32; k.ExitProcess.argtypes = [ctypes.c_uint]; "
            "k.ExitProcess(3221225794)",
        ],
    )

    assert is_running(config) is None
    err = capsys.readouterr().err
    assert "agent status command exited with code 3221225794 (0xC0000142)" in err


def test_launch_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match=r"agent\.launch"):
        launch(_config(tmp_path), "hello")


def test_status_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentError, match=r"agent\.status"):
        is_running(_config(tmp_path))


def test_session_id_returns_group_of_first_match(tmp_path: Path) -> None:
    config = _config(tmp_path, session_pattern=r'"id":"([^"]+)"')
    output = '{"sessions":[{"id":"abc-123","status":"running"},{"id":"zzz"}]}'

    assert session_id(config, output) == "abc-123"


def test_session_id_without_match_is_none(tmp_path: Path) -> None:
    config = _config(tmp_path, session_pattern=r'"id":"([^"]+)"')

    assert session_id(config, "agent status: idle") is None


def test_session_id_on_empty_output_is_none(tmp_path: Path) -> None:
    config = _config(tmp_path, session_pattern=r'"id":"([^"]+)"')

    assert session_id(config, "") is None


def test_session_id_without_pattern_is_none(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert session_id(config, '{"id":"abc-123"}') is None


def test_stop_fills_session_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        stdout = '{"id":"abc-123","status":"running"}' if list(command) == ["fake-status"] else ""
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = _config(
        tmp_path,
        status=["fake-status"],
        stop_command=["fake-stop", "--session-id", "{session}"],
        session_pattern=r'"id":"([^"]+)"',
    )

    stop(config)

    assert calls == [["fake-status"], ["fake-stop", "--session-id", "abc-123"]]


def test_stop_skips_when_no_session_id_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        stdout = "agent status: idle" if list(command) == ["fake-status"] else ""
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = _config(
        tmp_path,
        status=["fake-status"],
        stop_command=["fake-stop", "--session-id", "{session}"],
        session_pattern=r'"id":"([^"]+)"',
    )

    stop(config)

    # The status command ran once more but the stop never did: no literal {session} reaches a process.
    assert calls == [["fake-status"]]
    err = capsys.readouterr().err
    assert "skipping stop" in err


def test_stop_with_placeholder_but_no_pattern_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = _config(tmp_path, stop_command=["fake-stop", "--session-id", "{session}"])

    stop(config)

    assert calls == []
    err = capsys.readouterr().err
    assert "skipping stop" in err


def test_stop_without_placeholder_runs_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = _config(tmp_path, status=["fake-status"], stop_command=["fake-stop", "--now"])

    stop(config)

    # No {session} in the command: it runs as before and no extra status check happens.
    assert calls == [["fake-stop", "--now"]]
