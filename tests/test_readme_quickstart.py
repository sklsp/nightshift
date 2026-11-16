"""Every nightshift command in the README quick start must be a real subcommand."""

import re
from pathlib import Path

import pytest

from nightshift.cli import build_parser

README = Path(__file__).resolve().parent.parent / "README.md"


def _quick_start_section(text: str) -> str:
    """The Quick start section of the README, up to the next level two heading."""
    match = re.search(r"^## Quick start\n(.*?)(?=^## )", text, flags=re.M | re.S)
    assert match is not None, "README has no '## Quick start' section"
    return match.group(1)


def _command_lines(section: str) -> list[str]:
    """The nightshift command lines in the quick start section."""
    commands = [line.strip() for line in section.splitlines() if line.strip().startswith("nightshift ")]
    assert commands, "the quick start has no nightshift commands"
    return commands


def test_quick_start_commands_are_real_subcommands() -> None:
    """Each quick start command parses with the real argument parser."""
    text = README.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = _command_lines(_quick_start_section(text))
    assert len(lines) <= 8, f"the quick start has {len(lines)} commands, at most eight are allowed"
    used: set[str] = set()
    for line in lines:
        tokens = line.split()
        assert len(tokens) >= 2, f"a command line names no subcommand: {line!r}"
        argv = tokens[1:]
        try:
            build_parser().parse_args(argv)
        except SystemExit as exc:
            pytest.fail(f"not a real command or flag combination: {line!r} (exit code {exc.code})")
        used.add(tokens[1])
    missing = {"init", "doctor", "build", "supervise", "watch", "report"} - used
    assert not missing, f"the quick start is missing commands: {sorted(missing)}"
