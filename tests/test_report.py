"""Tests for the nightshift report command."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from nightshift.cli import main


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _short_hash(repo: Path, ref: str) -> str:
    """Return the short hash of one revision in repo."""
    result = subprocess.run(["git", "rev-parse", "--short", ref], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _dated_commit(repo: Path, filename: str, content: str, when: str, message: str) -> None:
    """Commit one file in repo with fixed author and committer dates."""
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    env = os.environ | {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    result = subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"git commit failed: {result.stderr}"


def _run_dir(
    tmp_path: Path, items_md: str, progress_md: str | None = None, protect: list[str] | None = None
) -> tuple[Path, Path]:
    """Create a fresh git repo and a run dir pointing at it; return (repo, run_dir)."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "report@example.com")
    _git(repo, "config", "user.name", "Report Test")
    if progress_md is not None:
        (repo / "PROGRESS-rep.md").write_text(progress_md, encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    protect_line = ""
    if protect is not None:
        values = ", ".join(f'"{value}"' for value in protect)
        protect_line = f"protect = [{values}]\n"
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "rep"\nrepo = "{repo.as_posix()}"\nprefix = "T"\n{protect_line}[check]\ntest = []\n',
        encoding="utf-8",
    )
    (run_dir / "items.md").write_text(items_md, encoding="utf-8")
    return repo, run_dir


_ITEMS_MD = """# Items for the rep run

## first item
Do the first thing.

## second item
Do the second thing.

## third item
Do the third thing.
"""

_PROGRESS_MD = (
    "# nightshift run rep progress\n\n- [x] T1. first item\n- [x] T2. second item\n- [ ] T3. third item BLOCKED\n"
)


def _event(stamp: str, msg: str) -> str:
    """One supervise.jsonl line."""
    return json.dumps({"t": stamp, "msg": msg})


def _write_supervise_log(run_dir: Path, lines: list[str]) -> None:
    """Write .gate/supervise.jsonl with the given raw lines."""
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir(exist_ok=True)
    (gate_dir / "supervise.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_report_lists_items_with_commits_and_blocked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _ITEMS_MD, _PROGRESS_MD)
    # a root commit before the run's range; the report must not list it as an item
    _dated_commit(repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(repo, "one.txt", "one\n", "2026-11-15T23:00:00", "T1: first item")
    _dated_commit(repo, "two.txt", "two\n", "2026-11-15T23:38:00", "T2: second item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 2/3 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD~1')}\n"
        f"- T2 second item: {_short_hash(repo, 'HEAD')} (38 min)\n"
        "- T3 third item: BLOCKED\n"
        "run time: 38 min (first to last item commit)\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_without_commits_says_not_committed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n\n## second item\nDo that.\n",
        "- [ ] T1. first item\n- [ ] T2. second item\n",
    )

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 0/2 items\n"
        "- T1 first item: not committed\n"
        "- T2 second item: not committed\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_out_writes_file_instead_of_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    out_file = tmp_path / "report.md"

    code = main(["report", str(run_dir), "--out", str(out_file)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert out_file.read_text(encoding="utf-8") == (
        "# rep: 1/1 items\n" "- T1 first item: not committed\n" "\n## Review\n- no warnings\n"
    )


def test_report_includes_last_gate_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "log").write_text("collected 3 items\n3 passed in 0.12s\n", encoding="utf-8")
    (gate_dir / "done").write_text("0\n", encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        "- T1 first item: not committed\n"
        "last gate: exit 0: 3 passed in 0.12s\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_appends_bugs_and_ideas_sections(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    (run_dir / "BUGS.md").write_text("# Bugs\n\ncrash on empty input\nslow gate wait\n", encoding="utf-8")
    (run_dir / "IDEAS.md").write_text("# Ideas\nAdd a watch mode.\n", encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        "- T1 first item: not committed\n"
        "\n## Review\n- no warnings\n"
        "\n"
        "## Bugs found\n"
        "crash on empty input\n"
        "slow gate wait\n"
        "\n"
        "## Ideas\n"
        "Add a watch mode.\n"
    )


def test_report_empty_notes_files_give_no_sections(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    (run_dir / "BUGS.md").write_text("", encoding="utf-8")
    (run_dir / "IDEAS.md").write_text("# Ideas\n\n", encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n" "- T1 first item: not committed\n" "\n## Review\n- no warnings\n"
    )


def test_report_shows_hours_for_longer_durations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        _ITEMS_MD,
        "# rep\n\n- [x] T1. first item\n- [x] T2. second item\n- [x] T3. third item\n",
    )
    _dated_commit(repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(repo, "one.txt", "one\n", "2026-11-15T23:00:00", "T1: first item")
    _dated_commit(repo, "two.txt", "two\n", "2026-11-15T23:38:00", "T2: second item")
    _dated_commit(repo, "three.txt", "three\n", "2026-11-16T00:50:00", "T3: third item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 3/3 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD~2')}\n"
        f"- T2 second item: {_short_hash(repo, 'HEAD~1')} (38 min)\n"
        f"- T3 third item: {_short_hash(repo, 'HEAD')} (1 h 12 min)\n"
        "run time: 1 h 50 min (first to last item commit)\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_single_commit_has_no_run_time(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n\n## second item\nDo that.\n",
        "- [x] T1. first item\n- [ ] T2. second item\n",
    )
    _dated_commit(repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(repo, "one.txt", "one\n", "2026-11-15T23:00:00", "T1: first item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        f"# rep: 1/2 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD')}\n"
        "- T2 second item: not committed\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_skips_blocked_items_for_durations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        _ITEMS_MD,
        "# rep\n\n- [x] T1. first item\n- [ ] T2. second item BLOCKED\n- [x] T3. third item\n",
    )
    _dated_commit(repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(repo, "one.txt", "one\n", "2026-11-15T23:00:00", "T1: first item")
    _dated_commit(repo, "three.txt", "three\n", "2026-11-15T23:38:00", "T3: third item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 2/3 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD~1')}\n"
        "- T2 second item: BLOCKED\n"
        f"- T3 third item: {_short_hash(repo, 'HEAD')} (38 min)\n"
        "run time: 38 min (first to last item commit)\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_supervisor_section_from_mixed_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    (run_dir / "BUGS.md").write_text("# Bugs\n\ncrash on empty input\n", encoding="utf-8")
    _write_supervise_log(
        run_dir,
        [
            _event("2026-11-15T23:00:00", "session 1 ended: 0/1 done, 0 blocked"),
            _event("2026-11-15T23:05:00", "agent did not start"),
            _event("2026-11-15T23:10:00", "session 2 ended: 1/1 done, 0 blocked"),
            _event(
                "2026-11-15T23:15:00",
                "agent looks stuck: no commit and no file change for 60 minutes",
            ),
            _event("2026-11-15T23:20:00", "session 3 ended: 1/1 done, 0 blocked"),
            _event("2026-11-15T23:25:00", "agent did not start"),
            _event(
                "2026-11-15T23:30:00",
                "agent status unknown for 10 polls; treating the session as ended",
            ),
        ],
    )

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        "- T1 first item: not committed\n"
        "\n## Review\n- no warnings\n"
        "\n"
        "## Supervisor\n"
        "- 3 sessions\n"
        "- 2 failed starts\n"
        "- 1 stuck report\n"
        "- 1 unreadable-status give-up\n"
        "\n"
        "## Bugs found\n"
        "crash on empty input\n"
    )


def test_report_supervisor_plural_labels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    lines = []
    for i in range(2):
        stamp = f"2026-11-15T23:{i * 4:02d}:00"
        lines.append(_event(stamp, f"session {i + 1} ended: 0/1 done, 0 blocked"))
        lines.append(_event(f"2026-11-15T23:{i * 4 + 1:02d}:00", "agent did not start"))
        lines.append(
            _event(
                f"2026-11-15T23:{i * 4 + 2:02d}:00",
                "agent looks stuck: no commit and no file change for 60 minutes",
            )
        )
        lines.append(
            _event(
                f"2026-11-15T23:{i * 4 + 3:02d}:00",
                "agent status unknown for 10 polls; treating the session as ended",
            )
        )
    _write_supervise_log(run_dir, lines)

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        "- T1 first item: not committed\n"
        "\n## Review\n- no warnings\n"
        "\n"
        "## Supervisor\n"
        "- 2 sessions\n"
        "- 2 failed starts\n"
        "- 2 stuck reports\n"
        "- 2 unreadable-status give-ups\n"
    )


def test_report_without_supervise_log_has_no_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Supervisor" not in out


def test_report_supervise_log_with_only_other_events_gives_no_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    _write_supervise_log(
        run_dir,
        [
            _event("2026-11-15T23:00:00", "agent launch exited with code 1"),
            "{not json",
            _event("2026-11-15T23:05:00", "stopped after 20 sessions without finishing; a human should look"),
        ],
    )

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Supervisor" not in out


def test_report_reads_gate_copy_when_repo_progress_removed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, _ITEMS_MD, _PROGRESS_MD)
    (repo / "PROGRESS-rep.md").unlink()  # cleanup removed the repo file
    gate_dir = run_dir / ".gate"
    gate_dir.mkdir()
    (gate_dir / "progress.md").write_text(_PROGRESS_MD, encoding="utf-8")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 2/3 items\n"
        "- T1 first item: not committed\n"
        "- T2 second item: not committed\n"
        "- T3 third item: BLOCKED\n"
        "\n## Review\n- no warnings\n"
    )


def test_report_without_any_progress_says_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, run_dir = _run_dir(tmp_path, "# Items for the rep run\n\n## first item\nDo it.\n", None)

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep\nprogress file missing\n- T1 first item: not committed\n" "\n## Review\n- no warnings\n"
    )


def test_report_shows_only_its_own_commits_when_prefix_is_reused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "night@test")
    _git(repo, "config", "user.name", "Night")
    _dated_commit(repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")

    # run a worked on its own branch forked from main; later run b reused the same prefix on main
    _git(repo, "checkout", "-q", "-b", "run-a")
    _dated_commit(repo, "a.txt", "A\n", "2026-11-15T23:00:00", "T1: first item")
    a_hash = _short_hash(repo, "HEAD")
    _git(repo, "checkout", "-q", "main")
    _dated_commit(repo, "b.txt", "B\n", "2026-11-15T23:30:00", "T1: first item")
    b_hash = _short_hash(repo, "HEAD")

    def _run_dir_for(name: str, branch: str, base: str = "") -> Path:
        run_dir = tmp_path / f"run-{name}"
        run_dir.mkdir()
        branch_line = f'branch = "{branch}"\n' if branch else ""
        branch_line += f'base = "{base}"\n' if base else ""
        (run_dir / "run.toml").write_text(
            f'[run]\nname = "{name}"\nrepo = "{repo.as_posix()}"\n{branch_line}prefix = "T"\n[check]\ntest = []\n',
            encoding="utf-8",
        )
        (run_dir / "items.md").write_text("# Items\n\n## first item\nDo it.\n", encoding="utf-8")
        return run_dir

    run_a = _run_dir_for("a", "run-a", base="main")  # worked on run-a, forked from main
    run_b = _run_dir_for("b", "")  # ran directly on main
    (repo / "PROGRESS-a.md").write_text("- [x] T1. first item\n", encoding="utf-8")
    (repo / "PROGRESS-b.md").write_text("- [x] T1. first item\n", encoding="utf-8")

    _git(repo, "checkout", "-q", "run-a")
    assert main(["report", str(run_a)]) == 0
    out = capsys.readouterr().out
    # run b's same-prefix commit must not show up in run a's report
    assert out == f"# a: 1/1 items\n- T1 first item: {a_hash}\n" "\n## Review\n- no warnings\n"

    _git(repo, "checkout", "-q", "main")
    assert main(["report", str(run_b)]) == 0
    out = capsys.readouterr().out
    # run a's same-prefix commit must not show up in run b's report
    assert out == f"# b: 1/1 items\n- T1 first item: {b_hash}\n" "\n## Review\n- no warnings\n"


def test_report_review_section_lists_warnings_by_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n\n## second item\nDo that.\n",
        "- [x] T1. first item\n- [x] T2. second item\n",
        protect=[".github/"],
    )
    (repo / "tests").mkdir()
    (repo / ".github").mkdir()
    three_tests = "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n\n\ndef test_three():\n    pass\n"
    two_tests = "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
    _dated_commit(repo, "tests/test_app.py", three_tests, "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(repo, ".github/ci.yml", "jobs: {}\n", "2026-11-15T23:00:00", "T1: first item")
    _dated_commit(repo, "tests/test_app.py", two_tests, "2026-11-15T23:38:00", "T2: second item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 2/2 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD~1')}\n"
        f"- T2 second item: {_short_hash(repo, 'HEAD')} (38 min)\n"
        "run time: 38 min (first to last item commit)\n"
        "\n## Review\n- T1: touches protected .github/ci.yml\n- T2: fewer tests in tests/test_app.py (3 -> 2)\n"
    )


def test_report_review_section_says_no_warnings_when_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, run_dir = _run_dir(
        tmp_path,
        "# Items for the rep run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    _dated_commit(repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(repo, "one.txt", "one\n", "2026-11-15T23:00:00", "T1: first item")

    code = main(["report", str(run_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "# rep: 1/1 items\n"
        f"- T1 first item: {_short_hash(repo, 'HEAD')}\n"
        "\n## Review\n- no warnings\n"
    )

