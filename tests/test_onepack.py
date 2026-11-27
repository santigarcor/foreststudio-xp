"""One map and one X-Plane pack for every region: each tile is built with its owner region's forest
types; the old per-region packs are merged into the one pack and the X-Plane link keeps its name."""

from __future__ import annotations

import json
import os

from forest.tiles import Tile


def _square(w, s, e, n):
    return {"type": "MultiPolygon", "coordinates": [[[[w, s], [e, s], [e, n], [w, n], [w, s]]]]}


def _regions(sandbox, outlines: bool):
    d = sandbox.root / "config" / "regions"
    d.mkdir(parents=True)
    for name, box in (("north", "[0, 0, 2, 2]"), ("south", "[0, -1, 2, 1]")):
        (d / f"{name}.toml").write_text(f'bounds = {box}\nosm_url = "https://x/{name}-latest.osm.pbf"\n',
                                        encoding="utf-8")
    if outlines:
        from forest.fetch import geofabrik
        feats = [{"properties": {"id": "north", "name": "North", "urls": {"pbf": "https://x/north-latest.osm.pbf"}},
                  "geometry": _square(0, 0.7, 2, 2)},      # 30 % of tile +00+000
                 {"properties": {"id": "south", "name": "South", "urls": {"pbf": "https://x/south-latest.osm.pbf"}},
                  "geometry": _square(0, -1, 2, 0.7)}]     # 70 % of tile +00+000
        p = geofabrik.index_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"features": feats}), encoding="utf-8")


def test_each_tile_has_one_owner(sandbox):
    from forest import regions

    _regions(sandbox, outlines=True)
    own = regions.owners()
    assert own["+01+000"] == "north" and own["-01+000"] == "south"   # only one region offers them
    assert own["+00+000"] == "south"                                  # the one covering most of it
    assert "colombia" in own.values()                                 # the shipped region is still there
    assert regions.owner(Tile(50, 50)) is None


def test_owner_without_outlines_is_stable(sandbox):
    from forest import regions

    _regions(sandbox, outlines=False)
    assert regions.owner(Tile(0, 0)) == "north"      # a tie: by name


def test_build_tile_uses_the_owner(sandbox, monkeypatch):
    from forest import build, regions

    _regions(sandbox, outlines=True)
    seen = {}

    def fake_load(cls, profile, region):
        seen["r"] = region
        raise RuntimeError("stop")

    monkeypatch.setattr(build.Settings, "load", classmethod(fake_load))
    monkeypatch.setattr(regions, "covered_tiles", lambda: frozenset({"+00+000"}))   # its OSM file is here
    try:
        build.build_tile(Tile(0, 0), profile="medium")
    except RuntimeError:
        pass
    assert seen["r"] == "south" == regions.owner(Tile(0, 0))


def _old_pack(sandbox, region: str, tiles: dict[str, bytes], mtime: int):
    for name, data in tiles.items():
        t = Tile.parse(name)
        f = sandbox.out / f"forest_{region}" / "Earth nav data" / t.folder / f"{name}.dsf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        os.utime(f, (mtime, mtime))
        s = sandbox.out / f"studio_{region}" / f"{name}.stats.json"
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_text(json.dumps({"region": region}))
        os.utime(s, (mtime, mtime))
    (sandbox.out / f"forest_{region}" / "CREDITS.txt").write_text("old")


def test_old_packs_merge_into_one_and_the_link_keeps_its_name(sandbox):
    from forest import migrate, pack
    from forest.paths import dsf_path, pack_root, studio_path

    _old_pack(sandbox, "colombia", {"+06-076": b"old", "+07-076": b"co"}, 1_000_000_000)
    _old_pack(sandbox, "panama", {"+06-076": b"new", "+08-080": b"pa"}, 2_000_000_000)
    cs = sandbox.xplane / "Custom Scenery"
    cs.mkdir(parents=True, exist_ok=True)
    for r in ("colombia", "panama"):
        pack.make_link(sandbox.out / f"forest_{r}", cs / f"forest_{r}")
    (cs / "scenery_packs.ini").write_text("I\n1000 Version\nSCENERY\n\nSCENERY_PACK Custom Scenery/SKRG/\n"
                                          "SCENERY_PACK Custom Scenery/forest_colombia/\n"
                                          "SCENERY_PACK Custom Scenery/forest_panama/\n", encoding="utf-8")
    ini_before = (cs / "scenery_packs.ini").read_bytes()

    notes = migrate.run_packs()
    assert notes
    assert dsf_path(Tile.parse("+06-076")).read_bytes() == b"new"              # the newer build wins
    assert dsf_path(Tile.parse("+07-076")).read_bytes() == b"co"
    assert dsf_path(Tile.parse("+08-080")).read_bytes() == b"pa"
    assert json.loads(studio_path(Tile.parse("+06-076"), ".stats.json").read_text())["region"] == "panama"
    assert not (sandbox.out / "forest_colombia").exists() and not (sandbox.out / "studio_panama").exists()
    assert (pack_root() / "CREDITS.txt").read_text(encoding="utf-8-sig") != "old"
    # forest_colombia (listed first in the scenery order) now shows the one pack; panama's link is gone
    assert pack.our_links() == [cs / "forest_colombia"] and not (cs / "forest_panama").exists()
    st = pack.pack_status()
    assert st["installed"] and st["pack"] == "forest_colombia" and st["in_ini"]
    assert (cs / "scenery_packs.ini").read_bytes() == ini_before              # never edited
    assert migrate.run_packs() == []                                          # nothing left to do
    assert pack.link_pack()["message"].endswith("already linked into X-Plane")
    assert pack.unlink_pack()["ok"] and not (cs / "forest_colombia").exists()
    assert pack.link_pack()["installed"] and (cs / "forest_studio_xp").exists()   # new installs: the new name
