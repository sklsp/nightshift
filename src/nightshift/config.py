"""Load and validate a run.toml configuration file."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """The run.toml file is missing or invalid."""


@dataclass(frozen=True)
class RunConfig:
    """Validated settings for one nightshift run."""

    name: str
    repo: Path
    test: list[str]
    branch: str = ""
    prefix: str = "T"
    command: list[str] = field(default_factory=lambda: ["nightshift"])
    lint: list[str] = field(default_factory=list)
    pre: list[list[str]] = field(default_factory=list)
    min_passed: int = 1
    wait_seconds: int = 25
    timeout_hint_seconds: int = 30
    launch: list[str] = field(default_factory=list)
    status: list[str] = field(default_factory=list)
    running_pattern: str = "running"
    poll_seconds: int = 30
    max_sessions: int = 20
    launch_grace_polls: int = 4
    status_retry_polls: int = 10
    idle_minutes: int = 60
    stop: list[str] = field(default_factory=list)
    launch_backoff_seconds: int = 60
    max_launch_failures: int = 6


_SECTIONS = ("run", "check", "agent")

_RUN_KEYS = {"name", "repo", "branch", "prefix", "command"}
_CHECK_KEYS = {"lint", "pre", "test", "min_passed", "wait_seconds", "timeout_hint_seconds"}
_AGENT_KEYS = {
    "launch",
    "status",
    "running_pattern",
    "poll_seconds",
    "max_sessions",
    "launch_grace_polls",
    "status_retry_polls",
    "idle_minutes",
    "stop",
    "launch_backoff_seconds",
    "max_launch_failures",
}


def load_config(run_dir: Path) -> RunConfig:
    """Load and validate <run dir>/run.toml into a RunConfig."""
    path = run_dir / "run.toml"
    if not path.is_file():
        raise ConfigError(f"missing run.toml in {run_dir}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in run.toml: {exc}") from exc

    for key in data:
        if key not in _SECTIONS:
            raise ConfigError(f"unknown key '{key}'")

    run = _section(data, "run", _RUN_KEYS)
    check = _section(data, "check", _CHECK_KEYS)
    agent = _section(data, "agent", _AGENT_KEYS)

    name = _required_str(run, "run", "name")
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        raise ConfigError("invalid value for 'run.name': letters, digits and dashes only")

    repo_raw = _required_str(run, "run", "repo")
    repo = Path(repo_raw)
    if not repo.is_absolute():
        repo = (run_dir / repo).resolve()

    branch = _str(run, "run", "branch", "")
    prefix = _str(run, "run", "prefix", "T")
    if not re.fullmatch(r"[A-Z]{1,3}", prefix):
        raise ConfigError("invalid value for 'run.prefix': 1 to 3 capital letters")

    command = _str_list(run, "run", "command", ["nightshift"])
    if not command:
        raise ConfigError("invalid value for 'run.command': at least one part is required")

    lint = _str_list(check, "check", "lint", [])
    pre = _str_list_of_lists(check, "check", "pre", [])
    test = _required_str_list(check, "check", "test")
    min_passed = _int(check, "check", "min_passed", 1)
    wait_seconds = _int(check, "check", "wait_seconds", 25)
    timeout_hint_seconds = _int(check, "check", "timeout_hint_seconds", 30)

    launch = _str_list(agent, "agent", "launch", [])
    status = _str_list(agent, "agent", "status", [])
    running_pattern = _str(agent, "agent", "running_pattern", "running")
    poll_seconds = _int(agent, "agent", "poll_seconds", 30)
    max_sessions = _int(agent, "agent", "max_sessions", 20)
    launch_grace_polls = _int(agent, "agent", "launch_grace_polls", 4)
    status_retry_polls = _int(agent, "agent", "status_retry_polls", 10)
    if status_retry_polls < 1:
        raise ConfigError("invalid value for 'agent.status_retry_polls': must be at least 1")
    idle_minutes = _int(agent, "agent", "idle_minutes", 60)
    stop = _str_list(agent, "agent", "stop", [])
    launch_backoff_seconds = _int(agent, "agent", "launch_backoff_seconds", 60)
    max_launch_failures = _int(agent, "agent", "max_launch_failures", 6)
    if launch_backoff_seconds < 1:
        raise ConfigError("invalid value for 'agent.launch_backoff_seconds': must be at least 1")
    if max_launch_failures < 1:
        raise ConfigError("invalid value for 'agent.max_launch_failures': must be at least 1")

    return RunConfig(
        name=name,
        repo=repo,
        test=test,
        branch=branch,
        prefix=prefix,
        command=command,
        lint=lint,
        pre=pre,
        min_passed=min_passed,
        wait_seconds=wait_seconds,
        timeout_hint_seconds=timeout_hint_seconds,
        launch=launch,
        status=status,
        running_pattern=running_pattern,
        poll_seconds=poll_seconds,
        max_sessions=max_sessions,
        launch_grace_polls=launch_grace_polls,
        status_retry_polls=status_retry_polls,
        idle_minutes=idle_minutes,
        stop=stop,
        launch_backoff_seconds=launch_backoff_seconds,
        max_launch_failures=max_launch_failures,
    )


def command_prefix(config: RunConfig) -> str:
    """Join run.command with spaces, quoting parts that need shell protection."""
    return " ".join(quote_arg(part) for part in config.command)


def quote_arg(part: str) -> str:
    """Wrap one command argument in double quotes when it contains a space or a glob character."""
    if any(ch in part for ch in (" ", "*", "?", "[")):
        return f'"{part}"'
    return part


def _section(data: dict[str, object], name: str, allowed: set[str]) -> dict[str, object]:
    """Return the named top level table and reject unknown keys inside it."""
    if name not in data:
        return {}
    value = data[name]
    if not isinstance(value, dict):
        raise ConfigError(f"wrong type for '{name}': expected a table")
    for key in value:
        if key not in allowed:
            raise ConfigError(f"unknown key '{name}.{key}'")
    return value


def _str(table: dict[str, object], where: str, key: str, default: str) -> str:
    """Return a string field or its default when the key is absent."""
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, str):
        raise ConfigError(f"wrong type for '{where}.{key}': expected str")
    return value


def _int(table: dict[str, object], where: str, key: str, default: int) -> int:
    """Return an integer field or its default when the key is absent."""
    if key not in table:
        return default
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"wrong type for '{where}.{key}': expected int")
    return value


def _str_list(table: dict[str, object], where: str, key: str, default: list[str]) -> list[str]:
    """Return a list of strings or its default when the key is absent."""
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"wrong type for '{where}.{key}': expected a list of str")
    return list(value)


def _str_list_of_lists(
    table: dict[str, object], where: str, key: str, default: list[list[str]]
) -> list[list[str]]:
    """Return a list of commands (each a list of strings) or its default when the key is absent."""
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, list):
        raise ConfigError(f"wrong type for '{where}.{key}': expected a list of commands")
    for index, item in enumerate(value):
        if not isinstance(item, list) or not all(isinstance(part, str) for part in item):
            raise ConfigError(f"wrong type for '{where}.{key}[{index}]': expected a list of str")
    return [list(item) for item in value]


def _required_str(table: dict[str, object], where: str, key: str) -> str:
    """Return a required string field or raise when it is missing."""
    if key not in table:
        raise ConfigError(f"missing required key '{where}.{key}'")
    value = table[key]
    if not isinstance(value, str):
        raise ConfigError(f"wrong type for '{where}.{key}': expected str")
    return value


def _required_str_list(table: dict[str, object], where: str, key: str) -> list[str]:
    """Return a required list of strings or raise when it is missing."""
    if key not in table:
        raise ConfigError(f"missing required key '{where}.{key}'")
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"wrong type for '{where}.{key}': expected a list of str")
    return list(value)
