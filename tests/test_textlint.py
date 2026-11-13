"""Tests for text linting."""

from pathlib import Path

from nightshift.textlint import lint_files, lint_text


def test_em_dash_reported_with_line_number() -> None:
    assert lint_text("ok\nbad \u2014 dash") == ["line 2: em dash"]


def test_en_dash_reported_with_line_number() -> None:
    assert lint_text("\u2013 first line") == ["line 1: en dash"]


def test_trailing_whitespace_reported_with_line_number() -> None:
    text = "ok\nbad" + "   "
    assert lint_text(text) == ["line 2: trailing whitespace"]


def test_clean_text_gives_no_problems() -> None:
    assert lint_text("one\ntwo\nthree") == []


def test_lint_files_returns_only_problem_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    good = tmp_path / "good.md"
    bad.write_text("x \u2014 y", encoding="utf-8")
    good.write_text("fine\n", encoding="utf-8")
    result = lint_files([bad, good])
    assert list(result) == [bad]
    assert result[bad] == ["line 1: em dash"]


def test_repo_docs_pass_lint() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    docs = [repo_root / "README.md", repo_root / "docs" / "LESSONS.md"]
    assert lint_files(docs) == {}
