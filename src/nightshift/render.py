"""Render the spec files for one nightshift run."""

from importlib.resources import files
from pathlib import Path
from string import Template

from nightshift.config import RunConfig, command_prefix
from nightshift.items import Item
from nightshift.textlint import lint_files


class RenderError(Exception):
    """Raised when rendered files fail the text check."""


def render_run(config: RunConfig, items: list[Item], out_dir: Path) -> list[Path]:
    """Write 00-start.md, one file per item and checklist.txt into out_dir."""
    run_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "00-start.md"]
    written[0].write_text(_start_text(config, len(items), run_dir), encoding="utf-8", newline="\n")

    total = len(items)
    for item in items:
        path = out_dir / f"item-{item.number:02d}.md"
        path.write_text(_item_text(config, item, total, run_dir), encoding="utf-8", newline="\n")
        written.append(path)

    checklist = [f"- [ ] {config.prefix}{item.number}. {item.title}" for item in items]
    checklist_path = out_dir / "checklist.txt"
    checklist_path.write_text("\n".join(checklist) + "\n", encoding="utf-8", newline="\n")
    written.append(checklist_path)

    problems = lint_files(written)
    if problems:
        raise RenderError(_render_error_message(problems))
    return written


def _render_error_message(problems: dict[Path, list[str]]) -> str:
    """Build the RenderError message listing every file and problem."""
    lines = ["rendered files have text problems (fix items.md):"]
    for path, found in problems.items():
        lines.append(f"{path.name}:")
        lines.extend(f"  {problem}" for problem in found)
    return "\n".join(lines)


def _start_text(config: RunConfig, total: int, run_dir: Path) -> str:
    """Render the 00-start.md text for one run."""
    template = Template(_template("start.md"))
    item_paths = [(run_dir / f"item-{number:02d}.md").as_posix() for number in range(1, total + 1)]
    return template.substitute(
        name=config.name,
        branch=config.branch,
        prefix=config.prefix,
        total=str(total),
        first_item=item_paths[0],
        items_list="\n".join(f"  {path}" for path in item_paths),
        checklist_file=(run_dir / "checklist.txt").as_posix(),
        progress_file=_progress_path(config).as_posix(),
        check_command=_check_command(config),
        command=command_prefix(config),
        run_dir=run_dir.resolve().as_posix(),
    )


def _item_text(config: RunConfig, item: Item, total: int, run_dir: Path) -> str:
    """Render the spec file text for one item."""
    template = Template(_template("item.md"))
    start_file = (run_dir / "00-start.md").as_posix()
    if item.number < total:
        next_item = (run_dir / f"item-{item.number + 1:02d}.md").as_posix()
        ending = f"open {next_item} and do that item. Do not look at it before this one is committed."
    else:
        ending = f"all items are done; send the final message described in {start_file}."
    return template.substitute(
        name=config.name,
        number=str(item.number),
        total=str(total),
        title=item.title,
        body=item.body,
        id=f"{config.prefix}{item.number}",
        start_file=start_file,
        progress_file=_progress_path(config).as_posix(),
        command=command_prefix(config),
        ending=ending,
        run_dir=run_dir.resolve().as_posix(),
    )


def _progress_path(config: RunConfig) -> Path:
    """Return the path of the run's progress file in the repo."""
    return config.repo / f"PROGRESS-{config.name}.md"


def _check_command(config: RunConfig) -> str:
    """Join lint, every pre check and the test command into one check line."""
    parts = [" ".join(command) for command in (config.lint, *config.pre) if command]
    parts.append(" ".join(config.test))
    return " ; ".join(parts)


def _template(name: str) -> str:
    """Read a template file from the package data."""
    return files("nightshift").joinpath(f"templates/{name}").read_text(encoding="utf-8")
