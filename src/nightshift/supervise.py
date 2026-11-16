"""Supervise an agent: relaunch it when a session ends until every item is done.

A stuck detector exists because of a real failure: one session spent hours
re-reading the same files after its context was compacted, writing nothing.
While the agent runs, the supervisor fingerprints the repo (HEAD plus git
status) on every poll; if it does not change for idle_minutes the session is
reported as stuck and, when configured, stopped.

Only one supervisor may run per repo: before its first poll, supervise takes
a lock file in the repo's git directory. A live pid in an existing lock means
another supervisor is running and this one refuses to start; a dead pid means
a crash left the lock behind and it is removed and taken over.

Every logged line is also appended to .gate/supervise.jsonl with a timestamp,
so a run stays readable after the fact.
"""

import contextlib
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from nightshift import agent
from nightshift.config import RunConfig
from nightshift.items import load_items
from nightshift.progress import Progress, read_progress

_LOCK_NAME = "nightshift-supervise.lock"
_PROCESS_QUERY_INFORMATION = 0x1000
_STILL_ACTIVE = 259


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


def _log(message: str) -> None:
    """Print one supervisor line and flush it so a redirected log file fills in at once."""
    print(message, flush=True)


def _refresh_progress_copy(progress_file: Path, run_dir: Path) -> None:
    """Copy the repo progress file into .gate so a finished run stays finished if cleanup removes it."""
    target = run_dir / ".gate" / "progress.md"
    if not progress_file.is_file():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(progress_file, target)
    except OSError:
        # The copy is a fallback for later reads; an unwritable .gate must never stop the supervisor.
        pass


def _read_progress(progress_file: Path, prefix: str, run_dir: Path, log=_log) -> Progress:
    """Read progress from the repo file; when cleanup removed it, read the .gate copy instead."""
    if progress_file.is_file():
        return read_progress(progress_file, prefix)
    fallback = run_dir / ".gate" / "progress.md"
    if fallback.is_file():
        log("repo progress file missing; reading the .gate copy")
        return read_progress(fallback, prefix)
    return read_progress(progress_file, prefix)


def _pid_alive(pid: int) -> bool:
    """Return True when a process with this pid is running on this machine."""
    if sys.platform == "win32":
        # os.kill would terminate the process on Windows, so ask kernel32 instead.
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _git_dir(repo: Path) -> Path | None:
    """Return the repo's git directory, or None when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = repo / path
    return path


def _lock_path(repo: Path) -> Path | None:
    """Return the supervisor lock file inside repo's git dir, or None when there is no git dir."""
    git_dir = _git_dir(repo)
    if git_dir is None:
        return None
    return git_dir / _LOCK_NAME


def _take_lock(lock_path: Path, run_dir: Path, log, alive) -> bool:
    """Write our pid and run into the lock; return False when a live supervisor holds it."""
    if lock_path.is_file():
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            other_pid = int(data["pid"])
            other_run = str(data.get("run", ""))
        except (OSError, TypeError, ValueError, KeyError):
            log("removing a stale supervisor lock (unreadable)")
        else:
            if alive(other_pid):
                log(f"another supervisor (pid {other_pid}) is running {other_run}; refusing to start")
                return False
            log(f"removing a stale supervisor lock (pid {other_pid})")
    payload = json.dumps({"pid": os.getpid(), "run": run_dir.resolve().as_posix()})
    tmp_path = lock_path.with_name(f"{lock_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, lock_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return True


def _release_lock(lock_path: Path) -> None:
    """Remove the supervisor lock file, ignoring a missing one."""
    with contextlib.suppress(OSError):
        lock_path.unlink()



def supervise(
    config: RunConfig,
    run_dir: Path,
    *,
    sleep=time.sleep,
    now=time.time,
    log=_log,
    fingerprint=_repo_fingerprint,
    alive=_pid_alive,
) -> int:
    """Relaunch the agent until every item is done or a stop condition hits."""
    total = len(load_items(run_dir))
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    message = (
        f"Read {(run_dir.resolve() / '00-start.md').as_posix()} and follow it. "
        f"Resume at the first item that is not ticked. "
        f"Do NOT call attempt_completion until item {total} is done."
    )

    event_path = run_dir / ".gate" / "supervise.jsonl"

    def _log_with_events(message: str) -> None:
        """Print one supervisor line and append it to .gate/supervise.jsonl so a run is readable afterwards."""
        log(message)
        try:
            event_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(now()).isoformat(timespec="seconds")
            with event_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": stamp, "msg": message}) + "\n")
        except OSError:
            # The event log is only for reading the run afterwards; a broken .gate must never stop supervision.
            pass

    lock_path = _lock_path(config.repo)
    if lock_path is not None and not _take_lock(lock_path, run_dir, _log_with_events, alive):
        return 2
    try:
        last_poll = now() - config.poll_seconds  # allow an immediate first check
        empty_streak = 0
        launch_failures = 0
        last_done = 0
        unknown_streak = 0

        def _status() -> bool | None:
            """Poll the agent once, at most every poll_seconds; log unknown streak boundaries."""
            nonlocal last_poll, unknown_streak
            wait_for = config.poll_seconds - (now() - last_poll)
            if wait_for > 0:
                sleep(wait_for)
            last_poll = now()
            _refresh_progress_copy(progress_path, run_dir)
            status = agent.is_running(config, quiet=unknown_streak > 0)
            if status is None:
                unknown_streak += 1
                if unknown_streak == config.status_retry_polls:
                    _log_with_events(
                        f"agent status unknown for {config.status_retry_polls} polls; treating the session as ended"
                    )
            elif unknown_streak > 0:
                _log_with_events(f"agent status readable again after {unknown_streak} failed polls")
                unknown_streak = 0
            return status

        def _status_gave_up() -> bool:
            """True when the unknown streak reached its cap and the session must end."""
            return unknown_streak >= config.status_retry_polls

        for session in range(1, config.max_sessions + 1):
            if _status_gave_up():
                unknown_streak = 0  # the session that gave up has ended; the next one gets a fresh budget
            status = _status()
            while status is None and not _status_gave_up():
                status = _status()
            if status is False:
                code = agent.launch(config, message)
                if code != 0:
                    _log_with_events(f"agent launch exited with code {agent.format_exit_code(code)}")
                started = False
                for _poll in range(config.launch_grace_polls):
                    grace_status = _status()
                    if grace_status is True:
                        started = True
                        break
                    if grace_status is None and _status_gave_up():
                        break
                if not started:
                    _log_with_events("agent did not start")
                    launch_failures += 1
                    if launch_failures >= config.max_launch_failures:
                        _log_with_events(
                            f"agent cannot be launched after {launch_failures} tries; a human should look"
                        )
                        return 2
                    sleep(min(config.launch_backoff_seconds * (2 ** (launch_failures - 1)), 900))
                    continue
                launch_failures = 0
            last_fp = None
            fp_since = now()
            stuck_reported = False
            while True:
                status = _status()
                if status is False or (status is None and _status_gave_up()):
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
                    _log_with_events(
                        f"agent looks stuck: no commit and no file change for {config.idle_minutes} minutes"
                    )
                    stuck_reported = True
                    agent.stop(config)
                    while True:
                        stop_status = _status()
                        if stop_status is False or (stop_status is None and _status_gave_up()):
                            break
            progress = _read_progress(progress_path, config.prefix, run_dir, _log_with_events)
            _log_with_events(
                f"session {session} ended: {len(progress.done)}/{total} done, {len(progress.blocked)} blocked"
            )
            if progress.finished:
                return 0
            if len(progress.done) > last_done:
                empty_streak = 0
            else:
                empty_streak += 1
            last_done = len(progress.done)
            if empty_streak >= 3:
                _log_with_events("agent stuck: no new item done three times in a row; a human should look")
                return 1

        _log_with_events(f"stopped after {config.max_sessions} sessions without finishing; a human should look")
        return 1
    finally:
        if lock_path is not None:
            _release_lock(lock_path)
