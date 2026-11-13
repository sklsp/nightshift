"""Text checks for rendered run files.

Agents copy dashes from the spec into commit messages and docs, and a dash-free
spec keeps them out.
"""

from pathlib import Path


def lint_text(text: str) -> list[str]:
    """Return one `line <n>: <what>` problem per em dash, en dash or trailing whitespace."""
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        if "\u2014" in line:
            problems.append(f"line {number}: em dash")
        if "\u2013" in line:
            problems.append(f"line {number}: en dash")
        if line != line.rstrip():
            problems.append(f"line {number}: trailing whitespace")
    return problems


def lint_files(paths: list[Path]) -> dict[Path, list[str]]:
    """Lint each file and return only the files that have problems."""
    result: dict[Path, list[str]] = {}
    for path in paths:
        problems = lint_text(path.read_text(encoding="utf-8"))
        if problems:
            result[path] = problems
    return result
