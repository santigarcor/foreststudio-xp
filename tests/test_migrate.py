"""Old in-repository data moves to the data folder once, without overwriting anything."""

import os

import pytest

from forest import config, migrate


def _old_layout(repo):
    (repo / "cache" / "tiles" / "+06-076").mkdir(parents=True)
    (repo / "cache" / "tiles" / "+06-076" / "osm.json").write_text("{}")
    (repo / "cache" / "history.jsonl").write_text("old\n")
    (repo / "out" / "forest_colombia" / "Earth nav data" / "+00-080").mkdir(parents=True)
    (repo / "out" / "forest_colombia" / "Earth nav data" / "+00-080" / "+06-076.dsf").write_bytes(b"dsf")


def test_moves_old_data_and_keeps_existing(sandbox):
    repo = sandbox.repo
    _old_layout(repo)
    (sandbox.cache / "history.jsonl").write_text("new\n")         # already in the data folder
    (repo / "config" / "local.toml").write_text("[xplane]\nroot = 'X'\n")
    (sandbox.root / "config" / "local.toml").unlink()
    assert migrate.pending()

    notes = migrate.run()

    assert (sandbox.cache / "tiles" / "+06-076" / "osm.json").exists()
    assert (sandbox.out / "forest_colombia" / "Earth nav data" / "+00-080" / "+06-076.dsf").read_bytes() == b"dsf"
    assert (sandbox.root / "config" / "local.toml").read_text().startswith("[xplane]")
    assert (sandbox.cache / "history.jsonl").read_text() == "new\n"       # never overwritten
    assert (repo / "cache" / "history.jsonl").exists()                     # old copy left, reported
    assert any("kept" in n for n in notes)
    assert not (repo / "out").exists() and not (repo / "config" / "local.toml").exists()
    assert all("kept" in n for n in migrate.run())        # a second run only re-reports the conflict


def test_nothing_to_do(sandbox):
    assert not migrate.pending() and migrate.run() == []


def test_default_profiles_are_seeded_not_overwritten(sandbox):
    (sandbox.repo / "config" / "profiles" / "medium.toml").write_text("grid_cells = 6000\n")
    mine = sandbox.root / "config" / "profiles" / "low.toml"
    mine.write_text("grid_cells = 1\n")
    (sandbox.repo / "config" / "profiles" / "low.toml").write_text("grid_cells = 3000\n")
    assert config.profile_path("medium").read_text() == "grid_cells = 6000\n"   # copied
    assert mine.read_text() == "grid_cells = 1\n"                                # user's kept


def test_xplane_link_is_repointed(sandbox):
    from forest import pack

    _old_layout(sandbox.repo)
    link = sandbox.xplane / "Custom Scenery" / "forest_colombia"
    pack.make_link(sandbox.repo / "out" / "forest_colombia", link)
    migrate.run()
    assert os.path.realpath(link).lower() == str((sandbox.out / "forest_colombia").resolve()).lower()
    assert (link / "Earth nav data" / "+00-080" / "+06-076.dsf").exists()
