"""Supervise an agent: relaunch it when a session ends until every item is done."""

import time
from pathlib import Path

from nightshift import agent
from nightshift.config import RunConfig
from nightshift.items import load_items
from nightshift.progress import read_progress


def supervise(config: RunConfig, run_dir: Path, *, sleep=time.sleep, now=time.time, log=print) -> int:
    """Relaunch the agent until every item is done or a stop condition hits."""
    total = len(load_items(run_dir))
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    message = (
        f"Read {(run_dir / '00-start.md').as_posix()} and follow it. "
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
        while True:
            if not _status():
                break
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
