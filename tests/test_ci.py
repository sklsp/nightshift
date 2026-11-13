"""Check the GitHub Actions workflow file by reading it as text."""

from pathlib import Path

CI_FILE = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _matrix_block(text: str) -> list[str]:
    """Return the stripped lines indented under the matrix key of the workflow."""
    block: list[str] = []
    indent = 0
    inside = False
    for line in text.splitlines():
        if not line.strip():
            continue
        current = len(line) - len(line.lstrip(" "))
        if not inside:
            if line.strip().startswith("matrix:"):
                inside = True
                indent = current
            continue
        if current <= indent:
            break
        block.append(line.strip())
    return block


def test_workflow_matrix() -> None:
    """The workflow matrix names both OSes and all three Python versions."""
    text = CI_FILE.read_text(encoding="utf-8")
    block = _matrix_block(text)
    os_line = next(line for line in block if line.startswith("os:"))
    assert "ubuntu-latest" in os_line
    assert "windows-latest" in os_line
    py_line = next(line for line in block if line.startswith("python-version:"))
    for version in ("3.11", "3.12", "3.13"):
        assert version in py_line
