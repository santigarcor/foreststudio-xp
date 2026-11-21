"""Disk usage report, clean-ups and deleting tiles never touch anything outside the project."""

import pytest

from forest import diskusage
from forest.tiles import Tile


def _fake_files(sandbox):
    built, unbuilt = Tile.parse("+06-076"), Tile.parse("+06-075")
    for t in (built, unbuilt):
        (t.cache / "worldcover.tif").write_bytes(b"w" * 1000)
        (t.cache / "osm.json").write_bytes(b"o" * 2000)
    (built.cache / "osm_parts").mkdir()
    (built.cache / "osm_parts" / "0_0.json").write_bytes(b"x" * 2_000_000)    # leftover (makes it the largest)
    dsf = sandbox.out / "forest_colombia" / "Earth nav data" / "+00-080" / "+06-076.dsf"
    dsf.parent.mkdir(parents=True)
    dsf.write_bytes(b"d" * 3000)
    studio = sandbox.out / "studio_colombia"
    studio.mkdir()
    (studio / "+06-076.stats.json").write_text("{}")
    (studio / "+06-076.txt").write_bytes(b"t" * 4000)                        # kept DSF text = leftover
    return built, unbuilt, dsf


def test_usage_categories(sandbox):
    _fake_files(sandbox)
    u = diskusage.usage()
    cats = {c["key"]: c for c in u["categories"]}
    assert set(cats) >= {"tile_data", "osm_country", "ecoregions", "built", "studio", "jobs", "leftovers"}
    assert u["cleanable"]["leftovers"]["items"] == 2
    assert u["cleanable"]["unbuilt_tile_data"]["items"] == 1
    assert u["largest_tiles"][0]["tile"] == "+06-076"


def test_clean_leftovers_and_unbuilt(sandbox):
    built, unbuilt, dsf = _fake_files(sandbox)
    r = diskusage.clean("leftovers")
    assert r["ok"] and r["deleted"] == 2
    assert not (built.cache_path / "osm_parts").exists()
    assert (built.cache_path / "osm.json").exists() and dsf.exists()
    r = diskusage.clean("unbuilt_tile_data")
    assert r["ok"] and not unbuilt.cache_path.exists() and built.cache_path.exists()
    assert not diskusage.clean("everything")["ok"]


def test_api_delete_tiles(sandbox):
    from forest.app.api import Api
    built, unbuilt, dsf = _fake_files(sandbox)
    api = Api.__new__(Api)          # no X-Plane/catalog needed for deleting
    from forest.app.api import Job
    api.job = Job()
    pv = api.delete_preview(["+06-076"], "colombia")
    assert pv["output"]["tiles"] == 1 and pv["cache"]["tiles"] == 1
    r = api.delete_tiles(["+06-076"], ["output"], "colombia")
    assert r["ok"] and not dsf.exists() and built.cache_path.exists()
    assert not (sandbox.out / "forest_colombia" / "Earth nav data" / "+00-080").exists()  # empty folder removed
    r = api.delete_tiles(["+06-076"], ["cache"], "colombia")
    assert r["ok"] and not built.cache_path.exists()
    assert not api.delete_tiles(["+06-076"], ["installed"], "colombia")["ok"]   # unknown kind
    assert not api.delete_tiles(["nonsense"], ["output"], "colombia")["ok"]


def test_pack_link_unlink_and_absorb(sandbox, monkeypatch):
    """Junction on Windows, symlink on macOS/Linux."""
    from forest import pack
    tile = sandbox.out / "forest_colombia" / "Earth nav data" / "+00-080" / "+06-076.dsf"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"tile")
    # An old real pack folder in X-Plane with one duplicate and one tile only X-Plane has.
    old = sandbox.xplane / "Custom Scenery" / "forest_colombia" / "Earth nav data" / "+00-080"
    old.mkdir(parents=True)
    (old / "+06-076.dsf").write_bytes(b"tile")
    (old / "+07-076.dsf").write_bytes(b"only-in-xplane")
    r = pack.link_pack("colombia")
    assert r["ok"] and r["installed"]
    assert (sandbox.out / "forest_colombia" / "Earth nav data" / "+00-080" / "+07-076.dsf").read_bytes() == b"only-in-xplane"
    link = sandbox.xplane / "Custom Scenery" / "forest_colombia"
    assert (link / "Earth nav data" / "+00-080" / "+06-076.dsf").read_bytes() == b"tile"   # seen through the link
    assert pack.link_pack("colombia")["message"].endswith("already linked into X-Plane")
    r = pack.unlink_pack("colombia")
    assert r["ok"] and not link.exists() and tile.exists()     # only the link is removed


def test_pack_refuses_folder_with_other_files(sandbox):
    from forest import pack
    old = sandbox.xplane / "Custom Scenery" / "forest_colombia"
    old.mkdir(parents=True)
    (old / "readme.txt").write_text("someone else's file")
    with pytest.raises(RuntimeError, match="non-tile"):
        pack.link_pack("colombia")
    assert (old / "readme.txt").exists()
    assert not pack.unlink_pack("colombia")["ok"]   # a real folder is never removed


def test_pack_credits_file(sandbox):
    from forest.credits import CREDITS_FILE, credits_text, write_pack_credits
    p = write_pack_credits(sandbox.out / "forest_colombia", "colombia")
    text = p.read_text(encoding="utf-8-sig")
    assert p.name == CREDITS_FILE and "© OpenStreetMap contributors" in text and "ODbL" in text
    assert "CC BY 4.0" in text and text == credits_text("colombia")
