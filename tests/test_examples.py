"""Build the bundled example run into a temporary directory."""

import shutil
from pathlib import Path

from nightshift.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_hello_run_builds(tmp_path: Path) -> None:
    """nightshift build renders all run files for the hello example."""
    shutil.copytree(EXAMPLES / "hello-run", tmp_path / "hello-run")
    shutil.copytree(EXAMPLES / "hello-repo", tmp_path / "hello-repo")
    assert main(["build", str(tmp_path / "hello-run")]) == 0
    run_dir = tmp_path / "hello-run"
    for name in ("00-start.md", "item-01.md", "item-02.md", "item-03.md", "checklist.txt"):
        assert (run_dir / name).is_file()
    checklist = (run_dir / "checklist.txt").read_text(encoding="utf-8")
    expected = (
        "- [ ] T1. Add a farewell function\n- [ ] T2. Test the farewell function\n- [ ] T3. Document the package\n"
    )
    assert checklist == expected
    start = (run_dir / "00-start.md").read_text(encoding="utf-8")
    assert "python -m pytest" in start
