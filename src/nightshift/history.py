"""Summarise several runs as one row per run plus a total line."""

from dataclasses import dataclass
from pathlib import Path

from nightshift.commits import run_commits
from nightshift.config import ConfigError, load_config
from nightshift.items import ItemsError, load_items
from nightshift.report import format_minutes, recorded_commits, review_warnings, run_seconds, session_count
from nightshift.supervise import read_run_progress


def _quiet(message: str) -> None:
    """No-op logger so history output stays clean rows."""


@dataclass(frozen=True)
class RunSummary:
    """One run's numbers for a history row and the total line."""

    name: str
    done: int
    total: int
    seconds: int | None
    sessions: int
    warnings: int


def summarize_run(run_dir: Path) -> RunSummary:
    """Collect one run's item, duration, session and warning numbers the way report computes them."""
    config = load_config(run_dir)
    items = load_items(run_dir)
    progress_path = config.repo / f"PROGRESS-{config.name}.md"
    progress = read_run_progress(progress_path, config.prefix, run_dir, log=_quiet)
    commits = recorded_commits(run_dir, config.prefix, run_commits(config))
    return RunSummary(
        name=config.name,
        done=len(progress.done),
        total=progress.total,
        seconds=run_seconds(items, config, progress, commits),
        sessions=session_count(run_dir),
        warnings=len(review_warnings(config, items, commits)),
    )


def format_row(summary: RunSummary) -> str:
    """Format one run's row with its name, item counts, run time or -, sessions and warnings."""
    run_time = "-" if summary.seconds is None else format_minutes(summary.seconds)
    return (
        f"{summary.name}  {summary.done}/{summary.total} items  {run_time}  "
        f"{summary.sessions} session(s)  {summary.warnings} warning(s)"
    )


def history_lines(run_dirs: list[Path]) -> list[str]:
    """Return one row per run dir, a '<path>: missing' note for absent ones, and the total line."""
    rows: list[str] = []
    summaries: list[RunSummary] = []
    for run_dir in run_dirs:
        if not run_dir.is_dir():
            rows.append(f"{run_dir}: missing")
            continue
        try:
            summary = summarize_run(run_dir)
        except (ConfigError, ItemsError) as exc:
            rows.append(f"{run_dir}: {exc}")
            continue
        summaries.append(summary)
        rows.append(format_row(summary))
    done = sum(summary.done for summary in summaries)
    total = sum(summary.total for summary in summaries)
    timed = [summary.seconds for summary in summaries if summary.seconds is not None]
    run_time = format_minutes(sum(timed)) if timed else "-"
    sessions = sum(summary.sessions for summary in summaries)
    warnings = sum(summary.warnings for summary in summaries)
    rows.append(f"{len(summaries)} runs, {done}/{total} items, {run_time}, {sessions} sessions, {warnings} warnings")
    return rows
