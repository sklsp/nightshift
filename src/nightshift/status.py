"""Compute how long the agent has been on its current item."""

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from nightshift.config import RunConfig
from nightshift.git import commit_log
from nightshift.progress import Progress
from nightshift.supervise import read_supervisor_events


def current_item_seconds(
    run_dir: Path,
    config: RunConfig,
    progress: Progress,
    now: Callable[[], float] = time.time,
) -> int | None:
    """Return whole seconds on the first not-done item, or None when there is no reference point.

    The reference is the newest commit whose subject starts with a marker of a done item;
    when no such commit exists it falls back to the first supervisor event timestamp.
    """
    if progress.next_item is None:
        return None
    markers = [f"{config.prefix}{number}:" for number in progress.done]
    reference: float | None = None
    for _, unix_time, subject in commit_log(config.repo):
        if any(subject.startswith(marker) for marker in markers):
            reference = float(unix_time)
            break
    if reference is None:
        events = read_supervisor_events(run_dir)
        if not events:
            return None
        reference = datetime.fromisoformat(events[0]["t"]).timestamp()
    return int(now() - reference)
