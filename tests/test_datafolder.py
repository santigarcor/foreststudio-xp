"""Changing the data folder from Settings (all in temp folders)."""

import os
import sys

import pytest

from forest import config, datafolder


@pytest.fixture
def home(sandbox, monkeypatch):
    """The sandbox data folder plays the default ~/.foreststudio."""
    monkeypatch.setattr(config, "DEFAULT_DATA_ROOT", sandbox.root)
    monkeypatch.setattr(config, "LOCATION_FILE", sandbox.root / "location.toml")
    monkeypatch.setattr(config, "DATA_SOURCE", "default")
    monkeypatch.delenv("FORESTSTUDIO_HOME", raising=False)
    (sandbox.cache / "tiles" / "+06-076").mkdir(parents=True)
    (sandbox.cache / "tiles" / "+06-076" / "osm.json").write_text("{}")
    return sandbox


def test_location_file_round_trip(home, tmp_path):
    target = tmp_path / "it's here"            # apostrophe: TOML must still parse
    config.write_location(target)
    assert config.resolve_data_root() == (target, "custom")
    config.write_location(config.DEFAULT_DATA_ROOT)
    assert not config.LOCATION_FILE.exists()
    assert config.resolve_data_root() == (config.DEFAULT_DATA_ROOT, "default")


def test_env_var_wins(home, tmp_path, monkeypatch):
    config.write_location(tmp_path / "chosen")
    monkeypatch.setenv("FORESTSTUDIO_HOME", str(tmp_path / "env"))
    assert config.resolve_data_root() == (tmp_path / "env", "env")


def test_check_rejects_bad_targets(home):
    assert not datafolder.check("")["ok"]
    assert not datafolder.check("relative\\path")["ok"]
    assert not datafolder.check(str(home.root / "cache" / "x"))["ok"]          # inside current
    assert not datafolder.check(str(home.xplane / "ForestData"))["ok"]         # inside X-Plane
    assert datafolder.check(str(home.root))["same"]


def test_non_empty_foreign_folder_gets_a_subfolder(home, tmp_path):
    d = tmp_path / "Drive"
    d.mkdir()
    (d / "photos.jpg").write_bytes(b"x")
    r = datafolder.check(str(d))
    assert r["ok"] and r["target"] == str(d / "ForestStudio") and r["note"]


def test_move_and_back(home, tmp_path):
    new = tmp_path / "elsewhere" / "FS"
    r = datafolder.change(str(new))
    assert r["ok"] and r["restart"]
    assert (new / "cache" / "tiles" / "+06-076" / "osm.json").exists()
    assert not (home.root / "cache").exists()                     # moved, not copied
    assert config.resolve_data_root() == (new, "custom")
    assert list(home.root.iterdir()) == [config.LOCATION_FILE]    # only the pointer is left

    # the app would restart here; emulate it, then go back to the default
    config.DATA_ROOT, config.DATA_SOURCE = new, "custom"
    r = datafolder.change(str(home.root))
    assert r["ok"]
    assert (home.root / "cache" / "tiles" / "+06-076" / "osm.json").exists()
    assert not config.LOCATION_FILE.exists() and not new.exists()


def test_switch_to_existing_data_moves_nothing(home, tmp_path):
    other = tmp_path / "Other"
    (other / "cache").mkdir(parents=True)
    r = datafolder.change(str(other))
    assert r["ok"] and "switched" in r["notes"][0]
    assert (home.root / "cache" / "tiles" / "+06-076" / "osm.json").exists()   # untouched


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_xplane_link_follows_the_move(home, tmp_path):
    import _winapi

    pack = home.out / "forest_colombia" / "Earth nav data"
    pack.mkdir(parents=True)
    link = home.xplane / "Custom Scenery" / "forest_colombia"
    _winapi.CreateJunction(str(home.out / "forest_colombia"), str(link))
    new = tmp_path / "FS2"
    datafolder.change(str(new))
    assert os.path.realpath(link).lower() == str((new / "out" / "forest_colombia").resolve()).lower()
