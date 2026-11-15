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


def launch(config: RunConfig, message: str) -> None:
    """Run the configured launch command with every {message} replaced by the message."""
    if not config.launch:
        raise AgentError("missing required key 'agent.launch'")
    command = [arg.replace("{message}", message) for arg in config.launch]
    subprocess.run(command, cwd=config.repo, stdin=subprocess.DEVNULL)


def _format_exit_code(code: int) -> str:
    """Format an exit code, adding hex when it is above 0x7FFFFFFF or below zero."""
    if code > 0x7FFFFFFF or code < 0:
        return f"{code} (0x{code & 0xFFFFFFFF:08X})"
    return str(code)


def is_running(config: RunConfig, *, quiet: bool = False) -> bool | None:
    """Return True when the status output matches, False when it does not, and None when the check failed."""
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
            print(f"agent status command exited with code {_format_exit_code(result.returncode)}", file=sys.stderr)
        return None
    return re.search(config.running_pattern, result.stdout) is not None


def stop(config: RunConfig) -> None:
    """Run the configured stop command once, or do nothing when it is empty."""
    if not config.stop:
        return
    subprocess.run(list(config.stop), cwd=config.repo, stdin=subprocess.DEVNULL)
