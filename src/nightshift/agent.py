"""Launch and check on an external coding agent.

Example for the Cline CLI in run.toml ([agent] section):

    launch = ["cline-cli", "-z", "-c", "<repo>", "-P", "lmstudio", "-m", "<model>", "{message}"]
    status = ["cline-cli", "history", "--json", "--limit", "1"]
    running_pattern = '"status":"running"'

Note for Cline: the launch command may print "zen dispatch failed" while the session still starts;
nightshift does not treat launch output as failure.
"""

import re
import subprocess
import sys

from nightshift.config import RunConfig

_STATUS_TIMEOUT_SECONDS = 10


class AgentError(ValueError):
    """The agent section of run.toml is missing or invalid."""


def launch(config: RunConfig, message: str) -> int:
    """Run the configured launch command with every {message} replaced; return its exit code."""
    if not config.launch:
        raise AgentError("missing required key 'agent.launch'")
    command = [arg.replace("{message}", message) for arg in config.launch]
    result = subprocess.run(command, cwd=config.repo, stdin=subprocess.DEVNULL)
    return result.returncode


def format_exit_code(code: int) -> str:
    """Format an exit code, adding hex when it is above 0x7FFFFFFF or below zero."""
    if code > 0x7FFFFFFF or code < 0:
        return f"{code} (0x{code & 0xFFFFFFFF:08X})"
    return str(code)


def status_output(config: RunConfig, *, quiet: bool = False) -> str | None:
    """Run the status command and return its stdout, or None when the check failed."""
    if not config.status:
        raise AgentError("missing required key 'agent.status'")
    try:
        result = subprocess.run(
            list(config.status),
            cwd=config.repo,
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        if not quiet:
            print(f"agent status check failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        if not quiet:
            print(f"agent status command exited with code {format_exit_code(result.returncode)}", file=sys.stderr)
        return None
    return result.stdout


def is_running(config: RunConfig, *, quiet: bool = False) -> bool | None:
    """Return True when the status output matches, False when it does not, and None when the check failed."""
    output = status_output(config, quiet=quiet)
    if output is None:
        return None
    return re.search(config.running_pattern, output) is not None


def session_id(config: RunConfig, output: str) -> str | None:
    """Apply agent.session_pattern to one status output and return group 1 of the first match."""
    if not config.session_pattern:
        return None
    match = re.search(config.session_pattern, output)
    if match is None:
        return None
    return match.group(1)


def stop(config: RunConfig) -> None:
    """Run the configured stop command once, or do nothing when it is empty.

    When a stop argument holds {session} and agent.session_pattern is set, the status command runs
    once more and group 1 of its first match fills every placeholder; without an id the stop is
    skipped instead of running with a literal {session}. Without the placeholder the command runs
    exactly as before.
    """
    if not config.stop:
        return
    if any("{session}" in arg for arg in config.stop):
        if not config.session_pattern:
            print("stop command uses {session} but agent.session_pattern is not set; skipping stop", file=sys.stderr)
            return
        output = status_output(config, quiet=True)
        session = session_id(config, output) if output is not None else None
        if session is None:
            print("no session id found in status output; skipping stop", file=sys.stderr)
            return
        command = [arg.replace("{session}", session) for arg in config.stop]
    else:
        command = list(config.stop)
    subprocess.run(command, cwd=config.repo, stdin=subprocess.DEVNULL)
