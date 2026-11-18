"""Render the spec files for one nightshift run."""

from importlib.resources import files
from pathlib import Path
from string import Template

from nightshift.config import RunConfig, command_prefix, quote_arg
from nightshift.git import current_branch
from nightshift.items import Item
from nightshift.textlint import lint_files


class RenderError(Exception):
    """Raised when rendered files fail the text check."""


def render_run(config: RunConfig, items: list[Item], out_dir: Path, *, run_dir: Path | None = None) -> list[Path]:
    """Write 00-start.md, one file per item and checklist.txt into out_dir.

    Relative template paths resolve against run_dir when given, else against out_dir.
    """
    spec_dir = (run_dir if run_dir is not None else out_dir).resolve()
    templates_dir = _templates_dir(config, spec_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "00-start.md"]
    written[0].write_text(_start_text(config, len(items), spec_dir, templates_dir), encoding="utf-8", newline="\n")

    total = len(items)
    for item in items:
        path = out_dir / f"item-{item.number:02d}.md"
        path.write_text(_item_text(config, item, total, spec_dir, templates_dir), encoding="utf-8", newline="\n")
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


def _start_text(config: RunConfig, total: int, run_dir: Path, templates_dir: Path | None) -> str:
    """Render the 00-start.md text for one run."""
    template = Template(_template(templates_dir, "start.md"))
    item_paths = [(run_dir / f"item-{number:02d}.md").as_posix() for number in range(1, total + 1)]
    return _substitute(
        template,
        "start.md",
        {
            "name": config.name,
            "branch": config.branch or current_branch(config.repo) or "main",  # never empty backticks
            "prefix": config.prefix,
            "total": str(total),
            "first_item": item_paths[0],
            "items_list": "\n".join(f"  {path}" for path in item_paths),
            "checklist_file": (run_dir / "checklist.txt").as_posix(),
            "progress_file": _progress_path(config).as_posix(),
            "check_command": _check_command(config),
            "command": command_prefix(config),
            "run_dir": run_dir.resolve().as_posix(),
            "timeout_hint": str(config.timeout_hint_seconds),
            "protected": _protected_line(config),
        },
    )


def _item_text(config: RunConfig, item: Item, total: int, run_dir: Path, templates_dir: Path | None) -> str:
    """Render the spec file text for one item."""
    template = Template(_template(templates_dir, "item.md"))
    start_file = (run_dir / "00-start.md").as_posix()
    if item.number < total:
        next_item = (run_dir / f"item-{item.number + 1:02d}.md").as_posix()
        ending = f"open {next_item} and do that item. Do not look at it before this one is committed."
    else:
        ending = f"all items are done; send the final message described in {start_file}."
    return _substitute(
        template,
        "item.md",
        {
            "name": config.name,
            "number": str(item.number),
            "total": str(total),
            "title": item.title,
            "body": item.body,
            "id": f"{config.prefix}{item.number}",
            "start_file": start_file,
            "progress_file": _progress_path(config).as_posix(),
            "command": command_prefix(config),
            "ending": ending,
            "run_dir": run_dir.resolve().as_posix(),
        },
    )


def _progress_path(config: RunConfig) -> Path:
    """Return the path of the run's progress file in the repo."""
    return config.repo / f"PROGRESS-{config.name}.md"


def _check_command(config: RunConfig) -> str:
    """Join lint, every pre check and the test command into one check line."""
    parts = [" ".join(quote_arg(part) for part in command) for command in (config.lint, *config.pre) if command]
    parts.append(" ".join(quote_arg(part) for part in config.test))
    return " ; ".join(parts)


def _protected_line(config: RunConfig) -> str:
    """The RULES line naming protected paths, empty when protect is unset."""
    if not config.protect:
        return ""
    paths = ", ".join(f"`{path}`" for path in config.protect)
    return f"- **Never touch** these paths (the commits check flags any commit that does): {paths}."


def _templates_dir(config: RunConfig, run_dir: Path) -> Path | None:
    """Return the project's templates dir resolved against the run dir, or None when unset."""
    if not config.templates:
        return None
    path = Path(config.templates)
    if not path.is_absolute():
        path = run_dir / path
    return path


def _template(templates_dir: Path | None, name: str) -> str:
    """Read one template from the project dir when present, else from the package data."""
    if templates_dir is not None:
        path = templates_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return files("nightshift").joinpath(f"templates/{name}").read_text(encoding="utf-8")


def _substitute(template: Template, template_name: str, mapping: dict[str, str]) -> str:
    """Fill one template; raise RenderError naming the file and placeholder on a typo."""
    try:
        return template.substitute(mapping)
    except KeyError as exc:
        placeholder = exc.args[0] if exc.args else "?"
        raise RenderError(f"unknown placeholder ${placeholder} in {template_name}") from exc
