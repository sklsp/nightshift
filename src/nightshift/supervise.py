"""Supervise an agent: relaunch it when a session ends until every item is done.

A stuck detector exists because of a real failure: one session spent hours
re-reading the same files after its context was compacted, writing nothing.
While the agent runs, the supervisor fingerprints the repo (HEAD plus git
status) on every poll; if it does not change for idle_minutes the session is
reported as stuck and, when configured, stopped.
"""

import subprocess
import time
from pathlib import Path

from nightshift import agent
from nightshift.config import RunConfig
from nightshift.items import load_items
from nightshift.progress import read_progress


def _repo_fingerprint(repo: Path) -> str | None:
    """Return HEAD's hash plus git status porcelain text, or None when git cannot answer."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if head.returncode != 0 or status.returncode != 0:
        return None
    return f"{head.stdout.strip()}\n{status.stdout}"


def supervise(
    config: RunConfig,
    run_dir: Path,
    *,
    sleep=time.sleep,
    now=time.time,
    log=print,
    fingerprint=_repo_fingerprint,
) -> int:
    """Relaunch the agent until every item is done or a stop condition hits."""
    total = len(load_items(run_dir))
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    message = (
        f"Read {(run_dir.resolve() / '00-start.md').as_posix()} and follow it. "
        f"Resume at the first item that is not ticked. "
        f"Do NOT call attempt_completion until item {total} is done."
    )

    last_poll = now() - config.poll_seconds  # allow an immediate first check
    empty_streak = 0
    last_done = 0

    def _status() -> bool:
        """Check the agent, never faster than poll_seconds since the previous check."""
        nonlocal last_poll
        wait_for = config.poll_seconds - (now() - last_poll)
        if wait_for > 0:
            sleep(wait_for)
        last_poll = now()
        return agent.is_running(config)

    for session in range(1, config.max_sessions + 1):
        if not _status():
            agent.launch(config, message)
            started = False
            for _poll in range(config.launch_grace_polls):
                if _status():
                    started = True
                    break
            if not started:
                log("agent did not start")
        last_fp = None
        fp_since = now()
        stuck_reported = False
        while True:
            if not _status():
                break
            fp = fingerprint(config.repo)
            if fp is not None and fp != last_fp:
                last_fp = fp
                fp_since = now()
                stuck_reported = False
            if (
                fp is not None
                and not stuck_reported
                and now() - fp_since >= config.idle_minutes * 60
            ):
                log(f"agent looks stuck: no commit and no file change for {config.idle_minutes} minutes")
                stuck_reported = True
                agent.stop(config)
                while _status():
                    pass
        progress = read_progress(progress_path, config.prefix)
        log(f"session {session} ended: {len(progress.done)}/{total} done, {len(progress.blocked)} blocked")
        if progress.finished:
            return 0
        if len(progress.done) > last_done:
            empty_streak = 0
        else:
            empty_streak += 1
        last_done = len(progress.done)
        if empty_streak >= 3:
            log("agent stuck: no new item done three times in a row; a human should look")
            return 1

    log(f"stopped after {config.max_sessions} sessions without finishing; a human should look")
    return 1
