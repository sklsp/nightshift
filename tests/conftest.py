"""Shared fixtures for tests that need a small git repo."""

import shutil
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    """Run one git command in repo and fail the test when it errors."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


@pytest.fixture(scope="session")
def git_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An initialized empty repo built once per session; tests copy it instead of re-initing."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    template = tmp_path_factory.mktemp("git-template") / "repo"
    template.mkdir()
    _git(template, "init", "-q", "-b", "main")
    _git(template, "config", "user.email", "night@test")
    _git(template, "config", "user.name", "Night")
    return template
