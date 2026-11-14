"""Tests for loading and validating run.toml."""

from pathlib import Path

import pytest

from nightshift.config import ConfigError, RunConfig, load_config


def _write(run_dir: Path, text: str) -> None:
    (run_dir / "run.toml").write_text(text, encoding="utf-8")


MINIMAL = """\
[run]
name = "demo"
repo = "."

[check]
test = ["pytest", "-q"]
"""


def test_full_valid_file(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    _write(
        tmp_path,
        """\
[run]
name = "demo-run"
repo = "repo"
branch = "night"
prefix = "AB"

[check]
lint = ["ruff", "check"]
test = ["pytest", "-q"]
min_passed = 3
wait_seconds = 20
timeout_hint_seconds = 45

[agent]
launch = ["lms", "start"]
status = ["lms", "status"]
running_pattern = "is running"
poll_seconds = 10
max_sessions = 5
launch_grace_polls = 2
""",
    )
    cfg = load_config(tmp_path)
    assert isinstance(cfg, RunConfig)
    assert cfg.name == "demo-run"
    assert cfg.repo == tmp_path / "repo"
    assert cfg.branch == "night"
    assert cfg.prefix == "AB"
    assert cfg.lint == ["ruff", "check"]
    assert cfg.test == ["pytest", "-q"]
    assert cfg.min_passed == 3
    assert cfg.wait_seconds == 20
    assert cfg.timeout_hint_seconds == 45
    assert cfg.launch == ["lms", "start"]
    assert cfg.status == ["lms", "status"]
    assert cfg.running_pattern == "is running"
    assert cfg.poll_seconds == 10
    assert cfg.max_sessions == 5
    assert cfg.launch_grace_polls == 2


def test_defaults_when_optional_keys_missing(tmp_path: Path) -> None:
    _write(tmp_path, MINIMAL)
    cfg = load_config(tmp_path)
    assert cfg.branch == "main"
    assert cfg.prefix == "T"
    assert cfg.lint == []
    assert cfg.min_passed == 1
    assert cfg.wait_seconds == 25
    assert cfg.timeout_hint_seconds == 30
    assert cfg.launch == []
    assert cfg.status == []
    assert cfg.running_pattern == "running"
    assert cfg.poll_seconds == 30
    assert cfg.max_sessions == 20
    assert cfg.launch_grace_polls == 4


def test_relative_repo_resolved_against_run_dir(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    _write(tmp_path, MINIMAL.replace('repo = "."', 'repo = "repo"'))
    cfg = load_config(tmp_path)
    assert cfg.repo == tmp_path / "repo"


def test_absolute_repo_left_alone(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    _write(tmp_path, MINIMAL.replace('repo = "."', f'repo = "{target.as_posix()}"'))
    cfg = load_config(tmp_path)
    assert cfg.repo == target


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing run.toml"):
        load_config(tmp_path)


def test_bad_toml_includes_message(tmp_path: Path) -> None:
    _write(tmp_path, "[run]\nname = \n")
    with pytest.raises(ConfigError, match="invalid TOML in run.toml"):
        load_config(tmp_path)


def test_missing_required_name(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nrepo = "."\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="missing required key 'run.name'"):
        load_config(tmp_path)


def test_missing_required_repo(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "demo"\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="missing required key 'run.repo'"):
        load_config(tmp_path)


def test_missing_required_test(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "demo"\nrepo = "."\n\n[check]')
    with pytest.raises(ConfigError, match="missing required key 'check.test'"):
        load_config(tmp_path)


def test_wrong_type_str(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = 5\nrepo = "."\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="wrong type for 'run.name': expected str"):
        load_config(tmp_path)


def test_wrong_type_int(tmp_path: Path) -> None:
    _write(tmp_path, MINIMAL.replace('test = ["pytest", "-q"]', 'test = ["x"]\nmin_passed = "3"'))
    with pytest.raises(ConfigError, match="wrong type for 'check.min_passed': expected int"):
        load_config(tmp_path)


def test_bool_is_not_an_int(tmp_path: Path) -> None:
    _write(tmp_path, MINIMAL.replace('test = ["pytest", "-q"]', 'test = ["x"]\nwait_seconds = true'))
    with pytest.raises(ConfigError, match="wrong type for 'check.wait_seconds': expected int"):
        load_config(tmp_path)


def test_wrong_type_list_of_str(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "demo"\nrepo = "."\n\n[check]\ntest = "pytest"')
    with pytest.raises(ConfigError, match="wrong type for 'check.test': expected a list of str"):
        load_config(tmp_path)


def test_invalid_name_with_space_and_punctuation(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "bad name!"\nrepo = "."\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="invalid value for 'run.name'"):
        load_config(tmp_path)


def test_invalid_prefix_lowercase(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "demo"\nrepo = "."\nprefix = "ab"\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="invalid value for 'run.prefix'"):
        load_config(tmp_path)


def test_invalid_prefix_too_long(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "demo"\nrepo = "."\nprefix = "TOOLONG"\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="invalid value for 'run.prefix'"):
        load_config(tmp_path)


def test_unknown_top_level_key(tmp_path: Path) -> None:
    _write(tmp_path, MINIMAL + "\n[extra]\nfoo = 1\n")
    with pytest.raises(ConfigError, match="unknown key 'extra'"):
        load_config(tmp_path)


def test_unknown_section_key(tmp_path: Path) -> None:
    _write(tmp_path, '[run]\nname = "demo"\nrepo = "."\nbranchh = "x"\n\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="unknown key 'run.branchh'"):
        load_config(tmp_path)


def test_section_must_be_a_table(tmp_path: Path) -> None:
    _write(tmp_path, 'run = 5\n[check]\ntest = ["x"]')
    with pytest.raises(ConfigError, match="wrong type for 'run': expected a table"):
        load_config(tmp_path)


def test_config_error_is_a_value_error() -> None:
    assert issubclass(ConfigError, ValueError)
