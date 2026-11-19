"""Build a markdown summary of a finished run for a pull request body."""

from pathlib import Path

from nightshift.commits import resolve_base
from nightshift.config import RunConfig, load_config
from nightshift.gate import last_gate_line
from nightshift.git import commit_log, run_git
from nightshift.items import Item, load_items
from nightshift.progress import Progress
from nightshift.review import item_warnings
from nightshift.supervise import gate_progress_path, read_run_progress, read_supervisor_events

_SUPERVISOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("session ", "session"),
    ("agent did not start", "failed start"),
    ("agent looks stuck", "stuck report"),
    ("agent status unknown for", "unreadable-status give-up"),
)


def _quiet(message: str) -> None:
    """No-op logger so report output stays clean markdown."""
    return None


def build_report(run_dir: Path) -> str:
    """Return the markdown summary of one run as a single string."""
    config = load_config(run_dir)
    items = load_items(run_dir)
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    # cleanup removes the repo file; the .gate copy keeps counts alive, and when neither exists say so
    has_progress = progress_path.is_file() or gate_progress_path(run_dir).is_file()
    progress = read_run_progress(progress_path, config.prefix, run_dir, log=_quiet)
    if has_progress:
        lines: list[str] = [f"# {config.name}: {len(progress.done)}/{progress.total} items"]
    else:
        lines = [f"# {config.name}", "progress file missing"]
    # only commits in the run's range, so a later run reusing the prefix never shows up here
    commits = run_commits(config)

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
                    state_text += f" ({format_minutes(commit_time - committed_times[-1])})"
                committed_times.append(commit_time)
        lines.append(f"- {marker} {item.title}: {state_text}")

    seconds = run_seconds(items, config, progress, commits)
    if seconds is not None:
        lines.append(f"run time: {format_minutes(seconds)} (first to last item commit)")
    gate_line = last_gate_line(run_dir)
    if gate_line != "last gate: none":
        lines.append(gate_line)
    lines.extend(_review_section(config, items, commits))
    lines.extend(_supervisor_lines(run_dir))
    _append_section(lines, run_dir / "BUGS.md", "Bugs found")
    _append_section(lines, run_dir / "IDEAS.md", "Ideas")
    return "\n".join(lines) + "\n"


def run_commits(config: RunConfig) -> list[tuple[str, int, str]]:
    """Return (hash, unix time, subject), newest first, of the commits in one run's range."""
    base = resolve_base(config)
    # the run's own branch when it exists: a later run's commits (other branches) never show up here
    tip = config.branch if config.branch and run_git(
        config.repo, "rev-parse", "--verify", "--quiet", f"{config.branch}^{{commit}}"
    ).returncode == 0 else "HEAD"
    return commit_log(config.repo, base=base, tip=tip)


def run_seconds(
    items: list[Item], config: RunConfig, progress: Progress, commits: list[tuple[str, int, str]]
) -> int | None:
    """Return the seconds from first to last item commit in item order, or None when fewer than two committed."""
    times: list[int] = []
    for item in items:
        if item.number in progress.blocked:
            continue
        found = _find_commit(commits, f"{config.prefix}{item.number}:")
        if found is not None:
            times.append(found[1])
    if len(times) < 2:
        return None
    return times[-1] - times[0]


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


def review_warnings(config: RunConfig, items: list[Item], commits: list[tuple[str, int, str]]) -> list[tuple[str, str]]:
    """Return (item marker, warning text) pairs for every committed item; empty when clean."""
    pairs: list[tuple[str, str]] = []
    for item in items:
        found = _find_commit(commits, f"{config.prefix}{item.number}:")
        if found is None:
            continue
        for warning in item_warnings(config, found[0]):
            pairs.append((f"{config.prefix}{item.number}", warning))
    return pairs


def _review_section(config: RunConfig, items: list[Item], commits: list[tuple[str, int, str]]) -> list[str]:
    """Return the '## Review' section lines with every warning review would print, or '- no warnings'."""
    body = [f"- {marker}: {warning}" for marker, warning in review_warnings(config, items, commits)]
    if not body:
        body.append("- no warnings")
    return ["", "## Review"] + body


def _event_count(events: list[dict[str, str]], prefix: str) -> int:
    """Return how many supervisor events start with one message prefix."""
    return sum(1 for event in events if event["msg"].startswith(prefix))


def session_count(run_dir: Path) -> int:
    """Return how many 'session N ended' events the run's supervisor logged."""
    return _event_count(read_supervisor_events(run_dir), "session ")


def _supervisor_lines(run_dir: Path) -> list[str]:
    """Return the '## Supervisor' section lines, or none when no event count is nonzero."""
    events = read_supervisor_events(run_dir)
    kept: list[tuple[int, str]] = []
    for prefix, label in _SUPERVISOR_PREFIXES:
        count = _event_count(events, prefix)
        if count:
            kept.append((count, f"{label}s" if count != 1 else label))
    if not kept:
        return []
    lines = ["", "## Supervisor"]
    lines.extend(f"- {count} {label}" for count, label in kept)
    return lines


def format_minutes(seconds: int) -> str:
    """Format a duration rounded to whole minutes, like '38 min' or '1 h 12 min'."""
    total = (seconds + 30) // 60
    if total < 60:
        return f"{total} min"
    hours, rest = divmod(total, 60)
    return f"{hours} h {rest} min"


def _find_commit(commits: list[tuple[str, int, str]], prefix: str) -> tuple[str, int] | None:
    """Return (hash, unix time) of the newest commit whose subject starts with prefix."""
    for commit_hash, unix_time, subject in commits:
        if subject.startswith(prefix):
            return (commit_hash, unix_time)
    return None
