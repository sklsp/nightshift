"""Build a markdown summary of a finished run for a pull request body."""

import subprocess
from pathlib import Path

from nightshift.config import load_config
from nightshift.gate import last_gate_line
from nightshift.items import load_items
from nightshift.progress import read_progress


def build_report(run_dir: Path) -> str:
    """Return the markdown summary of one run as a single string."""
    config = load_config(run_dir)
    items = load_items(run_dir)
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    progress = read_progress(progress_path, config.prefix)
    commits = _commit_lines(config.repo)

    lines: list[str] = [f"# {config.name}: {len(progress.done)}/{progress.total} items"]
    for item in items:
        marker = f"{config.prefix}{item.number}"
        if item.number in progress.blocked:
            state_text = "BLOCKED"
        else:
            commit_hash = _find_commit(commits, f"{marker}:")
            state_text = commit_hash or "not committed"
        lines.append(f"- {marker} {item.title}: {state_text}")

    gate_line = last_gate_line(run_dir)
    if gate_line != "last gate: none":
        lines.append(gate_line)
    return "\n".join(lines) + "\n"


def _commit_lines(repo: Path) -> list[tuple[str, str]]:
    """Return (hash, subject) for every commit of repo, newest first; empty when git is missing."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "--format=%h %s"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    pairs: list[tuple[str, str]] = []
    for line in result.stdout.rstrip("\n").splitlines():
        commit_hash, _, subject = line.partition(" ")
        pairs.append((commit_hash, subject))
    return pairs


def _find_commit(commits: list[tuple[str, str]], prefix: str) -> str | None:
    """Return the hash of the newest commit whose subject starts with prefix."""
    for commit_hash, subject in commits:
        if subject.startswith(prefix):
            return commit_hash
    return None
