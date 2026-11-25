"""Release packaging (tools/package/build.py): version source, tag check, the shipped file set."""

import importlib.util
import sys
from pathlib import Path

import pytest

import forest
from forest import config

ROOT = Path(__file__).resolve().parent.parent


def _build():
    spec = importlib.util.spec_from_file_location("fsxp_package_build", ROOT / "tools" / "package" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_reads_the_package_version():
    assert _build().read_version() == forest.__version__


def test_tag_must_match_the_version(monkeypatch):
    b = _build()
    with monkeypatch.context() as m:
        m.setenv("GITHUB_REF_TYPE", "tag")
        m.setenv("GITHUB_REF_NAME", f"v{forest.__version__}")
        b.check_tag(forest.__version__)            # fine
        m.setenv("GITHUB_REF_NAME", "v9.9.9")
        with pytest.raises(SystemExit, match="doesn't match"):
            b.check_tag(forest.__version__)
    with monkeypatch.context() as m:               # a branch build: no tag to check
        m.delenv("GITHUB_REF_TYPE", raising=False)
        m.delenv("GITHUB_REF", raising=False)
        b.check_tag("0.0.0")


def test_shipped_files_are_tracked_and_complete():
    b = _build()
    _, _, xp = b.target()
    files = {p.as_posix() for p in b.tracked_files(b.APP_ITEMS + [f"tools/xptools/{xp}"])}
    for need in ["forest/app/main.py", "forest/app/ui/index.html", "forest/app/ui/vendor/leaflet/leaflet.min.js",
                 "config/regions/colombia.toml", "config/rules/_global.toml", "LICENSE"]:
        assert need in files, need
    assert not any(f.startswith(("tests/", ".github/")) or "__pycache__" in f for f in files)
    assert any(f.startswith(f"tools/xptools/{xp}/DSFTool") for f in files)


def test_program_folders_from_a_checkout():
    # A source checkout has no bundled python/ next to it: only the code folder is the program.
    folders = config.program_folders()
    assert folders[0] == config.REPO_ROOT
    if not (config.REPO_ROOT.parent / "python").is_dir():
        assert folders == [config.REPO_ROOT]


def test_installed_layout_counts_the_install_folder(tmp_path, monkeypatch):
    install = tmp_path / "ForestStudio XP"
    (install / "python").mkdir(parents=True)
    (install / "app").mkdir()
    with monkeypatch.context() as m:
        m.setattr(config, "REPO_ROOT", install / "app")
        m.setattr(sys, "base_prefix", str(install / "python"))
        assert config.program_folders() == [install / "app", install]
