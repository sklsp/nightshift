"""Tests for rendering run spec files."""

from pathlib import Path

import pytest

from nightshift.config import RunConfig
from nightshift.items import Item
from nightshift.render import RenderError, render_run


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        name="demo",
        repo=tmp_path / "repo",
        test=["pytest", "-q"],
        lint=["ruff", "check"],
        prefix="T",
    )


def _items() -> list[Item]:
    return [
        Item(number=1, title="first item", body="Do the first thing."),
        Item(number=2, title="second item", body="Line one.\nLine two."),
        Item(number=3, title="third item", body="Last body."),
    ]


def test_file_names_and_count(tmp_path: Path) -> None:
    out = tmp_path / "out"
    written = render_run(_config(tmp_path), _items(), out)
    names = [path.name for path in written]
    assert names == ["00-start.md", "item-01.md", "item-02.md", "item-03.md", "checklist.txt"]
    assert all(path.is_file() for path in written)


def test_checklist_lines(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "checklist.txt").read_text(encoding="utf-8")
    assert text == "- [ ] T1. first item\n- [ ] T2. second item\n- [ ] T3. third item\n"


def test_custom_command_renders_prefix_in_start_file(tmp_path: Path) -> None:
    config = RunConfig(
        name="demo", repo=tmp_path / "repo", test=["pytest"], command=["python", "-m", "nightshift"]
    )
    out = tmp_path / "out"
    render_run(config, _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "python -m nightshift gate start <run dir>" in text
    assert "python -m nightshift gate wait <run dir> 1" in text
    assert "python -m nightshift gate file <run dir> <test file> <module>" in text
    assert "python -m nightshift status <run dir>" in text


def test_custom_command_renders_prefix_in_item_files(tmp_path: Path) -> None:
    config = RunConfig(
        name="demo", repo=tmp_path / "repo", test=["pytest"], command=["python", "-m", "nightshift"]
    )
    out = tmp_path / "out"
    written = render_run(config, _items(), out)
    for path in written:
        if path.name.startswith("item-"):
            text = path.read_text(encoding="utf-8")
            assert "python -m nightshift gate start <run dir>" in text
            assert "python -m nightshift gate wait <run dir> 1" in text


def test_command_part_with_space_is_quoted_in_rendered_files(tmp_path: Path) -> None:
    config = RunConfig(
        name="demo",
        repo=tmp_path / "repo",
        test=["pytest"],
        command=["C:/Program Files/nightshift/python.exe", "-m", "nightshift"],
    )
    out = tmp_path / "out"
    written = render_run(config, _items(), out)
    for path in written:
        if path.name == "00-start.md" or path.name.startswith("item-"):
            text = path.read_text(encoding="utf-8")
            assert '"C:/Program Files/nightshift/python.exe" -m nightshift gate start <run dir>' in text


def test_item_file_has_number_title_body_and_next_pointer(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "item-01.md").read_text(encoding="utf-8")
    assert "item 1 of 1 to 3" in text
    assert "1. **first item**" in text
    assert "Do the first thing." in text
    assert "item-02.md" in text


def test_last_item_does_not_point_to_next(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "item-03.md").read_text(encoding="utf-8")
    assert "third item" in text
    assert "Last body." in text
    assert "item-04.md" not in text


def test_start_file_has_run_name_check_and_gate(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "nightshift run demo:" in text
    assert "ruff check ; pytest -q" in text
    assert "nightshift gate start <run dir>" in text
    assert "nightshift gate wait <run dir> 1" in text
    assert "nightshift gate wait <run dir> 2" in text


def test_no_lint_renders_test_command_only(tmp_path: Path) -> None:
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest", "-q"])
    out = tmp_path / "out"
    render_run(config, _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "ruff check ; pytest -q" not in text
    assert "pytest -q" in text


def test_render_twice_gives_identical_bytes(tmp_path: Path) -> None:
    out = tmp_path / "out"
    paths_a = render_run(_config(tmp_path), _items(), out)
    bytes_a = [path.read_bytes() for path in paths_a]
    paths_b = render_run(_config(tmp_path), _items(), out)
    assert [path.read_bytes() for path in paths_b] == bytes_a


def test_spec_files_use_absolute_paths(tmp_path: Path) -> None:
    config = _config(tmp_path)
    out = tmp_path / "out"
    render_run(config, _items(), out)
    run_dir = out.resolve()
    next_item = (run_dir / "item-02.md").as_posix()
    progress_file = (config.repo / f"PROGRESS-{config.name}.md").as_posix()
    start_text = (out / "00-start.md").read_text(encoding="utf-8")
    item1_text = (out / "item-01.md").read_text(encoding="utf-8")
    assert next_item in start_text
    assert next_item in item1_text
    assert progress_file in start_text


def test_tick_line_uses_real_prefix(tmp_path: Path) -> None:
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], prefix="QA")
    out = tmp_path / "out"
    written = render_run(config, _items(), out)
    start_text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "- [x] QA<n>. <title> (<last pytest line>)" in start_text
    for path in written:
        assert "<id>" not in path.read_text(encoding="utf-8")


def test_render_run_refuses_item_body_with_dash(tmp_path: Path) -> None:
    items = [Item(number=1, title="first item", body="bad \u2014 dash")]
    with pytest.raises(RenderError, match="fix items.md") as excinfo:
        render_run(_config(tmp_path), items, tmp_path / "out")
    message = str(excinfo.value)
    assert "item-01.md" in message
    assert "em dash" in message


def test_start_file_has_keep_the_why_rule(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "**Keep the why:**" in text
    assert "keep any design reason in the old text" in text
    assert "word into a comment. Never delete it." in text


def test_start_file_has_append_only_notes_rule(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "**Notes are append-only:**" in text
    assert "BUGS.md, IDEAS.md and BLOCKED.md only grow" in text
    assert "never rewrite or shorten them" in text


def test_lessons_md_has_a_section_per_new_rule() -> None:
    lessons_path = Path(__file__).resolve().parent.parent / "docs" / "LESSONS.md"
    lessons = lessons_path.read_text(encoding="utf-8")
    assert "## Keep the why when you rewrite text" in lessons
    assert "## Notes files are append-only" in lessons
