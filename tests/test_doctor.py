"""Tests for the nightshift doctor command."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nightshift.cli import main


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _test_words() -> list[str]:
    """A test command built from the running interpreter so doctor always finds it."""
    return [Path(sys.executable).as_posix(), "-m", "pytest", "-q"]


def _healthy_run(tmp_path: Path) -> Path:
    """Create a run dir with valid config and items pointing at a fresh git repo."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "doc"\nrepo = "{repo.as_posix()}"\n[check]\n'
        f"test = {_test_words()!r}\nwait_seconds = 25\ntimeout_hint_seconds = 30\n",
        encoding="utf-8",
    )
    (run_dir / "items.md").write_text("# Items for the doc run\n\n## one item\nDo the thing.\n", encoding="utf-8")
    return run_dir


def _rewrite_toml(run_dir: Path, old: str, new: str) -> None:
    """Replace one line of the run's run.toml."""
    path = run_dir / "run.toml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_doctor_healthy_run_is_all_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "ok: run.toml",
        "ok: items.md",
        "ok: repo",
        "ok: test command",
        "ok: timing",
    ]


def test_doctor_full_config_names_every_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    exe = Path(sys.executable).as_posix()
    _rewrite_toml(run_dir, "wait_seconds = 25", f"lint = [{exe!r}]\nwait_seconds = 25")
    _rewrite_toml(
        run_dir,
        "timeout_hint_seconds = 30\n",
        f"timeout_hint_seconds = 30\n[agent]\nlaunch = [{exe!r}]\nstatus = [{exe!r}]\n",
    )

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "ok: run.toml",
        "ok: items.md",
        "ok: repo",
        "ok: test command",
        "ok: lint command",
        "ok: agent launch",
        "ok: agent status",
        "ok: timing",
    ]


def test_doctor_broken_run_toml_reports_named_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, 'name = "doc"\n', "")

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert lines == ["problem: run.toml: missing required key 'run.name'"]


def test_doctor_broken_items_md_reports_named_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    (run_dir / "items.md").write_text("# Items\nNo headers here.\n", encoding="utf-8")

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert lines == [
        "ok: run.toml",
        "problem: items.md: no items found",
        "ok: repo",
        "ok: test command",
        "ok: timing",
    ]


def test_doctor_missing_lint_program_reports_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, "wait_seconds = 25", 'lint = ["no-such-linter-xyz"]\nwait_seconds = 25')

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "problem: lint command: install no-such-linter-xyz or put it on PATH" in lines


def test_doctor_pre_checks_are_named_and_checked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    exe = Path(sys.executable).as_posix()
    _rewrite_toml(run_dir, "wait_seconds = 25", f"pre = [[{exe!r}], ['no-such-pre-xyz']]\nwait_seconds = 25")

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "ok: pre check 1" in lines
    assert "problem: pre check 2: install no-such-pre-xyz or put it on PATH" in lines


def test_doctor_missing_test_program_reports_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, repr(_test_words()), repr(["no-such-program-xyz"]))

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "problem: test command: install no-such-program-xyz or put it on PATH" in lines


def test_doctor_missing_agent_programs_report_problems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(
        run_dir,
        "timeout_hint_seconds = 30\n",
        'timeout_hint_seconds = 30\n[agent]\nlaunch = ["no-such-launch-xyz"]\nstatus = ["no-such-status-xyz"]\n',
    )

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "problem: agent launch: install no-such-launch-xyz or put it on PATH" in lines
    assert "problem: agent status: install no-such-status-xyz or put it on PATH" in lines


def _write_templates(run_dir: Path, start_md: str = "Start $name.\n", item_md: str = "Item $id.\n") -> None:
    """Create a templates dir with both template files under the run dir."""
    tpl = run_dir / "tpl"
    tpl.mkdir()
    (tpl / "start.md").write_text(start_md, encoding="utf-8")
    (tpl / "item.md").write_text(item_md, encoding="utf-8")


def test_doctor_templates_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _write_templates(run_dir)
    _rewrite_toml(run_dir, 'name = "doc"\n', 'name = "doc"\ntemplates = "tpl"\n')

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "ok: run.toml",
        "ok: items.md",
        "ok: templates",
        "ok: repo",
        "ok: test command",
        "ok: timing",
    ]


def test_doctor_templates_missing_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, 'name = "doc"\n', 'name = "doc"\ntemplates = "no-such-dir"\n')

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert f"problem: templates: directory missing: {run_dir / 'no-such-dir'}" in lines


def test_doctor_templates_bad_placeholder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _write_templates(run_dir, start_md="Start $bogus.\n")
    _rewrite_toml(run_dir, 'name = "doc"\n', 'name = "doc"\ntemplates = "tpl"\n')

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "problem: templates: unknown placeholder $bogus in start.md" in lines


def test_doctor_wait_seconds_not_below_timeout_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _rewrite_toml(run_dir, "wait_seconds = 25", "wait_seconds = 30")

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "problem: timing: lower check.wait_seconds below check.timeout_hint_seconds" in lines


def _add_agent_stop(run_dir: Path, printed: str, stop_args: list[str]) -> None:
    """Add an [agent] section whose status prints one fixed line and stop uses the given args."""
    exe = Path(sys.executable).as_posix()
    status_cmd = [exe, "-c", f'print("{printed}")']
    agent_section = (
        "timeout_hint_seconds = 30\n"
        "[agent]\n"
        f"status = {status_cmd!r}\n"
        'session_pattern = "sessionId=([^ ]+)"\n'
        f"stop = {stop_args!r}\n"
    )
    _rewrite_toml(run_dir, "timeout_hint_seconds = 30\n", agent_section)


def test_doctor_stop_session_ok_line_names_the_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _add_agent_stop(run_dir, "sessionId=abc-123", ["fake-stop", "--session-id", "{session}"])

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "ok: run.toml",
        "ok: items.md",
        "ok: repo",
        "ok: test command",
        "ok: agent status",
        "ok   stop would end session abc-123",
        "ok: timing",
    ]


def test_doctor_stop_session_warns_when_nothing_matches(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _add_agent_stop(run_dir, "no session around", ["fake-stop", "--session-id", "{session}"])

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "ok: run.toml",
        "ok: items.md",
        "ok: repo",
        "ok: test command",
        "ok: agent status",
        "warn stop: session_pattern matches nothing in the status output",
        "ok: timing",
    ]


def test_doctor_stop_without_session_prints_no_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = _healthy_run(tmp_path)
    _add_agent_stop(run_dir, "sessionId=abc-123", ["fake-stop", "--now"])

    code = main(["doctor", str(run_dir)])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines == [
        "ok: run.toml",
        "ok: items.md",
        "ok: repo",
        "ok: test command",
        "ok: agent status",
        "ok: timing",
    ]
