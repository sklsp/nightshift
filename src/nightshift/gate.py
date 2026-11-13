"""Start a test run behind a gate and wait for its result."""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from nightshift.config import RunConfig

_GATE_DIR = ".gate"
_LOG_NAME = "log"
_DONE_NAME = "done"
_MODE_NAME = "mode"
_FRESH_SECONDS = 120
_TAIL_LINES = 20
_POLL_SECONDS = 0.1

_COUNT_RE = re.compile(r"(\d+)\s+(failed|errors?|passed)\b")
_PERCENT_RE = re.compile(r"\[\s*(\d{1,3})%\]")


def _gate_dir(run_dir: Path) -> Path:
    return run_dir / _GATE_DIR


def _log_path(run_dir: Path) -> Path:
    return _gate_dir(run_dir) / _LOG_NAME


def _done_path(run_dir: Path) -> Path:
    return _gate_dir(run_dir) / _DONE_NAME


def _mode_path(run_dir: Path) -> Path:
    return _gate_dir(run_dir) / _MODE_NAME


def start(config: RunConfig, run_dir: Path, extra_args: Sequence[str] = ()) -> int:
    """Start the test command detached behind the gate, or refuse to double start."""
    if _run_is_going(run_dir):
        print(f"GATE ALREADY RUNNING; now run: nightshift gate wait {run_dir} 1")
        return 0

    for lint_command in ([config.lint] if config.lint else []):  # lint is ONE command (fixed by Claude)
        with tempfile.TemporaryFile("w+b") as lint_log:
            result = subprocess.run(
                lint_command,
                cwd=config.repo,
                stdout=lint_log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                lint_log.seek(0)
                tail = lint_log.read().decode("utf-8", "replace").splitlines()[-_TAIL_LINES:]
                for line in tail:
                    print(line, file=sys.stderr)
                print("GATE NOT STARTED: lint failed", file=sys.stderr)
                return 1

    command = [*config.test, *extra_args]
    gate_dir = _gate_dir(run_dir)
    gate_dir.mkdir(parents=True, exist_ok=True)
    done_path = _done_path(run_dir)
    if done_path.exists():
        done_path.unlink()
    log_path = _log_path(run_dir)
    _mode_path(run_dir).write_text(" ".join(command), encoding="utf-8", newline="\n")

    with open(log_path, "wb") as log_file:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "nightshift.gate",
                "_run",
                "--log",
                str(log_path),
                "--done",
                str(done_path),
                "--cwd",
                str(config.repo),
                *command,
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=_child_env(),
            **_detach_kwargs(),
        )

    print(f"GATE STARTED; now run: nightshift gate wait {run_dir} 1")
    return 0


def _run_is_going(run_dir: Path) -> bool:
    """True when a previous start is still fresh enough to count as running."""
    log_path = _log_path(run_dir)
    if not log_path.is_file() or _done_path(run_dir).is_file():
        return False
    age = time.time() - log_path.stat().st_mtime
    return age < _FRESH_SECONDS


def wait(config: RunConfig, run_dir: Path, n: int) -> int:
    """Wait for the gated test run to finish and report its result."""
    done_path = _done_path(run_dir)
    deadline = time.monotonic() + config.wait_seconds
    while not done_path.is_file():
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_SECONDS)

    if not done_path.is_file():
        # The number changes on every call ON PURPOSE: some agents cancel a session
        # that repeats one tool call 5 times in a row, so the next command must
        # never be identical to this one.
        print(f"RUNNING {_last_percent(_log_path(run_dir))}%; next run: nightshift gate wait {run_dir} {n + 1}")
        return 0

    text = _log_text(run_dir)
    for line in _failed_error_lines(text):
        print(line)
    summary = _summary_line(text)
    if summary is not None:
        print(summary)
    failed, errors, passed = _counts(text)
    if failed == 0 and errors == 0 and passed >= config.min_passed:
        print("GATE PASS")
        return 0
    print("GATE FAIL")
    return 1


def _log_text(run_dir: Path) -> str:
    log_path = _log_path(run_dir)
    if not log_path.is_file():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")


def _last_percent(log_path: Path) -> int:
    """Return the last [ NN%] marker found in the log, or 0 when there is none."""
    if not log_path.is_file():
        return 0
    text = log_path.read_text(encoding="utf-8", errors="replace")
    matches = _PERCENT_RE.findall(text)
    return int(matches[-1]) if matches else 0


def _failed_error_lines(text: str) -> list[str]:
    """Return the FAILED and ERROR lines of a test log."""
    return [line for line in text.splitlines() if "FAILED" in line or "ERROR" in line]


def _summary_line(text: str) -> str | None:
    """Return the last log line that carries pass/fail counts, if any."""
    for line in reversed(text.splitlines()):
        if _COUNT_RE.search(line):
            return line.strip()
    return None


def _counts(text: str) -> tuple[int, int, int]:
    """Count failed, errors and passed from the log; the last count wins."""
    failed = 0
    errors = 0
    passed = 0
    for line in text.splitlines():
        for match in _COUNT_RE.finditer(line):
            value = int(match.group(1))
            if match.group(2) == "failed":
                failed = value
            elif match.group(2).startswith("error"):
                errors = value
            else:
                passed = value
    return failed, errors, passed


def _child_env() -> dict[str, str]:
    """Environment for the detached child so it can import nightshift again."""
    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    return env


def _detach_kwargs() -> dict[str, object]:
    """Popen flags that detach the child from this terminal."""
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags}
    return {"start_new_session": True}


def _run(log_path: Path, done_path: Path, cwd: Path | None, command: list[str]) -> int:
    """Run one test command; its output goes to the inherited log file."""
    try:
        code = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL).returncode
    except Exception as exc:  # noqa: BLE001 - the gate must always write done
        print(f"gate could not run {' '.join(command)}: {exc}", file=sys.stderr)
        code = 127
    done_path.write_text(str(code), encoding="utf-8", newline="\n")
    return code


def _main(argv: list[str] | None = None) -> int:
    """Command line entry point for the detached wrapper."""
    parser = argparse.ArgumentParser(prog="nightshift.gate", description="Run one gated test command.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("_run", help="wrapper that writes the exit code to done")
    run_parser.add_argument("--log", type=Path, required=True)
    run_parser.add_argument("--done", type=Path, required=True)
    run_parser.add_argument("--cwd", type=Path, default=None)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    return _run(args.log, args.done, args.cwd, command)


if __name__ == "__main__":
    raise SystemExit(_main())
