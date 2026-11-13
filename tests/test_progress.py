"""Tests for reading the run progress file."""

from pathlib import Path

from nightshift.progress import read_progress


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fresh_checklist_has_nothing_done(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-demo.md"
    _write(path, ["- [ ] T1. first item", "- [ ] T2. second item", "- [ ] T3. third item"])
    progress = read_progress(path, "T")
    assert progress.total == 3
    assert progress.done == []
    assert progress.blocked == []
    assert progress.next_item == 1
    assert progress.started is True
    assert progress.finished is False


def test_some_done_and_one_blocked(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-demo.md"
    _write(
        path,
        [
            "- [x] T1. first item (2 passed in 0.01s)",
            "- [ ] T2. second item BLOCKED",
            "- [ ] T3. third item",
        ],
    )
    progress = read_progress(path, "T")
    assert progress.total == 3
    assert progress.done == [1]
    assert progress.blocked == [2]
    assert progress.next_item == 3


def test_done_and_blocked_together_finish_the_run(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-demo.md"
    _write(path, ["- [x] T1. first item (ok)", "- [ ] T2. second item BLOCKED"])
    progress = read_progress(path, "T")
    assert progress.finished is True
    assert progress.next_item is None


def test_all_done_is_finished(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-demo.md"
    _write(path, ["- [x] T1. first item (ok)", "- [x] T2. second item (ok)"])
    progress = read_progress(path, "T")
    assert progress.finished is True
    assert progress.next_item is None


def test_missing_file_means_not_started(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-demo.md"
    progress = read_progress(path, "T")
    assert progress.total == 0
    assert progress.done == []
    assert progress.blocked == []
    assert progress.next_item is None
    assert progress.started is False
    assert progress.finished is False


def test_extra_text_after_the_title_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-demo.md"
    _write(
        path,
        [
            "- [x] T1. first item (47 passed in 0.24s)",
            "- [ ] T2. second item (waiting on review)",
        ],
    )
    progress = read_progress(path, "T")
    assert progress.total == 2
    assert progress.done == [1]
    assert progress.blocked == []
    assert progress.next_item == 2


def test_different_prefix(tmp_path: Path) -> None:
    path = tmp_path / "PROGRESS-nightshift1.md"
    _write(
        path,
        ["- [x] N1. build: package skeleton (2 passed in 0.01s)", "- [ ] N2. config: run.toml loader"],
    )
    progress = read_progress(path, "N")
    assert progress.total == 2
    assert progress.done == [1]
    assert progress.next_item == 2

    other = read_progress(path, "T")
    assert other.total == 0
    assert other.started is False
    assert other.finished is False
