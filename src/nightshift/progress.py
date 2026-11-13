"""Read the PROGRESS file an agent keeps for one run."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Progress:
    """State of one run as recorded in its progress file."""

    total: int
    done: list[int]
    blocked: list[int]
    next_item: int | None

    @property
    def started(self) -> bool:
        """True when the progress file lists at least one item."""
        return self.total > 0

    @property
    def finished(self) -> bool:
        """True when every item is done or blocked and there are items."""
        if self.total == 0:
            return False
        resolved = set(self.done) | set(self.blocked)
        return all(number in resolved for number in range(1, self.total + 1))


def read_progress(path: Path, prefix: str) -> Progress:
    """Read the progress file at path and summarize done and blocked items."""
    if not path.is_file():
        return Progress(total=0, done=[], blocked=[], next_item=None)

    pattern = re.compile(rf"^- \[( |x|X)\] {re.escape(prefix)}(\d+)\. .+$")
    seen: set[int] = set()
    done: set[int] = set()
    blocked: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = pattern.match(stripped)
        if not match:
            continue
        number = int(match.group(2))
        seen.add(number)
        if match.group(1).strip():
            done.add(number)
        elif stripped.endswith(" BLOCKED"):
            blocked.add(number)

    resolved = done | blocked
    next_item = next((number for number in sorted(seen) if number not in resolved), None)
    return Progress(total=len(seen), done=sorted(done), blocked=sorted(blocked), next_item=next_item)
