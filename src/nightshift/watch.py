"""Watch a run: print new commits and supervisor events as they appear."""

import time
from collections.abc import Callable
from pathlib import Path

from nightshift.config import RunConfig
from nightshift.git import commit_log
from nightshift.progress import Progress, read_progress
from nightshift.supervise import read_supervisor_events


def _out(line: str) -> None:
    """Print one watch line and flush it so a redirected log fills in at once."""
    print(line, flush=True)


def _event_line(event: dict[str, str]) -> str:
    """Format one supervisor event as 'HH:MM:SS <msg>'."""
    return f"{event['t'].rsplit('T', 1)[-1]} {event['msg']}"


def _read_run_progress(progress_path: Path, prefix: str, run_dir: Path) -> Progress:
    """Read progress from the repo file; when cleanup removed it, read the .gate copy instead."""
    if not progress_path.is_file():
        fallback = run_dir / ".gate" / "progress.md"
        if fallback.is_file():
            return read_progress(fallback, prefix)
    return read_progress(progress_path, prefix)


def watch(
    config: RunConfig,
    run_dir: Path,
    *,
    every: float = 20.0,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = _out,
) -> int:
    """Print new commits and supervisor events until the run is finished; return 0."""
    repo = config.repo
    progress_path = repo / f"PROGRESS-{config.name}.md"

    if once:
        for event in read_supervisor_events(run_dir):
            out(_event_line(event))
        last = commit_log(repo)[-5:]
        for commit_hash, _, subject in reversed(last):
            out(f"commit {commit_hash} {subject}")
        return 0

    seen = {commit_hash for commit_hash, _, _ in commit_log(repo)}
    event_count = len(read_supervisor_events(run_dir))
    interval = max(every, 1)
    try:
        while True:
            new = [c for c in reversed(commit_log(repo)) if c[0] not in seen]
            for commit_hash, _, subject in new:
                out(f"commit {commit_hash} {subject}")
                seen.add(commit_hash)
            events = read_supervisor_events(run_dir)[event_count:]
            event_count += len(events)
            for event in events:
                out(_event_line(event))
            if _read_run_progress(progress_path, config.prefix, run_dir).finished:
                out("watch: run finished")
                return 0
            sleep(interval)
    except KeyboardInterrupt:
        return 0
