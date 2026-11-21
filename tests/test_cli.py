"""Tests for the nightshift command line interface."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import nightshift.cli as cli_module
from nightshift.cli import main


def _make_run(tmp_path: Path, repo: Path, test: list[str] | None = None) -> Path:
    """Create a run dir whose config points at repo with prefix N."""
    if test is None:
        test = ["pytest"]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # json.dumps, not repr: a Python repr is single-quoted, and TOML single-quoted
    # strings are literal, so backslashes in Windows paths would survive unescaped.
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "st"\nrepo = "{repo.as_posix()}"\nprefix = "N"\n[check]\ntest = {json.dumps(test)}\n',
        encoding="utf-8",
    )
    return run_dir


def _queue_run(base: Path, repo: Path, n: int) -> Path:
    """Create a minimal numbered run dir for queue tests; supervise is always faked."""
    run_dir = base / f"run{n}"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "st"\nrepo = "{repo.as_posix()}"\nprefix = "N"\n[check]\ntest = ["pytest"]\n',
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
        "name",
        "total",
        "done",
        "blocked",
        "next_item",
        "next_title",
        "current_minutes",
        "gate",
        "commits",
        "events",
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
    assert payload["events"] == []
    assert payload["current_minutes"] is None


def test_status_json_fresh_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "name",
        "total",
        "done",
        "blocked",
        "next_item",
        "next_title",
        "current_minutes",
        "gate",
        "commits",
        "events",
    }
    assert payload["name"] == "st"
    assert payload["total"] == 0
    assert payload["done"] == []
    assert payload["blocked"] == []
    assert payload["next_item"] is None
    assert payload["next_title"] is None
    assert payload["gate"] == "none"
    assert payload["commits"] == []
    assert payload["current_minutes"] is None
    assert payload["events"] == []


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "nightshift 0.22.0" in capsys.readouterr().out


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


def test_gate_start_passes_extra_args_after_double_dash(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast = [sys.executable, "-c", "pass"]
    run_dir = _make_run(tmp_path, repo, test=fast)
    assert main(["gate", "start", str(run_dir), "--", "--durations=25"]) == 0
    capsys.readouterr()
    mode_lines = (run_dir / ".gate" / "mode").read_text(encoding="utf-8").splitlines()
    assert mode_lines[1] == f"{fast[0]} -c pass --durations=25"


def test_gate_start_without_double_dash_keeps_the_test_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast = [sys.executable, "-c", "pass"]
    run_dir = _make_run(tmp_path, repo, test=fast)
    assert main(["gate", "start", str(run_dir)]) == 0
    capsys.readouterr()
    mode_lines = (run_dir / ".gate" / "mode").read_text(encoding="utf-8").splitlines()
    assert mode_lines[1] == f"{fast[0]} -c pass"


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


def _make_old_run(base: Path) -> Path:
    """Create a finished-looking run dir with comments, to copy from with init --from."""
    old = base / "old-run"
    old.mkdir()
    (old / "run.toml").write_text(
        "# copied from the last run\n"
        "[run]\n"
        'name = "old"   # short id for PROGRESS-<name>.md\n'
        'repo = ".."\n'
        'branch = "feature-old"  # keep me out of main\n'
        'prefix = "N"\n'
        '# templates = "../shared/templates"\n'
        "[check]\n"
        'test = ["pytest"]\n',
        encoding="utf-8",
    )
    (old / "items.md").write_text(
        "# Items\n\n## first item\nDo the old thing.\nMore detail line.\n\n## second item\nDo another old thing.\n",
        encoding="utf-8",
    )
    return old


def test_init_from_replaces_name_and_branch_keeps_comments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = _make_old_run(tmp_path)
    new_dir = tmp_path / "new-run"
    assert main(["init", str(new_dir), "--from", str(old), "--name", "fresh", "--branch", "main"]) == 0
    out = capsys.readouterr().out
    assert "run.toml" in out and "items.md" in out
    expected = (
        "# copied from the last run\n"
        "[run]\n"
        'name = "fresh"   # short id for PROGRESS-<name>.md\n'
        'repo = ".."\n'
        'branch = "main"  # keep me out of main\n'
        'prefix = "N"\n'
        '# templates = "../shared/templates"\n'
        "[check]\n"
        'test = ["pytest"]\n'
    )
    assert (new_dir / "run.toml").read_text(encoding="utf-8") == expected


def test_init_from_without_flags_keeps_run_toml_byte_for_byte(tmp_path: Path) -> None:
    old = _make_old_run(tmp_path)
    new_dir = tmp_path / "new-run"
    assert main(["init", str(new_dir), "--from", str(old)]) == 0
    assert (new_dir / "run.toml").read_text(encoding="utf-8") == (old / "run.toml").read_text(encoding="utf-8")


def test_init_from_adds_missing_branch_after_name(tmp_path: Path) -> None:
    old = tmp_path / "old-run"
    old.mkdir()
    (old / "run.toml").write_text('[run]\nname = "old"\nrepo = ".."\n[check]\ntest = ["pytest"]\n', encoding="utf-8")
    new_dir = tmp_path / "new-run"
    assert main(["init", str(new_dir), "--from", str(old), "--branch", "main"]) == 0
    lines = (new_dir / "run.toml").read_text(encoding="utf-8").splitlines()
    name_at = next(i for i, line in enumerate(lines) if line.startswith("name"))
    assert lines[name_at + 1] == 'branch = "main"'


def test_init_from_replaces_base_keeps_comments(tmp_path: Path) -> None:
    old = tmp_path / "old-run"
    old.mkdir()
    (old / "run.toml").write_text(
        "[run]\n"
        'name = "old"\n'
        'branch = "feature-old"\n'
        'base = "origin/old-main"  # keep me out of main\n'
        '[check]\ntest = ["pytest"]\n',
        encoding="utf-8",
    )
    new_dir = tmp_path / "new-run"
    assert main(["init", str(new_dir), "--from", str(old), "--base", "origin/main"]) == 0
    expected = (
        "[run]\n"
        'name = "old"\n'
        'branch = "feature-old"\n'
        'base = "origin/main"  # keep me out of main\n'
        '[check]\ntest = ["pytest"]\n'
    )
    assert (new_dir / "run.toml").read_text(encoding="utf-8") == expected


def test_init_from_adds_missing_base_after_branch(tmp_path: Path) -> None:
    old = tmp_path / "old-run"
    old.mkdir()
    (old / "run.toml").write_text(
        '[run]\nname = "old"\nbranch = "feature-old"\nrepo = ".."\n[check]\ntest = ["pytest"]\n', encoding="utf-8"
    )
    new_dir = tmp_path / "new-run"
    assert main(["init", str(new_dir), "--from", str(old), "--base", "origin/main"]) == 0
    lines = (new_dir / "run.toml").read_text(encoding="utf-8").splitlines()
    branch_at = next(i for i, line in enumerate(lines) if line.startswith("branch"))
    assert lines[branch_at + 1] == 'base = "origin/main"'


def test_init_from_rewrites_relative_templates_for_non_sibling(tmp_path: Path) -> None:
    templates = tmp_path / "shared" / "templates"
    templates.mkdir(parents=True)
    old = tmp_path / "old-run"
    old.mkdir()
    (old / "run.toml").write_text(
        '[run]\nname = "old"\nrepo = ".."\ntemplates = "../shared/templates"\n[check]\ntest = ["pytest"]\n',
        encoding="utf-8",
    )
    new_dir = tmp_path / "elsewhere" / "new-run"
    assert main(["init", str(new_dir), "--from", str(old)]) == 0
    data = tomllib.loads((new_dir / "run.toml").read_text(encoding="utf-8"))
    value = data["run"]["templates"]
    assert not Path(value).is_absolute()
    assert (new_dir / value).resolve() == templates.resolve()


def test_init_from_writes_skeleton_items(tmp_path: Path) -> None:
    old = _make_old_run(tmp_path)
    new_dir = tmp_path / "new-run"
    assert main(["init", str(new_dir), "--from", str(old)]) == 0
    text = (new_dir / "items.md").read_text(encoding="utf-8")
    assert text.count("\n## ") == 1
    assert "Do the old thing." not in text and "second item" not in text


def test_init_from_refuses_existing_run_toml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    new_dir = tmp_path / "new-run"
    new_dir.mkdir()
    (new_dir / "run.toml").write_text("[run]\n", encoding="utf-8")
    assert main(["init", str(new_dir), "--from", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "run.toml" in err


def test_init_from_refuses_existing_items_md(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    new_dir = tmp_path / "new-run"
    new_dir.mkdir()
    (new_dir / "items.md").write_text("# Items\n", encoding="utf-8")
    assert main(["init", str(new_dir), "--from", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "items.md" in err


def test_init_from_missing_old_config_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = tmp_path / "old"
    old.mkdir()
    (old / "items.md").write_text("# Items\n", encoding="utf-8")
    assert main(["init", str(tmp_path / "new"), "--from", str(old)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "no run.toml" in err


def test_init_name_without_from_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", str(tmp_path / "new"), "--name", "x"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "only work with --from" in err


def test_init_base_without_from_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", str(tmp_path / "new"), "--base", "origin/main"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "only work with --from" in err


def test_init_example_test_command_has_no_q(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["init", str(run_dir)]) == 0
    data = tomllib.loads((run_dir / "run.toml").read_text(encoding="utf-8"))
    # the example must not add -q when the project pytest config already sets it (docs/LESSONS.md)
    assert data["check"]["test"] == ["pytest"]


def test_init_example_contains_commented_templates_key(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["init", str(run_dir)]) == 0
    text = (run_dir / "run.toml").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "templates" in line]
    assert len(lines) == 1
    assert lines[0].startswith("# templates = ")
    data = tomllib.loads(text)
    assert "templates" not in data["run"]


def test_init_example_contains_commented_launch_ok_codes_key(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["init", str(run_dir)]) == 0
    text = (run_dir / "run.toml").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "launch_ok_codes" in line]
    assert len(lines) == 1
    assert lines[0].startswith("# launch_ok_codes = ")
    data = tomllib.loads(text)
    assert "launch_ok_codes" not in data["agent"]


def test_init_example_contains_commented_protect_key(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    assert main(["init", str(run_dir)]) == 0
    text = (run_dir / "run.toml").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "protect" in line]
    assert len(lines) == 1
    assert lines[0].startswith("# protect = ")
    data = tomllib.loads(text)
    assert "protect" not in data["run"]


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


def _write_events(run_dir: Path, lines: list[str]) -> None:
    """Write .gate/supervise.jsonl with the given raw lines."""
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir(exist_ok=True)
    (gate_dir / "supervise.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seven_event_lines() -> list[str]:
    """Seven supervisor event lines, one per minute from 23:00."""
    return [json.dumps({"t": f"2026-11-15T23:{i:02d}:00", "msg": f"event {i + 1}"}) for i in range(7)]


def test_status_prints_last_five_supervisor_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    _write_events(run_dir, _seven_event_lines())

    assert main(["status", str(run_dir)]) == 0
    lines = capsys.readouterr().out.splitlines()
    # Of the seven events only the last five show, after everything status printed before.
    assert lines[-6:] == [
        "Supervisor:",
        "  23:02:00 event 3",
        "  23:03:00 event 4",
        "  23:04:00 event 5",
        "  23:05:00 event 6",
        "  23:06:00 event 7",
    ]


def test_status_json_includes_last_five_supervisor_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    _write_events(run_dir, _seven_event_lines())

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"] == [
        {"t": "2026-11-15T23:02:00", "msg": "event 3"},
        {"t": "2026-11-15T23:03:00", "msg": "event 4"},
        {"t": "2026-11-15T23:04:00", "msg": "event 5"},
        {"t": "2026-11-15T23:05:00", "msg": "event 6"},
        {"t": "2026-11-15T23:06:00", "msg": "event 7"},
    ]


def test_status_without_events_prints_nothing_extra(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)

    assert main(["status", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "Supervisor:" not in out

    assert main(["status", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["events"] == []


def test_status_skips_bad_supervisor_event_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = _make_run(tmp_path, repo)
    lines = _seven_event_lines()
    lines[2] = "{not json"
    _write_events(run_dir, lines)

    assert main(["status", str(run_dir)]) == 0
    out_lines = capsys.readouterr().out.splitlines()
    # The bad line is skipped; of the six good events only the last five show.
    assert out_lines[-6:] == [
        "Supervisor:",
        "  23:01:00 event 2",
        "  23:03:00 event 4",
        "  23:04:00 event 5",
        "  23:05:00 event 6",
        "  23:06:00 event 7",
    ]


def _make_doctor_run(tmp_path: Path, repo: Path, branch: str | None) -> Path:
    """Create a run dir that passes every doctor check except possibly the branch one."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    toml = f'[run]\nname = "st"\nrepo = "{repo.as_posix()}"\nprefix = "N"\n'
    if branch is not None:
        toml += f'branch = "{branch}"\n'
    toml += f"[check]\ntest = [{sys.executable!r}]\n"
    (run_dir / "run.toml").write_text(toml, encoding="utf-8")
    (run_dir / "items.md").write_text("# Items\n\n## first item\nDo the thing and tick it.\n", encoding="utf-8")
    return run_dir


def test_doctor_reports_branch_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-q", "-b", "main")

    run_dir = _make_doctor_run(tmp_path, repo, branch="main")

    assert main(["doctor", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "ok: branch" in out


def test_doctor_reports_branch_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-q", "-b", "feature")

    run_dir = _make_doctor_run(tmp_path, repo, branch="main")

    assert main(["doctor", str(run_dir)]) == 1
    out = capsys.readouterr().out
    assert "problem: branch: repo is on branch feature, this run needs main" in out


def test_doctor_skips_branch_check_when_unset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    run_dir = _make_doctor_run(tmp_path, repo, branch=None)

    assert main(["doctor", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "ok: branch" not in out
    assert "problem: branch" not in out


def test_queue_runs_every_run_when_all_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = _queue_run(tmp_path, repo, 1)
    second = _queue_run(tmp_path, repo, 2)
    ran: list[Path] = []

    def fake_supervise(config: object, run_dir: Path) -> int:
        ran.append(run_dir)
        return 0

    monkeypatch.setattr(cli_module, "supervise", fake_supervise)

    assert main(["queue", str(first), str(second)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert ran == [first, second]
    assert out[-3:] == [
        f"queue: {first} finished with 0",
        f"queue: {second} finished with 0",
        "queue: all 2 runs finished",
    ]


def test_queue_stops_at_the_first_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = _queue_run(tmp_path, repo, 1)
    second = _queue_run(tmp_path, repo, 2)
    third = _queue_run(tmp_path, repo, 3)
    codes = iter([0, 1])
    ran: list[Path] = []

    def fake_supervise(config: object, run_dir: Path) -> int:
        ran.append(run_dir)
        return next(codes)

    monkeypatch.setattr(cli_module, "supervise", fake_supervise)

    assert main(["queue", str(first), str(second), str(third)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert ran == [first, second]
    assert out[-2:] == [
        f"queue: {first} finished with 0",
        f"queue: {second} finished with 1",
    ]
    assert "all 3 runs finished" not in out


def test_queue_missing_run_dir_stops_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = _queue_run(tmp_path, repo, 1)
    missing = tmp_path / "nope"
    ran: list[Path] = []

    def fake_supervise(config: object, run_dir: Path) -> int:
        ran.append(run_dir)
        return 0

    monkeypatch.setattr(cli_module, "supervise", fake_supervise)

    assert main(["queue", str(first), str(missing)]) == 2
    err = capsys.readouterr().err
    assert not ran
    assert f"error: {missing} does not exist or has no run.toml" in err


def _unblock_run(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo plus a run dir whose progress file has one blocked item."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    (repo / "PROGRESS-st.md").write_text(
        "- [x] N1. first item\n- [ ] N2. second item BLOCKED\n- [ ] N3. third item\n", encoding="utf-8"
    )
    run_dir = _make_run(tmp_path, repo)
    return repo, run_dir


def test_unblock_removes_the_marker_and_prints_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _unblock_run(tmp_path)

    assert main(["unblock", str(run_dir), "N2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["- [ ] N2. second item"]
    lines = (repo / "PROGRESS-st.md").read_text(encoding="utf-8").splitlines()
    assert "- [ ] N2. second item" in lines
    assert "- [x] N1. first item" in lines
    assert "- [ ] N3. third item" in lines


def test_unblock_accepts_a_bare_number(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repo, run_dir = _unblock_run(tmp_path)

    assert main(["unblock", str(run_dir), "2"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["- [ ] N2. second item"]


def test_unblock_refuses_while_a_live_supervisor_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _unblock_run(tmp_path)
    lock_file = repo / ".git" / "nightshift-supervise.lock"
    lock_file.write_text(json.dumps({"pid": os.getpid(), "run": str(run_dir)}), encoding="utf-8")

    assert main(["unblock", str(run_dir), "N2"]) == 1
    err = capsys.readouterr().err
    assert "a live supervisor holds the repo lock; refusing to unblock" in err
    lines = (repo / "PROGRESS-st.md").read_text(encoding="utf-8").splitlines()
    assert "- [ ] N2. second item BLOCKED" in lines


def test_unblock_missing_progress_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _unblock_run(tmp_path)
    (repo / "PROGRESS-st.md").unlink()

    assert main(["unblock", str(run_dir), "N2"]) == 1
    err = capsys.readouterr().err
    assert "PROGRESS-st.md is missing" in err


def test_unblock_item_not_on_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repo, run_dir = _unblock_run(tmp_path)

    assert main(["unblock", str(run_dir), "N9"]) == 1
    err = capsys.readouterr().err
    assert "item N9 is not on PROGRESS-st.md" in err


def test_unblock_item_not_blocked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repo, run_dir = _unblock_run(tmp_path)

    assert main(["unblock", str(run_dir), "N3"]) == 1
    err = capsys.readouterr().err
    assert "item N3 is not marked BLOCKED" in err


def test_unblock_bad_item_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _repo, run_dir = _unblock_run(tmp_path)

    assert main(["unblock", str(run_dir), "nope"]) == 1
    err = capsys.readouterr().err
    assert "item must be a number or prefix plus number" in err
