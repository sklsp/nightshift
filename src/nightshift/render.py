"""Render the spec files for one nightshift run."""

from importlib.resources import files
from pathlib import Path
from string import Template

from nightshift.config import RunConfig
from nightshift.items import Item


def render_run(config: RunConfig, items: list[Item], out_dir: Path) -> list[Path]:
    """Write 00-start.md, one file per item and checklist.txt into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "00-start.md"]
    written[0].write_text(_start_text(config, len(items)), encoding="utf-8", newline="\n")

    total = len(items)
    for item in items:
        path = out_dir / f"item-{item.number:02d}.md"
        path.write_text(_item_text(config, item, total), encoding="utf-8", newline="\n")
        written.append(path)

    checklist = [f"- [ ] {config.prefix}{item.number}. {item.title}" for item in items]
    checklist_path = out_dir / "checklist.txt"
    checklist_path.write_text("\n".join(checklist) + "\n", encoding="utf-8", newline="\n")
    written.append(checklist_path)
    return written


def _start_text(config: RunConfig, total: int) -> str:
    """Render the 00-start.md text for one run."""
    template = Template(_template("start.md"))
    return template.substitute(
        name=config.name,
        branch=config.branch,
        total=str(total),
        total_padded=f"{total:02d}",
        check_command=_check_command(config),
    )


def _item_text(config: RunConfig, item: Item, total: int) -> str:
    """Render the spec file text for one item."""
    template = Template(_template("item.md"))
    if item.number < total:
        ending = (
            f"open item-{item.number + 1:02d}.md and do that item. "
            "Do not look at it before this one is committed."
        )
    else:
        ending = "all items are done; send the final message described in 00-start.md."
    return template.substitute(
        name=config.name,
        number=str(item.number),
        total=str(total),
        title=item.title,
        body=item.body,
        id=f"{config.prefix}{item.number}",
        ending=ending,
    )


def _check_command(config: RunConfig) -> str:
    """Join the lint and test commands into one check line."""
    parts = [" ".join(config.lint)] if config.lint else []
    parts.append(" ".join(config.test))
    return " ; ".join(parts)


def _template(name: str) -> str:
    """Read a template file from the package data."""
    return files("nightshift").joinpath(f"templates/{name}").read_text(encoding="utf-8")
