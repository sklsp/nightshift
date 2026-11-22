"""End to end test: supervise drives the fake agent from launch to every item done.

This is the one test in the suite that runs a real supervisor against a real
(subprocess) agent, so it also proves the pieces fit together on this machine:
config loading, launching, status polling, progress reading and lock release.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from nightshift.config import load_config
from nightshift.git import commit_log, git_dir
from nightshift.progress import read_progress
from nightshift.supervise import supervise

_FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

_ITEMS_MD = """\
# Items for the end to end test

## first item
Do the first thing and tick it.

## second item
Do the second thing and tick it.

## third item
Do the third thing and tick it.
"""

_PROGRESS_UNTICKED = "- [ ] E1. first item\n- [ ] E2. second item\n- [ ] E3. third item\n"


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _run_toml(repo: Path) -> str:
    """The run.toml for this test; paths are posix so the TOML stays valid on Windows."""
    exe = Path(sys.executable).as_posix()
    agent = _FAKE_AGENT.as_posix()
    repo_path = repo.as_posix()
    status_code = f"print(open(r'{repo_path}/.fake-agent-status').read())"
    return (
        "[run]\n"
        'name = "e2e"\n'
        f'repo = "{repo_path}"\n'
        'prefix = "E"\n'
        "\n[check]\n"
        f'test = ["{exe}", "-m", "pytest"]\n'
        "\n[agent]\n"
        f'launch = ["{exe}", "{agent}", "launch", "{repo_path}", "E", "PROGRESS-e2e.md"]\n'
        f'status = ["{exe}", "-c", "{status_code}"]\n'
        'running_pattern = "running"\n'
        # poll_seconds 0 keeps the real supervisor and agent but removes the one second between polls,
        # so the run stays fast; the rate limited sleep branch is covered by test_supervise with a fake clock
        "poll_seconds = 0\n"
        "launch_grace_polls = 5\n"
        "launch_backoff_seconds = 1\n"
        "launch_ok_codes = [0]\n"
    )


def test_supervise_drives_the_fake_agent_end_to_end(git_template: Path, tmp_path: Path) -> None:
    """supervise launches the fake agent and runs until all three items are ticked."""
    repo = tmp_path / "repo"
    shutil.copytree(git_template, repo)
    (repo / "PROGRESS-e2e.md").write_text(_PROGRESS_UNTICKED, encoding="utf-8")
    # The fake agent reports itself through a status file; before the first launch it says "done",
    # which does not match running_pattern, so supervise takes that as "the agent is not running".
    (repo / ".fake-agent-status").write_text("done", encoding="utf-8")
    _git(repo, "add", "--", "PROGRESS-e2e.md", ".fake-agent-status")
    _git(repo, "commit", "-m", "e2e: initial state")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "items.md").write_text(_ITEMS_MD, encoding="utf-8")
    (run_dir / "run.toml").write_text(_run_toml(repo), encoding="utf-8")

    started = time.monotonic()
    code = supervise(load_config(run_dir), run_dir)
    elapsed = time.monotonic() - started

    assert code == 0, f"supervise exited with code {code}"
    assert elapsed < 30, f"the end to end run took {elapsed:.1f}s; it must finish within 30s"

    subjects = [subject for _, _, subject in commit_log(repo)]
    assert subjects == ["E3: third item", "E2: second item", "E1: first item", "e2e: initial state"]

    work_text = (repo / "work.txt").read_text(encoding="utf-8")
    assert work_text == "1\n2\n3\n"

    progress = read_progress(repo / "PROGRESS-e2e.md", "E")
    assert progress.total == 3 and progress.done == [1, 2, 3] and progress.blocked == []
    assert progress.finished

    git_directory = git_dir(repo)
    assert git_directory is not None, "the repo lost its .git directory"
    lock_file = git_directory / "nightshift-supervise.lock"
    assert not lock_file.is_file(), "supervise left the supervisor lock file behind"

    events_path = run_dir / ".gate" / "supervise.jsonl"
    raw_lines = events_path.read_text(encoding="utf-8").splitlines()
    messages = [json.loads(line)["msg"] for line in raw_lines if line.strip()]
    # How many sessions the fake agent needs depends on timing (a fast CI runner can see it between items and
    # count a session end); what matters is that the last session ends with every item done.
    session_ends = [m for m in messages if m.startswith("session ") and " ended: " in m]
    assert session_ends and session_ends[-1].endswith("ended: 3/3 done, 0 blocked"), session_ends
