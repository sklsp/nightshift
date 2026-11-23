"""The README and docs must match the CLI: every subcommand is documented, every documented command exists."""

import argparse
import re
from pathlib import Path

from nightshift.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS_DIR = ROOT / "docs"

_WORD = r"[A-Za-z][A-Za-z0-9_-]*"


def _subcommand_names(parser: argparse.ArgumentParser) -> set[str]:
    """The names of the subparsers registered on parser."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices = action.choices
            assert isinstance(choices, dict)
            return set(choices.keys())
    return set()


def _all_subcommands() -> tuple[set[str], set[str]]:
    """The top level subcommand names and the gate subcommand names."""
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices = action.choices
            assert isinstance(choices, dict)
            top = set(choices.keys())
            assert "gate" in top, "the CLI has no gate command"
            gate_parser = choices["gate"]
            assert isinstance(gate_parser, argparse.ArgumentParser)
            return top, _subcommand_names(gate_parser)
    raise AssertionError("the parser has no subparsers")


def _code_regions(line: str, in_fence: bool) -> list[str]:
    """The code parts of one markdown line: the whole line inside a fence, else its backtick spans."""
    if in_fence:
        return [line]
    return re.findall(r"`([^`\n]+)`", line)


def _mentions(text: str) -> list[tuple[int, str, str | None]]:
    """Every `nightshift <word>` inside the code regions of text.

    Returns (line number, word, gate subcommand). The third entry is set only when a bare word follows
    `nightshift gate`, so a typo in the second level fails too."""
    found: list[tuple[int, str, str | None]] = []
    pattern = re.compile(rf"\bnightshift\s+({_WORD})(?:\s+({_WORD}))?")
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        for region in _code_regions(line, in_fence):
            for match in pattern.finditer(region):
                word = match.group(1)
                second = match.group(2)
                gate_sub = second if word == "gate" and second is not None else None
                found.append((number, word, gate_sub))
    return found


def _read(path: Path) -> str:
    """Read a markdown file with normalized newlines."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_subcommand_is_documented_in_readme() -> None:
    """Every subcommand of the CLI appears in README.md as `nightshift <name>`."""
    top, gate_subs = _all_subcommands()
    assert top and gate_subs, "the CLI parser has no subcommands"
    text = _read(README)
    for name in sorted(top):
        assert re.search(rf"\bnightshift {re.escape(name)}\b", text), (
            f"README.md never shows the command `nightshift {name}`"
        )
    for name in sorted(gate_subs):
        assert re.search(rf"\bnightshift gate {re.escape(name)}\b", text), (
            f"README.md never shows the command `nightshift gate {name}`"
        )


def test_every_documented_command_is_real() -> None:
    """Every `nightshift <word>` in README.md and docs/*.md code regions is a real subcommand."""
    top, gate_subs = _all_subcommands()
    paths = [README, *sorted(DOCS_DIR.glob("*.md"))]
    for path in paths:
        text = _read(path)
        for number, word, gate_sub in _mentions(text):
            assert word in top, (
                f"{path.name}:{number} shows `nightshift {word}` but the CLI has no such subcommand"
            )
            if gate_sub is not None:
                assert gate_sub in gate_subs, (
                    f"{path.name}:{number} shows `nightshift gate {gate_sub}` "
                    "but the gate has no such subcommand"
                )
