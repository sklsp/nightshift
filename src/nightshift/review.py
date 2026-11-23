"""Print what each item commit of a run changed, with warning lines under each block."""

import re
from pathlib import Path

from nightshift.commits import changed_files, resolve_base
from nightshift.config import RunConfig, load_config
from nightshift.git import commit_log, run_git
from nightshift.items import Item, load_items


def build_review(run_dir: Path) -> str:
    """Return the review text of one run: a block per item commit plus warning lines."""
    config = load_config(run_dir)
    items = load_items(run_dir)
    return "\n".join(review_lines(config, items)) + "\n"


def review_lines(config: RunConfig, items: list[Item]) -> list[str]:
    """Return the review lines of one run in item order; warnings follow their block."""
    base = resolve_base(config)
    commits = commit_log(config.repo, base=base, tip=_tip(config)) if base is not None else []
    by_number: dict[int, str] = {}
    pattern = re.compile(rf"^{re.escape(config.prefix)}(\d+): ")
    for found_hash, _, subject in commits:
        match = pattern.match(subject)
        if match is not None and int(match.group(1)) not in by_number:
            by_number[int(match.group(1))] = found_hash
    lines: list[str] = []
    for item in items:
        marker = f"{config.prefix}{item.number}"
        commit_hash = by_number.get(item.number)
        if commit_hash is None:
            lines.append(f"{marker} {item.title}: no commit")
        else:
            lines.extend(_block(config, item, commit_hash))
    return lines


def item_warnings(config: RunConfig, commit_hash: str) -> list[str]:
    """Return the plain warning texts of one item commit, in review order; empty when clean."""
    paths = changed_files(config.repo, commit_hash)
    per_file = _test_counts(config.repo, commit_hash, paths)
    return _warnings_for(config, paths, per_file)


def _tip(config: RunConfig) -> str:
    """The run's branch when it exists in the repo, else HEAD (the report's tip rule)."""
    if config.branch and run_git(
        config.repo, "rev-parse", "--verify", "--quiet", f"{config.branch}^{{commit}}"
    ).returncode == 0:
        return config.branch
    return "HEAD"


def _block(config: RunConfig, item: Item, commit_hash: str) -> list[str]:
    """Return the review block of one item commit: header, files line, tests line and warnings."""
    marker = f"{config.prefix}{item.number}"
    paths = changed_files(config.repo, commit_hash)
    added, removed = _numstat(config.repo, commit_hash)
    lines = [f"{marker} {item.title} ({commit_hash})"]
    files_line = f"  files: {len(paths)} (+{added} -{removed})"
    if paths:
        files_line += "  " + ", ".join(paths)
    lines.append(files_line)
    per_file = _test_counts(config.repo, commit_hash, paths)
    before = sum(count[0] for count in per_file.values())
    after = sum(count[1] for count in per_file.values())
    lines.append(f"  tests: {before} -> {after}")
    lines.extend(f"  ! {warning}" for warning in _warnings_for(config, paths, per_file))
    return lines


def _numstat(repo: Path, commit_hash: str) -> tuple[int, int]:
    """Return the total added and removed lines of one commit from git show --numstat."""
    result = run_git(repo, "show", "--numstat", commit_hash)
    if result.returncode != 0:
        return (0, 0)
    added = 0
    removed = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added += int(parts[0]) if parts[0].isdigit() else 0
        removed += int(parts[1]) if parts[1].isdigit() else 0
    return (added, removed)


_JS_TEST_SUFFIXES = (".test.js", ".test.mjs", ".test.cjs", ".test.ts", ".spec.js", ".spec.mjs", ".spec.ts")
_JS_TEST_STARTS = ("test(", "it(", "test.only(", "it.only(", "test.skip(", "it.skip(")


def _test_files(paths: list[str]) -> list[str]:
    """The changed files that are test files: python's test_*.py and js/ts .test.* or .spec.* in any folder."""
    return [path for path in paths if _is_test_file(path)]


def _is_test_file(path: str) -> bool:
    """True when the path is a python test file or a javascript/typescript test file."""
    if path.endswith(".py") and path.rsplit("/", 1)[-1].startswith("test_"):
        return True
    return any(path.endswith(suffix) for suffix in _JS_TEST_SUFFIXES)


def _count_tests(repo: Path, revision: str, path: str) -> int:
    """Count the test lines of one file at one revision; a missing file counts 0.

    Python counts 'def test' and 'async def test'; JavaScript and TypeScript count lines whose first token is
    test(, it( or their .only/.skip forms, template-literal or quoted names alike. It stays a line count with no
    parser: tests generated in a loop count as one line."""
    result = run_git(repo, "show", f"{revision}:{path}")
    if result.returncode != 0:
        return 0
    if path.endswith(".py"):
        return sum(1 for line in result.stdout.splitlines() if _is_python_test_line(line))
    return sum(1 for line in result.stdout.splitlines() if _is_js_test_line(line))


def _is_python_test_line(line: str) -> bool:
    """True when the line starts a python test function, sync or async."""
    stripped = line.lstrip()
    return stripped.startswith("def test") or stripped.startswith("async def test")


def _is_js_test_line(line: str) -> bool:
    """True when the first token of the line is a javascript/typescript test call."""
    stripped = line.lstrip()
    return any(stripped.startswith(token) for token in _JS_TEST_STARTS)


def _test_counts(repo: Path, commit_hash: str, paths: list[str]) -> dict[str, tuple[int, int]]:
    """Return per-file test counts before and after one commit for its changed test files."""
    counts: dict[str, tuple[int, int]] = {}
    for path in _test_files(paths):
        counts[path] = (_count_tests(repo, f"{commit_hash}~1", path), _count_tests(repo, commit_hash, path))
    return counts


def _warnings_for(config: RunConfig, paths: list[str], per_file: dict[str, tuple[int, int]]) -> list[str]:
    """Return the plain warning texts of one commit's changed files; empty when clean."""
    warnings: list[str] = []
    warnings.extend(
        f"touches protected {path}" for path in paths if any(path.startswith(prefix) for prefix in config.protect)
    )
    for path, (before, after) in per_file.items():
        if after < before:
            warnings.append(f"fewer tests in {path} ({before} -> {after})")
    if len(paths) > 15:
        warnings.append(f"more than 15 files ({len(paths)})")
    return warnings
