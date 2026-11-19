"""Tests for the nightshift history command."""

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


def _dated_commit(repo: Path, filename: str, content: str, when: str, message: str) -> None:
    """Commit one file in repo with fixed author and committer dates."""
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    env = os.environ | {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    result = subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"git commit failed: {result.stderr}"


def _run_dir(tmp_path: Path, name: str, items_md: str, progress_md: str) -> tuple[Path, Path]:
    """Create a fresh git repo and a run dir named name pointing at it; return (repo, run_dir)."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    repo = tmp_path / f"repo-{name}"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "history@example.com")
    _git(repo, "config", "user.name", "History Test")
    (repo / f"PROGRESS-{name}.md").write_text(progress_md, encoding="utf-8")
    run_dir = tmp_path / f"run-{name}"
    run_dir.mkdir()
    (run_dir / "run.toml").write_text(
        f'[run]\nname = "{name}"\nrepo = "{repo.as_posix()}"\nprefix = "T"\n[check]\ntest = []\n',
        encoding="utf-8",
    )
    (run_dir / "items.md").write_text(items_md, encoding="utf-8")
    return repo, run_dir


def _event(stamp: str, msg: str) -> str:
    """One supervise.jsonl line."""
    return json.dumps({"t": stamp, "msg": msg})


def test_history_prints_one_row_per_run_and_a_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alpha_repo, alpha_dir = _run_dir(
        tmp_path,
        "alpha",
        "# Items for the alpha run\n\n## first item\nDo it.\n\n## second item\nDo that.\n",
        "- [x] T1. first item\n- [x] T2. second item\n",
    )
    _dated_commit(alpha_repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(alpha_repo, "one.txt", "one\n", "2026-11-15T23:00:00", "T1: first item")
    _dated_commit(alpha_repo, "two.txt", "two\n", "2026-11-15T23:38:00", "T2: second item")
    (alpha_dir / ".gate").mkdir()
    (alpha_dir / ".gate" / "supervise.jsonl").write_text(
        "\n".join([_event("2026-11-15T23:00:00", "session 1 ended"), _event("2026-11-15T23:40:00", "session 2 ended")])
        + "\n",
        encoding="utf-8",
    )

    beta_repo, beta_dir = _run_dir(
        tmp_path,
        "beta",
        "# Items for the beta run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    _dated_commit(beta_repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(beta_repo, "one.txt", "one\n", "2026-11-15T23:05:00", "T1: first item")

    code = main(["history", str(alpha_dir), str(beta_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "alpha  2/2 items  38 min  2 session(s)  0 warning(s)\n"
        "beta  1/1 items  -  0 session(s)  0 warning(s)\n"
        "2 runs, 3/3 items, 38 min, 2 sessions, 0 warnings\n"
    )


def test_history_notes_missing_dirs_and_skips_them_in_the_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gamma_repo, gamma_dir = _run_dir(
        tmp_path,
        "gamma",
        "# Items for the gamma run\n\n## first item\nDo it.\n",
        "- [x] T1. first item\n",
    )
    _dated_commit(gamma_repo, "root.txt", "root\n", "2026-11-15T22:00:00", "root: initial import")
    _dated_commit(gamma_repo, "one.txt", "one\n", "2026-11-15T23:05:00", "T1: first item")
    missing = tmp_path / "no-such-run"

    code = main(["history", str(gamma_dir), str(missing)])

    out = capsys.readouterr().out
    assert code == 0
    assert out == (
        "gamma  1/1 items  -  0 session(s)  0 warning(s)\n"
        f"{missing}: missing\n"
        "1 runs, 1/1 items, -, 0 sessions, 0 warnings\n"
    )
