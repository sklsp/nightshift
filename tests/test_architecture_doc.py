"""Tests for docs/ARCHITECTURE.md staying true to the code."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = ROOT / "docs" / "ARCHITECTURE.md"
SRC_DIR = ROOT / "src" / "nightshift"


def _doc_text() -> str:
    """Read the architecture doc."""
    return DOC_PATH.read_text(encoding="utf-8")


def test_every_mentioned_module_exists() -> None:
    text = _doc_text()
    stems = re.findall(r"src/nightshift/([A-Za-z0-9_]+)\.py", text)
    assert stems, "the doc must name at least one module as src/nightshift/<name>.py"
    for stem in stems:
        assert (SRC_DIR / f"{stem}.py").is_file(), f"doc names a missing file: src/nightshift/{stem}.py"


def test_every_module_is_mentioned() -> None:
    text = _doc_text()
    skipped = {"__init__.py", "__main__.py"}
    for path in sorted(SRC_DIR.glob("*.py")):
        if path.name in skipped:
            continue
        assert f"{path.stem}.py" in text, f"the doc never mentions {path.name}"


def test_has_mermaid_sequence_diagram() -> None:
    text = _doc_text()
    assert "```mermaid" in text, "missing a ```mermaid block"
    assert re.search(r"^sequenceDiagram$", text, flags=re.MULTILINE) is not None


def test_no_em_or_en_dash() -> None:
    text = _doc_text()
    assert "\u2014" not in text, "em dash found in the doc"
    assert "\u2013" not in text, "en dash found in the doc"
