"""Build a markdown summary of a finished run for a pull request body."""

from pathlib import Path

from nightshift.config import load_config
from nightshift.gate import last_gate_line
from nightshift.git import run_git
from nightshift.items import load_items
from nightshift.progress import read_progress
from nightshift.supervise import read_supervisor_events

_SUPERVISOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("session ", "session"),
    ("agent did not start", "failed start"),
    ("agent looks stuck", "stuck report"),
    ("agent status unknown for", "unreadable-status give-up"),
)


def build_report(run_dir: Path) -> str:
    """Return the markdown summary of one run as a single string."""
    config = load_config(run_dir)
    items = load_items(run_dir)
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    progress = read_progress(progress_path, config.prefix)
    commits = _commit_lines(config.repo)

    lines: list[str] = [f"# {config.name}: {len(progress.done)}/{progress.total} items"]
    committed_times: list[int] = []
    for item in items:
        marker = f"{config.prefix}{item.number}"
        if item.number in progress.blocked:
            state_text = "BLOCKED"
        else:
            found = _find_commit(commits, f"{marker}:")
            if found is None:
                state_text = "not committed"
            else:
                commit_hash, commit_time = found
                state_text = commit_hash
                if committed_times:
                    state_text += f" ({_format_minutes(commit_time - committed_times[-1])})"
                committed_times.append(commit_time)
        lines.append(f"- {marker} {item.title}: {state_text}")

    if len(committed_times) >= 2:
        run_seconds = committed_times[-1] - committed_times[0]
        lines.append(f"run time: {_format_minutes(run_seconds)} (first to last item commit)")
    gate_line = last_gate_line(run_dir)
    if gate_line != "last gate: none":
        lines.append(gate_line)
    lines.extend(_supervisor_lines(run_dir))
    _append_section(lines, run_dir / "BUGS.md", "Bugs found")
    _append_section(lines, run_dir / "IDEAS.md", "Ideas")
    return "\n".join(lines) + "\n"


def _note_lines(path: Path) -> list[str]:
    """Content lines of one notes file: blank lines and a first '# ' heading dropped."""
    if not path.is_file():
        return []
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not kept and stripped.startswith("# "):
            continue
        kept.append(line)
    return kept


def _append_section(lines: list[str], path: Path, title: str) -> None:
    """Append '## <title>' plus the content lines of one notes file, when it has any."""
    note_lines = _note_lines(path)
    if not note_lines:
        return
    lines.append("")
    lines.append(f"## {title}")
    lines.extend(note_lines)


def _supervisor_lines(run_dir: Path) -> list[str]:
    """Return the '## Supervisor' section lines, or none when no event count is nonzero."""
    events = read_supervisor_events(run_dir)
    kept: list[tuple[int, str]] = []
    for prefix, label in _SUPERVISOR_PREFIXES:
        count = sum(1 for event in events if event["msg"].startswith(prefix))
        if count:
            kept.append((count, f"{label}s" if count != 1 else label))
    if not kept:
        return []
    lines = ["", "## Supervisor"]
    lines.extend(f"- {count} {label}" for count, label in kept)
    return lines


def _format_minutes(seconds: int) -> str:
    """Format a duration rounded to whole minutes, like '38 min' or '1 h 12 min'."""
    total = (seconds + 30) // 60
    if total < 60:
        return f"{total} min"
    hours, rest = divmod(total, 60)
    return f"{hours} h {rest} min"


def _commit_lines(repo: Path) -> list[tuple[str, int, str]]:
    """Return (hash, unix time, subject) for every commit of repo, newest first; empty when git is missing."""
    try:
        result = run_git(repo, "log", "--format=%h %ct %s")
    except FileNotFoundError:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    triples: list[tuple[str, int, str]] = []
    for line in result.stdout.rstrip("\n").splitlines():
        commit_hash, _, rest = line.partition(" ")
        unix_time_text, _, subject = rest.partition(" ")
        triples.append((commit_hash, int(unix_time_text), subject))
    return triples


def _find_commit(commits: list[tuple[str, int, str]], prefix: str) -> tuple[str, int] | None:
    """Return (hash, unix time) of the newest commit whose subject starts with prefix."""
    for commit_hash, unix_time, subject in commits:
        if subject.startswith(prefix):
            return (commit_hash, unix_time)
    return None
