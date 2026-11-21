"""Tests for rendering run spec files."""

from pathlib import Path

import pytest

from nightshift.config import ConfigError, RunConfig, load_config
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
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], command=["python", "-m", "nightshift"])
    out = tmp_path / "out"
    render_run(config, _items(), out)
    run_dir = out.resolve().as_posix()
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert f"python -m nightshift gate start {run_dir}" in text
    assert f"python -m nightshift gate wait {run_dir} 1" in text
    assert f"python -m nightshift gate file {run_dir} <test file> <module>" in text
    assert f"python -m nightshift status {run_dir}" in text


def test_start_file_with_protect_contains_rule_line(tmp_path: Path) -> None:
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], protect=[".github/", "src/policy.py"])
    out = tmp_path / "out"
    render_run(config, _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert (
        "- **Never touch** these paths (the commits check flags any commit that does): `.github/`, `src/policy.py`."
        in text
    )


def test_start_file_without_protect_has_no_never_touch_line(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "Never touch" not in text


def test_custom_command_renders_prefix_in_item_files(tmp_path: Path) -> None:
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], command=["python", "-m", "nightshift"])
    out = tmp_path / "out"
    written = render_run(config, _items(), out)
    run_dir = out.resolve().as_posix()
    for path in written:
        if path.name.startswith("item-"):
            text = path.read_text(encoding="utf-8")
            assert f"python -m nightshift gate start {run_dir}" in text
            assert f"python -m nightshift gate wait {run_dir} 1" in text


def test_command_part_with_space_is_quoted_in_rendered_files(tmp_path: Path) -> None:
    config = RunConfig(
        name="demo",
        repo=tmp_path / "repo",
        test=["pytest"],
        command=["C:/Program Files/nightshift/python.exe", "-m", "nightshift"],
    )
    out = tmp_path / "out"
    written = render_run(config, _items(), out)
    run_dir = out.resolve().as_posix()
    for path in written:
        if path.name == "00-start.md" or path.name.startswith("item-"):
            text = path.read_text(encoding="utf-8")
            assert f'"C:/Program Files/nightshift/python.exe" -m nightshift gate start {run_dir}' in text


def test_no_rendered_file_contains_run_dir_placeholder(tmp_path: Path) -> None:
    out = tmp_path / "out"
    written = render_run(_config(tmp_path), _items(), out)
    for path in written:
        assert "<run dir>" not in path.read_text(encoding="utf-8")


def test_start_file_gate_commands_use_absolute_posix_run_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    run_dir = out.resolve().as_posix()
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert f"nightshift gate start {run_dir}" in text
    assert f"nightshift gate wait {run_dir} 1" in text
    assert f"nightshift gate wait {run_dir} 2" in text
    assert f"nightshift status {run_dir}" in text


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
    run_dir = out.resolve().as_posix()
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "nightshift run demo:" in text
    assert "ruff check ; pytest -q" in text
    assert f"nightshift gate start {run_dir}" in text
    assert f"nightshift gate wait {run_dir} 1" in text
    assert f"nightshift gate wait {run_dir} 2" in text


def test_start_file_gate_rule_names_configured_limit(tmp_path: Path) -> None:
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], timeout_hint_seconds=45)
    out = tmp_path / "out"
    render_run(config, _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "20 seconds" not in text
    assert "longer than 45 seconds, run it through the gate" in text


def test_start_file_gate_section_mentions_extra_args(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    run_dir = out.resolve().as_posix()
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert f"nightshift gate start {run_dir} -- <extra args>" in text


def test_check_command_quotes_arguments_with_space_or_glob(tmp_path: Path) -> None:
    config = RunConfig(
        name="demo",
        repo=tmp_path / "repo",
        lint=["C:/Program Files/ruff/ruff.exe", "check"],
        pre=[["node", "--test", "tests/web/**/*.test.mjs"], ["grep", "-rn", "[Tt]odo?", "src"]],
        test=["pytest", "-q"],
    )
    out = tmp_path / "out"
    render_run(config, _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert '"C:/Program Files/ruff/ruff.exe" check' in text
    assert 'node --test "tests/web/**/*.test.mjs"' in text
    assert 'grep -rn "[Tt]odo?" src' in text
    assert "pytest -q" in text


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
    with pytest.raises(RenderError, match=r"fix items\.md") as excinfo:
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


def test_start_file_has_never_kill_by_name_rule(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "**Never kill processes by name**" in text
    assert "`Stop-Process -Name python`" in text
    assert "`taskkill /IM python.exe`" in text
    assert "`pkill python`" in text
    assert "by the pid you saved when you started it." in text


def test_start_file_has_never_push_rule(tmp_path: Path) -> None:
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "**Never push**" in text
    assert "`git push`" in text
    assert "no new remote branches" in text
    assert "the operator reviews, rebases and pushes after the run." in text


def test_lessons_md_has_a_section_per_new_rule() -> None:
    lessons_path = Path(__file__).resolve().parent.parent / "docs" / "LESSONS.md"
    lessons = lessons_path.read_text(encoding="utf-8")
    assert "## Keep the why when you rewrite text" in lessons
    assert "## Notes files are append-only" in lessons
    assert "## Never kill by name" in lessons
    assert "## Never push" in lessons


def test_start_file_names_a_branch_when_none_is_configured(tmp_path: Path) -> None:
    # branch is optional since 0.9.0; the spec must never say "branch ``". The repo here is not a git repo,
    # so the fallback is "main".
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "branch `main`" in text
    assert "``" not in text


def test_missing_repo_folder_skips_git_and_uses_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: object, **kwargs: object) -> str:
        raise AssertionError("current_branch must not be called when the repo folder does not exist")

    monkeypatch.setattr("nightshift.render.current_branch", _fail)
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "branch `main`" in text


def test_existing_repo_folder_still_asks_git_for_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "repo").mkdir()
    calls: list[Path] = []

    def _fake(repo: Path) -> str:
        calls.append(repo)
        return "dev"

    monkeypatch.setattr("nightshift.render.current_branch", _fake)
    out = tmp_path / "out"
    render_run(_config(tmp_path), _items(), out)
    text = (out / "00-start.md").read_text(encoding="utf-8")
    assert "branch `dev`" in text
    assert calls == [tmp_path / "repo"]


def _write_run_toml(run_dir: Path, text: str) -> None:
    """Write a run.toml into the given directory."""
    (run_dir / "run.toml").write_text(text, encoding="utf-8")


RUN_TOML = """\
[run]
name = "demo"
repo = "."

[check]
test = ["pytest", "-q"]
"""


def test_project_templates_dir_overrides_start_only(tmp_path: Path) -> None:
    out = tmp_path / "out"
    tpl = out / "tpl"
    tpl.mkdir(parents=True)
    (tpl / "start.md").write_text("custom start for $name on $branch\n", encoding="utf-8")
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], templates="tpl")
    render_run(config, _items(), out)
    assert (out / "00-start.md").read_text(encoding="utf-8") == "custom start for demo on main\n"
    item_text = (out / "item-01.md").read_text(encoding="utf-8")
    assert "item 1 of 1 to 3" in item_text
    assert "WHEN DONE:" in item_text


def test_project_templates_dir_overrides_both_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "start.md").write_text("custom start $name, costs $$5\n", encoding="utf-8")
    (tpl / "item.md").write_text("custom item $number of $total: $title ($id)\n", encoding="utf-8")
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], templates=tpl.as_posix())
    render_run(config, _items(), out)
    assert (out / "00-start.md").read_text(encoding="utf-8") == "custom start demo, costs $5\n"
    assert (out / "item-01.md").read_text(encoding="utf-8") == "custom item 1 of 3: first item (T1)\n"


def test_relative_templates_path_resolved_against_run_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    tpl = out / "nested" / "tpl"
    tpl.mkdir(parents=True)
    (tpl / "start.md").write_text("relative start $name\n", encoding="utf-8")
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], templates="nested/tpl")
    render_run(config, _items(), out)
    assert (out / "00-start.md").read_text(encoding="utf-8") == "relative start demo\n"


def test_unknown_placeholder_in_project_start_template_raises(tmp_path: Path) -> None:
    out = tmp_path / "out"
    tpl = out / "tpl"
    tpl.mkdir(parents=True)
    (tpl / "start.md").write_text("custom start $bogus\n", encoding="utf-8")
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], templates="tpl")
    with pytest.raises(RenderError, match=r"unknown placeholder \$bogus in start\.md"):
        render_run(config, _items(), out)


def test_unknown_placeholder_in_project_item_template_raises(tmp_path: Path) -> None:
    out = tmp_path / "out"
    tpl = out / "tpl"
    tpl.mkdir(parents=True)
    (tpl / "item.md").write_text("custom item $typo\n", encoding="utf-8")
    config = RunConfig(name="demo", repo=tmp_path / "repo", test=["pytest"], templates="tpl")
    with pytest.raises(RenderError, match=r"unknown placeholder \$typo in item\.md"):
        render_run(config, _items(), out)


def test_config_templates_key_set(tmp_path: Path) -> None:
    _write_run_toml(
        tmp_path,
        '[run]\nname = "demo"\nrepo = "."\ntemplates = "tpl"\n\n[check]\ntest = ["pytest", "-q"]\n',
    )
    cfg = load_config(tmp_path)
    assert cfg.templates == "tpl"


def test_config_templates_key_unset_defaults_to_empty(tmp_path: Path) -> None:
    _write_run_toml(tmp_path, RUN_TOML)
    cfg = load_config(tmp_path)
    assert cfg.templates == ""


def test_config_templates_key_wrong_type_is_rejected(tmp_path: Path) -> None:
    _write_run_toml(
        tmp_path,
        '[run]\nname = "demo"\nrepo = "."\ntemplates = 5\n\n[check]\ntest = ["pytest", "-q"]\n',
    )
    with pytest.raises(ConfigError, match=r"wrong type for 'run\.templates': expected str"):
        load_config(tmp_path)
