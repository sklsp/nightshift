"""Build a wheel of this repo and check it contains what the code needs."""

import shutil
import zipfile
from pathlib import Path

import pytest
from setuptools import build_meta

REPO_ROOT = Path(__file__).resolve().parent.parent


def _wheel_file(directory: Path) -> Path:
    """The single .whl file in directory, or fail the test."""
    wheels = sorted(directory.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel in {directory}, found {[w.name for w in wheels]}"
    return wheels[0]


def test_wheel_contains_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wheel built from the repo holds the templates, every module and the console script."""
    build_dir = REPO_ROOT / "build"
    egg_info = REPO_ROOT / "src" / "nightshift.egg-info"
    pre_existing = {path for path in (build_dir, egg_info) if path.exists()}
    monkeypatch.chdir(REPO_ROOT)
    try:
        name = build_meta.build_wheel(str(tmp_path))
        wheel = _wheel_file(tmp_path)
        assert wheel.name == name
        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
            assert "nightshift/templates/start.md" in names, "wheel is missing nightshift/templates/start.md"
            assert "nightshift/templates/item.md" in names, "wheel is missing nightshift/templates/item.md"
            modules = sorted(p.name for p in (REPO_ROOT / "src" / "nightshift").glob("*.py"))
            assert modules, "no modules found under src/nightshift"
            for module in modules:
                assert f"nightshift/{module}" in names, f"wheel is missing nightshift/{module}"
            entry_points = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
            assert len(entry_points) == 1, f"expected one entry_points.txt, found {entry_points}"
            text = zf.read(entry_points[0]).decode("utf-8")
        assert "nightshift = nightshift.cli:main" in text
    finally:
        for path in (build_dir, egg_info):
            if path not in pre_existing and path.exists():
                shutil.rmtree(path)
