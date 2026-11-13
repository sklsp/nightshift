"""Command line interface for nightshift."""

import argparse

from nightshift import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the top level argument parser."""
    parser = argparse.ArgumentParser(
        prog="nightshift",
        description="Run a local LLM coding agent unattended.",
    )
    parser.add_argument("--version", action="version", version=f"nightshift {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch; subcommands arrive in later items."""
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 2
    parser.parse_args(argv)
    return 0
