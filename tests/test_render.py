"""Tests for rendering run spec files."""

from pathlib import Path

from nightshift.config import RunConfig
from nightshift.items import Item
from nightshift.render import render_run


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
    first = tmp_path / "one"
    second = tmp_path / "two"
    paths_a = render_run(_config(tmp_path), _items(), first)
    paths_b = render_run(_config(tmp_path), _items(), second)
    for path_a, path_b in zip(paths_a, paths_b, strict=True):
        assert path_a.read_bytes() == path_b.read_bytes()
