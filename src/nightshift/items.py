"""Parse an items.md file into a numbered list of items."""

from dataclasses import dataclass
from pathlib import Path


class ItemsError(ValueError):
    """The items.md file is missing or invalid."""


@dataclass(frozen=True)
class Item:
    """One item from items.md with its number, title and body."""

    number: int
    title: str
    body: str


def parse_items(text: str) -> list[Item]:
    """Split items.md text into numbered items."""
    lines = text.splitlines()
    headers: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if line.startswith("## "):
            headers.append((index, line[3:].strip()))

    if not headers:
        raise ItemsError("no items found")

    titles = [title for _, title in headers]
    for number, title in enumerate(titles, start=1):
        if not title:
            raise ItemsError(f"item {number} has an empty title")

    seen: dict[str, int] = {}
    for number, title in enumerate(titles, start=1):
        key = title.lower()
        if key in seen:
            raise ItemsError(f"duplicate title '{title}' in item {number}")
        seen[key] = number

    items: list[Item] = []
    for position, (start, title) in enumerate(headers):
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        body_lines = lines[start + 1 : end]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        if not body_lines:
            raise ItemsError(f"item {position + 1} has an empty body")
        items.append(Item(number=position + 1, title=title, body="\n".join(body_lines)))
    return items


def load_items(run_dir: Path) -> list[Item]:
    """Read and parse <run dir>/items.md."""
    path = run_dir / "items.md"
    if not path.is_file():
        raise ItemsError(f"missing items.md in {run_dir}")
    return parse_items(path.read_text(encoding="utf-8"))
