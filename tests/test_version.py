"""The one version number reaches the app, the pack's CREDITS.txt and the package metadata."""

import re
import tomllib
from pathlib import Path

import forest
from forest import credits

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", forest.__version__)


def test_version_in_credits_and_about():
    assert forest.__version__ in credits.credits_text().splitlines()[0]
    assert credits.as_dict()["version"] == forest.__version__


def test_pyproject_reads_the_version_from_the_package():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in meta["project"]["dynamic"] and "version" not in meta["project"]
    assert meta["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "forest.__version__"}


def test_release_notes_exist_for_this_version():
    assert (ROOT / "docs" / "releases" / f"{forest.__version__}.md").is_file()
    assert f"## [{forest.__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
