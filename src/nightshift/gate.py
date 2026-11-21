"""Start a test run behind a gate and wait for its result.

The pre checks exist because of a real failure: one run shipped 55 type errors
because its gate ran only lint and the tests while CI also ran a strict type
checker, so the green gate never saw them. Every command in check.pre runs
after lint and before the suite; the first one that fails stops the start.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nightshift.config import RunConfig, command_prefix

_GATE_DIR = ".gate"
_LOG_NAME = "log"
_DONE_NAME = "done"
_MODE_NAME = "mode"
_COMMAND_NAME = "command"
_FRESH_SECONDS = 120
_TAIL_LINES = 20
_POLL_SECONDS = 0.1

_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b")
_PERCENT_RE = re.compile(r"\[\s*(\d{1,3})%\]")


@dataclass(frozen=True)
class Summary:
    """Counts parsed from one pytest summary line."""

    passed: int
    failed: int
    errors: int
    skipped: int
    xfailed: int
    xpassed: int
    line: str | None


def _gate_dir(run_dir: Path) -> Path:
    return run_dir / _GATE_DIR


def _log_path(run_dir: Path) -> Path:
    return _gate_dir(run_dir) / _LOG_NAME


def _done_path(run_dir: Path) -> Path:
    return _gate_dir(run_dir) / _DONE_NAME


def _mode_path(run_dir: Path) -> Path:
    return _gate_dir(run_dir) / _MODE_NAME


def _saved_prefix(config: RunConfig, run_dir: Path) -> str:
    """Return the command prefix saved at start, or the one from config when there is none."""
    path = _gate_dir(run_dir) / _COMMAND_NAME
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text
    return command_prefix(config)


def _preflight(config: RunConfig, run_dir: Path) -> int | None:
    """Refuse a double start and run lint plus every pre check in order; return an exit code or None."""
    if _run_is_going(run_dir):
        print(f"GATE ALREADY RUNNING; now run: {_saved_prefix(config, run_dir)} gate wait {run_dir} 1")
        return 0
    if _is_stale(run_dir):
        print("previous gate run looks stale; starting a new one")

    for label, command in _preflight_commands(config):
        with tempfile.TemporaryFile("w+b") as check_log:
            result = subprocess.run(
                command,
                cwd=config.repo,
                stdout=check_log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                check_log.seek(0)
                tail = check_log.read().decode("utf-8", "replace").splitlines()[-_TAIL_LINES:]
                for line in tail:
                    print(line, file=sys.stderr)
                print(f"GATE NOT STARTED: {label} failed", file=sys.stderr)
                return 1
    return None


def _preflight_commands(config: RunConfig) -> list[tuple[str, list[str]]]:
    """Return (label, command) pairs for lint and every pre check, in the order they run."""
    commands: list[tuple[str, list[str]]] = []
    if config.lint:  # lint is ONE command (fixed by Claude)
        commands.append(("lint", config.lint))
    for command in config.pre:
        if command:
            commands.append((" ".join(command), command))  # noqa: PERF401 - the join reads better than a comprehension here
    return commands


def _launch(config: RunConfig, run_dir: Path, command: list[str], mode_line: str) -> int:
    """Launch the detached test run and remember how it was started in .gate/mode."""
    gate_dir = _gate_dir(run_dir)
    gate_dir.mkdir(parents=True, exist_ok=True)
    done_path = _done_path(run_dir)
    if done_path.exists():
        done_path.unlink()
    log_path = _log_path(run_dir)
    _mode_path(run_dir).write_text(f"{mode_line}\n{' '.join(command)}", encoding="utf-8", newline="\n")
    (_gate_dir(run_dir) / _COMMAND_NAME).write_text(command_prefix(config), encoding="utf-8", newline="\n")

    with log_path.open("wb") as log_file:
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

    print(f"GATE STARTED; now run: {command_prefix(config)} gate wait {run_dir} 1")
    return 0


def start(config: RunConfig, run_dir: Path, extra_args: Sequence[str] = ()) -> int:
    """Start the test command detached behind the gate, or refuse to double start."""
    blocked = _preflight(config, run_dir)
    if blocked is not None:
        return blocked
    return _launch(config, run_dir, [*config.test, *extra_args], "start")


def file(config: RunConfig, run_dir: Path, test_file: str, module: str) -> int:
    """Start one test file with coverage of one module behind the gate."""
    blocked = _preflight(config, run_dir)
    if blocked is not None:
        return blocked
    # Plain --cov, not --cov=<module>: pointing pytest-cov at a module imports it before
    # the tests do, which broke a SQLAlchemy C extension in a real run.
    command = [*config.test, test_file, "--cov", "--cov-report=term-missing", "--cov-fail-under=0"]
    return _launch(config, run_dir, command, f"file {module}")


def clean(run_dir: Path) -> int:
    """Delete <run dir>/.gate/ and print what it removed."""
    gate_dir = _gate_dir(run_dir)
    if not gate_dir.is_dir():
        print(f"nothing to remove: {gate_dir} does not exist")
        return 0
    for path in sorted(gate_dir.iterdir()):
        print(f"removed {path}")
    shutil.rmtree(gate_dir)
    print(f"removed {gate_dir}")
    return 0


def state(run_dir: Path) -> str:
    """Return 'pass', 'fail', 'running', 'stale' or 'none' for the last gated run."""
    log_path = _log_path(run_dir)
    if not log_path.is_file():
        return "none"
    done_path = _done_path(run_dir)
    if not done_path.is_file():
        return "stale" if _is_stale(run_dir) else "running"
    code_text = done_path.read_text(encoding="utf-8", errors="replace").strip()
    return "pass" if code_text == "0" else "fail"


def last_gate_line(run_dir: Path) -> str:
    """One line describing the last gated test run, or 'last gate: none' when there is no log."""
    log_path = _log_path(run_dir)
    if not log_path.is_file():
        return "last gate: none"
    done_path = _done_path(run_dir)
    if not done_path.is_file():
        return "last gate: running"
    code_text = done_path.read_text(encoding="utf-8", errors="replace").strip() or "?"
    summary = parse_summary(_log_text(run_dir))
    if summary.line is None:
        return f"last gate: exit {code_text} (no pytest summary line)"
    return f"last gate: exit {code_text}: {summary.line}"


def _run_is_going(run_dir: Path) -> bool:
    """True when a previous start is still fresh enough to count as running."""
    log_path = _log_path(run_dir)
    if not log_path.is_file() or _done_path(run_dir).is_file():
        return False
    age = time.time() - log_path.stat().st_mtime
    return age < _FRESH_SECONDS


def _is_stale(run_dir: Path) -> bool:
    """True when the log is older than _FRESH_SECONDS with no done file (the detached process died)."""
    log_path = _log_path(run_dir)
    if not log_path.is_file() or _done_path(run_dir).is_file():
        return False
    age = time.time() - log_path.stat().st_mtime
    return age >= _FRESH_SECONDS


def wait(config: RunConfig, run_dir: Path, n: int) -> int:
    """Wait for the gated test run to finish and report its result."""
    prefix = _saved_prefix(config, run_dir)
    done_path = _done_path(run_dir)
    if not done_path.is_file() and _is_stale(run_dir):
        print(f"GATE STALE: run {prefix} gate start {run_dir} again")
        return 1
    deadline = time.monotonic() + config.wait_seconds
    while not done_path.is_file():
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_SECONDS)

    if not done_path.is_file():
        # The number changes on every call ON PURPOSE: some agents cancel a session
        # that repeats one tool call 5 times in a row, so the next command must
        # never be identical to this one.
        print(f"RUNNING {_last_percent(_log_path(run_dir))}%; next run: {prefix} gate wait {run_dir} {n + 1}")
        return 0

    text = _log_text(run_dir)
    for line in _failed_error_lines(text):
        print(line)
    summary = parse_summary(text)
    if summary.line is None:
        # This cost a whole night once; the hint is the point.
        print("no pytest summary line: is -q passed twice? (pytest -qq hides it)")
        print("GATE FAIL")
        return 1
    print(summary.line)
    kind, module = _mode(run_dir)
    if kind == "file":
        coverage_line = _coverage_line(text, module or "")
        print(coverage_line if coverage_line is not None else f"{module} 100% (fully covered)")
        if summary.passed >= 1 and summary.failed == 0:
            print("GATE PASS")
            return 0
        print("GATE FAIL")
        return 1
    if summary.failed == 0 and summary.errors == 0 and summary.passed >= config.min_passed:
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


def _mode(run_dir: Path) -> tuple[str, str | None]:
    """Return (kind, module) from .gate/mode; kind is 'start' or 'file'."""
    path = _mode_path(run_dir)
    if not path.is_file():
        return "start", None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return "start", None
    first = lines[0]
    if first.startswith("file "):
        return "file", first[len("file ") :]
    return "start", None


def _coverage_line(log_text: str, module: str) -> str | None:
    """Return the coverage report line for the module, or None when the report skipped it."""
    target = module.replace("\\", "/")
    for line in log_text.splitlines():
        parts = line.split()
        if parts and parts[0].replace("\\", "/") == target:
            return line.strip()
    return None


def parse_summary(log_text: str) -> Summary:
    """Return counts from the last pytest summary line, or an empty Summary when there is none."""
    for raw in reversed(log_text.splitlines()):
        if _COUNT_RE.search(raw):
            return _summary_from_line(raw.strip().strip("=").strip())
    return Summary(passed=0, failed=0, errors=0, skipped=0, xfailed=0, xpassed=0, line=None)


def _summary_from_line(line: str) -> Summary:
    """Fill the Summary fields from one summary line."""
    passed = failed = errors = skipped = xfailed = xpassed = 0
    for match in _COUNT_RE.finditer(line):
        value = int(match.group(1))
        word = match.group(2)
        if word == "passed":
            passed += value
        elif word == "failed":
            failed += value
        elif word.startswith("error"):
            errors += value
        elif word == "skipped":
            skipped += value
        elif word == "xfailed":
            xfailed += value
        else:  # xpassed
            xpassed += value
    return Summary(
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        xfailed=xfailed,
        xpassed=xpassed,
        line=line,
    )


def _child_env() -> dict[str, str]:
    """Environment for the detached child so it can import nightshift again."""
    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    return env


def _detach_kwargs() -> dict[str, Any]:
    """Popen flags that detach the child from this terminal."""
    if sys.platform == "win32":  # mypy narrows sys.platform, not os.name, so Linux CI type-checks this
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags}
    return {"start_new_session": True}


def _run(log_path: Path, done_path: Path, cwd: Path | None, command: list[str]) -> int:
    """Run one test command; its output goes to the inherited log file."""
    # the gate must always write done
    try:
        code = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL).returncode
    except Exception as exc:
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
