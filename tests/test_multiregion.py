"""Other regions: global default rules by RESOLVE biome, regions from Geofabrik's list, and
border tiles filled from every OSM extract that covers them."""

from __future__ import annotations

import json
import tomllib

import numpy as np
import pytest

from forest.tiles import Grid, Tile
from tests.conftest import ROOT


@pytest.fixture
def global_rules(sandbox):
    import shutil
    shutil.copy2(ROOT / "config" / "rules" / "_global.toml", sandbox.repo / "config" / "rules" / "_global.toml")
    return sandbox


def _region(**kw):
    from forest import regions
    return regions.Region("testland", "Testland", (0, 0, 1, 1), "https://x/testland-latest.osm.pbf", **kw)


def _assets(text: str) -> set[str]:
    import re
    return set(re.findall(r'"(lib/[^"]+\.for)"', text))


def test_template_swaps_seasonal_types_in_evergreen_regions(global_rules):
    from forest import regions

    raw = (global_rules.repo / "config" / "rules" / "_global.toml").read_text(encoding="utf-8")
    swap = tomllib.loads(raw)["evergreen_swap"]
    ever = regions.template_rules_text(_region(evergreen_only=True))
    seas = regions.template_rules_text(_region(evergreen_only=False))
    for text in (ever, seas):
        d = tomllib.loads(text)
        assert "evergreen_swap" not in d and "Testland" in text and "{label}" not in text
        assert d["biome"][-1]["name"] == "default"
    assert not _assets(ever) & set(swap)                       # no seasonal type left
    assert "lib/vegetation/forests/broadleaves/temperate.for" in _assets(seas)
    # every RESOLVE biome is mapped once
    names = [rb for b in tomllib.loads(seas)["biome"] for rb in b.get("resolve_biomes", [])]
    assert len(names) == len(set(names)) >= 14


def test_region_without_rules_gets_the_template(global_rules):
    from forest import config

    d = global_rules.root / "config" / "regions"
    d.mkdir(parents=True)
    (d / "testland.toml").write_text('bounds = [0, 0, 1, 1]\nosm_url = "https://x/t.osm.pbf"\n'
                                     'evergreen_only = false\n', encoding="utf-8")
    p = config.rules_path("testland")
    assert p.exists() and "temperate.for" in p.read_text(encoding="utf-8")
    assert not (global_rules.root / "config" / "rules" / "_global.toml").exists()   # templates aren't copied


def test_biome_raster_name_then_resolve_biome_then_default(monkeypatch):
    import forest.fetch.ecoregions as eco
    from forest.build import biome_raster, rule_biomes

    g = Grid(Tile(6, -76), 9)
    ids = np.zeros(g.shape, dtype=np.int32)
    ids[:, :3], ids[:, 3:6], ids[:, 6:] = 1, 2, 3
    monkeypatch.setattr(eco, "ecoregion_raster", lambda t, grid, region: (
        ids, {1: "Napo moist forests", 2: "Other moist forests", 3: "Some tundra"}))
    monkeypatch.setattr(eco, "ecoregion_biome_names", lambda region: {
        "Napo moist forests": "Moist", "Other moist forests": "Moist", "Some tundra": "Tundra"})
    biomes = rule_biomes({"biome": [
        {"name": "napo", "ecoregions": ["Napo moist forests"], "band": []},
        {"name": "moist", "ecoregions": [], "resolve_biomes": ["Moist"], "band": []},
        {"name": "default", "ecoregions": [], "band": []}]})
    r = biome_raster(Tile(6, -76), g, biomes, "x")
    assert (r[:, :3] == 0).all() and (r[:, 3:6] == 1).all() and (r[:, 6:] == 2).all()


def test_resolve_biomes_saved_only_when_used(sandbox):
    from forest.app import settings as st

    a = "lib/a.for"
    band = [{"name": "all", "elev_max": 9000, "dense": a, "sparse": a}]
    data = {"sparse_below": 0.4, "single_tree": a, "biome": [
        {"name": "moist", "ecoregions": [], "resolve_biomes": ["Moist"], "band": band},
        {"name": "default", "ecoregions": [], "resolve_biomes": [], "band": band}]}
    v = st.validate_rules(data, {a})
    assert v["biome"][0]["resolve_biomes"] == ["Moist"] and "resolve_biomes" not in v["biome"][1]
    p = st.write_rules("colombia", data, {a})
    back = tomllib.loads(p.read_text(encoding="utf-8"))
    assert back["biome"][0]["resolve_biomes"] == ["Moist"] and "resolve_biomes" not in back["biome"][1]
    with pytest.raises(ValueError, match="not an allowed"):
        st.validate_rules({**data, "single_tree": "lib/b.for"}, {a})


# ---------- Geofabrik ----------

def _square(w, s, e, n):
    return {"type": "MultiPolygon", "coordinates": [[[[w, s], [e, s], [e, n], [w, n], [w, s]]]]}


@pytest.fixture
def index(sandbox):
    feats = [
        {"properties": {"id": "europe", "name": "Europe", "urls": {"pbf": "https://d/europe-latest.osm.pbf"}},
         "geometry": _square(-10, 35, 30, 70)},
        {"properties": {"id": "luxembourg", "name": "Luxembourg", "parent": "europe",
                        "urls": {"pbf": "https://d/europe/luxembourg-latest.osm.pbf"}},
         "geometry": _square(5.7, 49.4, 6.6, 50.2)},
        {"properties": {"id": "testland", "name": "Testland", "parent": "europe",
                        "urls": {"pbf": "https://d/europe/testland-latest.osm.pbf"}},
         "geometry": _square(0.2, 0.2, 1.5, 0.8)},
        {"properties": {"id": "nourl", "name": "No URL", "urls": {}}, "geometry": _square(0, 0, 1, 1)},
    ]
    from forest.fetch import geofabrik
    p = geofabrik.index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"features": feats}), encoding="utf-8")
    return sandbox


def test_index_tree_and_tiles(index):
    from forest.fetch import geofabrik

    tr = geofabrik.tree()
    assert [(e["id"], e["depth"], e["continent"]) for e in tr] == [
        ("europe", 0, "europe"), ("luxembourg", 1, "europe"), ("testland", 1, "europe")]
    assert tr[0]["children"] == 2
    assert geofabrik.tiles_of("luxembourg") == {"+49+005", "+49+006", "+50+005", "+50+006"}
    assert geofabrik.by_file_name()["luxembourg-latest.osm.pbf"]["id"] == "luxembourg"


def test_add_and_remove_region(index):
    from forest import regions
    from forest.fetch import sources

    r = regions.add_from_geofabrik("luxembourg")
    assert r.name == "luxembourg" and r.bounds == (5, 49, 7, 51) and not r.evergreen_only
    assert r.contains(Tile(49, 6)) and len(r.tiles()) == 4 and r.info()["user"]
    assert regions.add_from_geofabrik("testland").evergreen_only            # between the tropics
    sources._meta_path(r.osm_file).parent.mkdir(parents=True, exist_ok=True)
    r.osm_file.write_bytes(b"x")
    sources._meta_path(r.osm_file).write_text("{}")
    gone = regions.remove("luxembourg")
    assert "luxembourg.toml" in gone and not r.osm_file.exists() and "luxembourg" not in regions.names()
    with pytest.raises(ValueError, match="shipped"):
        regions.remove("colombia")


# ---------- border tiles: several OSM files ----------

def _osm(nodes_ways: str) -> str:
    return f"<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator=\"test\">\n{nodes_ways}</osm>\n"


NODES = """  <node id="2" lat="0.5" lon="0.2" version="1"/>
  <node id="3" lat="0.5" lon="0.8" version="1"/>
"""
A = _osm(NODES + """  <node id="1" lat="0.5" lon="0.5" version="1"><tag k="natural" v="tree"/></node>
  <way id="100" version="1"><nd ref="2"/><nd ref="3"/><tag k="highway" v="primary"/></way>
""")
B = _osm(NODES + """  <node id="5" lat="0.6" lon="0.6" version="1"><tag k="natural" v="tree"/></node>
  <way id="100" version="1"><nd ref="2"/><nd ref="3"/><tag k="highway" v="primary"/></way>
""")


def test_border_tile_reads_both_files_once(sandbox):
    from forest.fetch import osm_pbf

    a, b = sandbox.tmp / "a.osm", sandbox.tmp / "b.osm"
    a.write_text(A)
    b.write_text(B)
    t = Tile(0, 0)
    osm_pbf.extract_tiles([t], a)
    head = (t.cache_path / "osm.json").read_text()[:60]
    assert '"sources"' not in head                                  # one file: the old header
    assert osm_pbf.is_current(t, [a]) and not osm_pbf.is_current(t, [a, b])
    counts = osm_pbf.extract_tiles([t], [b, a])
    d = json.loads((t.cache_path / "osm.json").read_text())
    ids = [(e["type"], e["id"]) for e in d["elements"]]
    assert d["sources"] == ["a.osm", "b.osm"] and counts == {"+00+000": 3}
    assert sorted(ids) == [("node", 1), ("node", 5), ("way", 100)]  # the shared road only once
    assert osm_pbf.is_current(t, [a, b]) and not osm_pbf.is_current(t, [a])
    assert osm_pbf.extract_tiles([t], [a, b]) == {}


def test_osm_sources_uses_downloaded_files_covering_the_tile(sandbox):
    from forest import regions

    d = sandbox.root / "config" / "regions"
    d.mkdir(parents=True)
    for name, box in (("north", "[0, 1, 2, 2]"), ("south", "[0, 0, 2, 2]")):
        (d / f"{name}.toml").write_text(f'bounds = {box}\nosm_url = "https://x/{name}.osm.pbf"\n', encoding="utf-8")
    t = Tile(1, 0)
    assert regions.osm_sources(t) == []                             # nothing downloaded
    for name in ("north", "south"):
        regions.get(name).osm_file.parent.mkdir(parents=True, exist_ok=True)
        regions.get(name).osm_file.write_bytes(b"")
    assert [p.name for p in regions.osm_sources(t)] == ["north.osm.pbf", "south.osm.pbf"]
    assert [p.name for p in regions.osm_sources(Tile(0, 0))] == ["south.osm.pbf"]
    # north's file doesn't cover +00+000, even when north's forest types build it
    assert [p.name for p in regions.osm_sources(Tile(0, 0), "north")] == ["south.osm.pbf"]


def test_tile_no_extract_covers_is_refused(sandbox, monkeypatch):
    """Colombia's box reaches into Panama (MPTO, +09-080), outside Colombia's extract: reading
    Colombia's file gave an empty osm.json and a tile with trees on the runways."""
    from forest import build, regions
    from forest.fetch import osm as osm_mod
    from forest.fetch.osm import OsmFileMissing

    d = sandbox.root / "config" / "regions"
    d.mkdir(parents=True)
    (d / "south.toml").write_text('bounds = [0, 0, 2, 2]\nosm_url = "https://x/south.osm.pbf"\n', encoding="utf-8")
    regions.get("south").osm_file.parent.mkdir(parents=True, exist_ok=True)
    regions.get("south").osm_file.write_bytes(b"")
    t = Tile(1, 1)
    monkeypatch.setattr(regions, "_covers", lambda r, tile: tile.name != t.name)   # outline misses t
    monkeypatch.setattr(regions, "covered_tiles", lambda: frozenset({"+00+000", "+00+001", "+01+000"}))
    assert regions.osm_files(t) == [] and not regions.osm_covered(t)
    t.cache.joinpath("osm.json").write_text('{"version": 8, "elements": [\n\n]}', encoding="utf-8")
    assert not osm_mod.usable_without_country_file(t)                 # the old empty file doesn't count
    with pytest.raises(OsmFileMissing, match="no region's OpenStreetMap file covers"):
        osm_mod.fetch_osm(t)
    with pytest.raises(OsmFileMissing):
        build.build_tile(t, region="south")
    stats = {"fingerprint": "x", "profile": "medium", "region": "south", "data": {}}
    reasons = build.outdated_reasons(stats, t, "medium", "south", fingerprint="x")
    assert any("no OpenStreetMap file covers" in r for r in reasons)
