"""Tests for parsing items.md files."""

import dataclasses
from pathlib import Path

import pytest

from nightshift.items import Item, ItemsError, load_items, parse_items


def test_two_items_with_bodies() -> None:
    text = (
        "# Preamble that is ignored\n"
        "\n"
        "## First item\n"
        "\n"
        "Do the first thing.\n"
        "Then more detail.\n"
        "\n"
        "## Second item\n"
        "\n"
        "- one\n"
        "- two\n"
    )
    items = parse_items(text)
    assert [item.number for item in items] == [1, 2]
    assert items[0].title == "First item"
    assert items[0].body == "Do the first thing.\nThen more detail."
    assert items[1].title == "Second item"
    assert items[1].body == "- one\n- two"


def test_preamble_before_first_header_is_ignored() -> None:
    text = "nightshift run demo, items 1 to 2.\nRules apply.\n\n## Only item\n\nBody line.\n"
    items = parse_items(text)
    assert len(items) == 1
    assert items[0].number == 1
    assert items[0].title == "Only item"
    assert items[0].body == "Body line."


def test_triple_hash_inside_body_stays_in_body() -> None:
    text = "## Top\n\n### Sub heading\nDetail under sub.\n"
    items = parse_items(text)
    assert len(items) == 1
    assert items[0].body == "### Sub heading\nDetail under sub."


def test_blank_lines_stripped_from_body_edges() -> None:
    text = "## T\n\n\nline one\n\n\nline two\n\n"
    items = parse_items(text)
    assert items[0].body == "line one\n\n\nline two"


def test_item_is_frozen() -> None:
    item = Item(number=1, title="t", body="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.title = "x"  # type: ignore[misc]  # the write must fail


def test_no_items_raises() -> None:
    with pytest.raises(ItemsError, match="no items found"):
        parse_items("just a preamble\nwith no headers\n")


def test_empty_text_has_no_items() -> None:
    with pytest.raises(ItemsError, match="no items found"):
        parse_items("")


def test_empty_title_raises_with_number() -> None:
    with pytest.raises(ItemsError, match="item 2 has an empty title"):
        parse_items("## One\nbody a\n\n## \nbody b\n")


def test_duplicate_title_case_insensitive() -> None:
    text = "## Alpha\nbody a\n\n## alpha\nbody b\n"
    with pytest.raises(ItemsError, match="duplicate title 'alpha' in item 2"):
        parse_items(text)


def test_empty_body_raises_with_number() -> None:
    text = "## One\n\n## Two\nhas body\n"
    with pytest.raises(ItemsError, match="item 1 has an empty body"):
        parse_items(text)


def test_load_items_reads_file(tmp_path: Path) -> None:
    (tmp_path / "items.md").write_text("## Only\n\nBody.\n", encoding="utf-8")
    items = load_items(tmp_path)
    assert [(item.number, item.title, item.body) for item in items] == [(1, "Only", "Body.")]


def test_load_items_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ItemsError, match=r"missing items\.md"):
        load_items(tmp_path)
