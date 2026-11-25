"""The real pipeline on a small synthetic tile: satellite rasters, OSM, ecoregions and elevation
are generated in the sandbox, then build_tile runs end to end (and DSFTool compiles it when
available)."""

import json
import shutil
import tomllib

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from forest.tiles import Tile
from tests.conftest import ROOT, fake_catalog_entries, rules_assets, write_json

TILE = Tile.parse("+06-076")  # synthetic data; nothing real is read


def _write_profile(sandbox, prof: dict, name: str = "test"):
    """Profiles below the app's minimum grid are fine for tests; write the TOML directly."""
    def val(v):
        return "[" + ", ".join(map(str, v)) + "]" if isinstance(v, list) else str(v)
    text = "\n".join(f"{k} = {val(v)}" for k, v in prof.items()) + "\n"
    (sandbox.root / "config" / "profiles" / f"{name}.toml").write_text(text, encoding="utf-8")


def _tif(path, arr, cells):
    with rasterio.open(path, "w", driver="GTiff", width=cells, height=cells, count=1, dtype=arr.dtype,
                       crs="EPSG:4326", transform=from_origin(TILE.lon, TILE.lat + 1, 1 / cells, 1 / cells)) as d:
        d.write(arr, 1)


def _make_tile_data(sandbox):
    c = TILE.cache
    n = 1200                                    # WorldCover at 1/1200° (≈ 90 m) — small but real format
    wc = np.full((n, n), 30, np.uint8)          # grass
    wc[:, : n // 2] = 10                        # west half: trees
    wc[: n // 10, n // 2: n // 2 + 60] = 95     # a mangrove strip in the north
    wc[n // 2:, n // 2:] = 40                   # cropland
    wc[n // 10: n // 2, 700:900] = 20           # shrubland (montane biome)
    wc[n // 10: n // 4, 900:] = 50              # a town in the NE
    _tif(c / "worldcover.tif", wc, n)
    _tif(c / "treecover2000.tif", np.full((600, 600), 80, np.uint8), 600)
    loss = np.zeros((600, 600), np.uint8)
    loss[550:, :50] = 23                        # recent loss in the SW corner
    _tif(c / "lossyear.tif", loss, 600)
    elev = np.full((121, 121), 300, np.int16)   # lowland west
    elev[:, 60:] = 2500                         # highland east
    np.save(c / "elevation_int16.npy", elev)

    def sq(x, y, d):
        return [{"lon": x, "lat": y}, {"lon": x + d, "lat": y}, {"lon": x + d, "lat": y + d},
                {"lon": x, "lat": y + d}, {"lon": x, "lat": y}]
    els = [
        {"type": "way", "id": 1, "tags": {"highway": "primary"},
         "geometry": [{"lon": -75.95, "lat": 6.5}, {"lon": -75.55, "lat": 6.5}]},
        {"type": "way", "id": 2, "tags": {"landuse": "orchard", "trees": "oil_palms"}, "geometry": sq(-75.3, 6.2, 0.05)},
        {"type": "way", "id": 3, "tags": {"landuse": "farmland", "crop": "sugarcane"}, "geometry": sq(-75.2, 6.1, 0.05)},
        {"type": "node", "id": 4, "tags": {"natural": "tree"}, "lat": 6.3, "lon": -75.4},
        {"type": "way", "id": 5, "tags": {"aeroway": "runway"},
         "geometry": [{"lon": -75.8, "lat": 6.1}, {"lon": -75.8, "lat": 6.15}]},
        {"type": "way", "id": 6, "tags": {"building": "yes"}, "geometry": sq(-75.2, 6.8, 0.02)},
        {"type": "way", "id": 7, "tags": {"landuse": "industrial"}, "geometry": sq(-75.1, 6.8, 0.04)},
        {"type": "way", "id": 8, "tags": {"natural": "scrub"}, "geometry": sq(-75.6, 6.92, 0.04)},
        {"type": "way", "id": 9, "tags": {"aeroway": "aerodrome", "icao": "SKXX"}, "geometry": sq(-75.24, 6.76, 0.04)},
        # Town: a car park and a pond (no town trees); forest: a building site (no trees at all).
        {"type": "way", "id": 10, "tags": {"amenity": "parking"}, "geometry": sq(-75.05, 6.85, 0.02)},
        {"type": "way", "id": 11, "tags": {"natural": "water", "water": "basin"}, "geometry": sq(-75.02, 6.80, 0.01)},
        {"type": "way", "id": 12, "tags": {"landuse": "construction"}, "geometry": sq(-75.9, 6.7, 0.05)},
    ]
    from forest.fetch.osm_pbf import VERSION  # the current format: built without a country file
    write_json(c / "osm.json", {"version": VERSION, "elements": els})


@pytest.fixture
def built_tile(sandbox, monkeypatch, real_rules):
    import forest.build as build
    import forest.fetch.ecoregions as eco
    _make_tile_data(sandbox)
    shutil.copy2(ROOT / "config" / "rules" / "colombia.toml", sandbox.root / "config" / "rules" / "colombia.toml")
    prof = tomllib.loads((ROOT / "config" / "profiles" / "medium.toml").read_text(encoding="utf-8"))
    prof.update(grid_cells=600, chunks_per_degree=4, tile_tree_budget=100_000_000)
    _write_profile(sandbox, prof)
    # Ecoregions: west half Amazon (rainforest biome), east half Andean montane.
    feats = [
        {"type": "Feature", "properties": {"eco_id": 1, "eco_name": "Napo moist forests"},
         "geometry": {"type": "Polygon", "coordinates": [[[-76, 6], [-75.5, 6], [-75.5, 7], [-76, 7], [-76, 6]]]}},
        {"type": "Feature", "properties": {"eco_id": 2, "eco_name": "Northwest Andean montane forests"},
         "geometry": {"type": "Polygon", "coordinates": [[[-75.5, 6], [-75, 6], [-75, 7], [-75.5, 7], [-75.5, 6]]]}},
    ]
    monkeypatch.setattr(eco, "load_features", lambda region: feats)
    cat = {"forests": fake_catalog_entries(rules_assets(real_rules))}
    monkeypatch.setattr(build, "load_catalog", lambda *a, **k: cat)
    txt = build.build_tile(TILE, profile="test", region="colombia")
    return txt


@pytest.mark.integration
def test_build_tile_end_to_end(built_tile, sandbox):
    from forest.paths import studio_path
    txt = built_tile
    assert txt.exists() and txt.parent.name == "studio"
    stats = json.loads(studio_path(TILE, ".stats.json").read_text())
    assert stats["polygons"] > 0 and stats["forest_km2"] > 1000
    assert set(stats["biome_km2"]) >= {"rainforest", "mangrove"}
    assert "oil_palm" in stats["plantation_km2"] and "no_trees" in stats["plantation_km2"]
    assert stats["osm_single_trees"] == 1
    assert stats["scrub_km2"] > 50 and stats["town_km2"] > 50
    assert stats["town_trees"] > 1000
    text = txt.read_text()
    assert "lib/g8/shrb_vhot_wet.for" in text                              # montane scrub
    assert studio_path(TILE, ".overlay.png").exists()
    text = txt.read_text()
    assert "PROPERTY sim/exclude_for -76.0000000/6.0000000/-75.0000000/7.0000000" in text
    assert "lib/vegetation/trees/palm/date_palm_small.for" in text          # oil palm plantation
    defs = [ln.split(" ", 1)[1] for ln in text.splitlines() if ln.startswith("POLYGON_DEF")]
    cat_assets = set(defs)
    assert cat_assets, "no forest types written"
    # no polygon exceeds DSF's 255-winding limit
    w = mx = 0
    for ln in text.splitlines():
        if ln.startswith("BEGIN_POLYGON"):
            w = 0
        elif ln.startswith("BEGIN_WINDING"):
            w += 1
        elif ln.startswith("END_POLYGON"):
            mx = max(mx, w)
    assert mx <= 255


@pytest.mark.integration
def test_build_refuses_seasonal_assets(sandbox, monkeypatch, real_rules):
    import forest.build as build
    import forest.fetch.ecoregions as eco
    _make_tile_data(sandbox)
    shutil.copy2(ROOT / "config" / "rules" / "colombia.toml", sandbox.root / "config" / "rules" / "colombia.toml")
    prof = tomllib.loads((ROOT / "config" / "profiles" / "medium.toml").read_text(encoding="utf-8"))
    prof.update(grid_cells=300, chunks_per_degree=2)
    _write_profile(sandbox, prof)
    monkeypatch.setattr(eco, "load_features", lambda region: [])
    assets = rules_assets(real_rules)
    cat = {"forests": fake_catalog_entries(assets, seasonal=assets)}   # pretend all are seasonal
    monkeypatch.setattr(build, "load_catalog", lambda *a, **k: cat)
    with pytest.raises(ValueError, match="seasonal"):
        build.build_tile(TILE, profile="test", region="colombia")


@pytest.mark.integration
@pytest.mark.dsftool
def test_compile_with_real_dsftool(built_tile, sandbox):
    from forest import cli, config
    real = ROOT / "tools" / "xptools" / config.PLATFORM_DIR / config.DSFTOOL_EXE   # this system's bundled one
    if not real.exists():
        pytest.skip("bundled DSFTool not available on this platform")
    config.save(sandbox.xplane, real)
    from forest.paths import dsf_path
    dsf = dsf_path(TILE)
    assert cli.compile_dsf(built_tile, dsf) == 0
    assert dsf.exists() and dsf.stat().st_size > 1000
    assert not dsf.with_name(dsf.stem + ".building.dsf").exists()   # temp file swapped in


@pytest.mark.integration
def test_outdated_detection(built_tile, sandbox):
    import os

    from forest.build import outdated_reasons
    from forest.paths import studio_path
    stats = json.loads(studio_path(TILE, ".stats.json").read_text())
    assert stats["fingerprint"] and "osm.json" in stats["data"]
    assert outdated_reasons(stats, TILE, "test", "colombia") == []               # just built
    assert "built with profile 'test'" in outdated_reasons(stats, TILE, "medium", "colombia")[0]
    prof = sandbox.root / "config" / "profiles" / "test.toml"
    prof.write_text(prof.read_text().replace("global_density = ", "global_density = 0.5 #"))
    assert outdated_reasons(stats, TILE, "test", "colombia") == ["profile or forest types changed"]
    osm = TILE.cache_path / "osm.json"
    t = stats["data"]["osm.json"] + 3600
    os.utime(osm, (t, t))                                                          # re-extracted later
    assert "newer data: osm.json" in outdated_reasons(stats, TILE, "test", "colombia")
    old = {k: v for k, v in stats.items() if k != "fingerprint"}
    assert outdated_reasons(old, TILE, "test", "colombia") == ["built before ForestStudio tracked settings"]


@pytest.mark.integration
def test_town_trees_avoid_buildings_and_industry(built_tile):
    """Town trees are points (fill 512); none inside the big building or the industrial area."""
    from shapely.geometry import Point, box
    building, industrial = box(-75.2, 6.8, -75.18, 6.82), box(-75.1, 6.8, -75.06, 6.84)
    airport = box(-75.24, 6.76, -75.20, 6.80)
    pts, cur = [], None
    for ln in built_tile.read_text().splitlines():
        if ln.startswith("BEGIN_POLYGON"):
            cur = int(ln.split()[2]) >= 512
        elif cur and ln.startswith("POLYGON_POINT"):
            x, y = map(float, ln.split()[1:3])
            pts.append(Point(x, y))
    town = [q for q in pts if q.y > 6.74 and q.x > -75.26]
    assert len(town) > 1000
    assert not any(building.contains(q) for q in town)
    assert not any(industrial.contains(q) for q in town)
    assert not any(airport.contains(q) for q in town)          # no town trees on airport grounds
    parking, pond = box(-75.05, 6.85, -75.03, 6.87), box(-75.02, 6.80, -75.01, 6.81)
    assert not any(parking.contains(q) or pond.contains(q) for q in town)


@pytest.mark.integration
def test_no_forest_on_construction_sites(built_tile):
    """The satellite sees forest there, but OSM says the land is being built on now."""
    from shapely.geometry import Point
    site = Point(-75.875, 6.725)   # centre of the 0.05° building site in the western forest
    cur, wind, inside = None, [], []
    for ln in built_tile.read_text().splitlines():
        if ln.startswith("BEGIN_POLYGON"):
            cur, wind = int(ln.split()[2]) < 256, []
        elif ln.startswith("POLYGON_POINT") and cur:
            wind.append(tuple(map(float, ln.split()[1:3])))
        elif ln.startswith("END_WINDING") and cur and len(wind) >= 3:
            from shapely.geometry import Polygon
            inside.append(Polygon(wind).contains(site))
            wind = []
    assert inside and not any(inside)
    from forest.paths import studio_path
    stats = json.loads(studio_path(TILE, ".stats.json").read_text())
    assert stats["exclusion_cells"]["bare"] > 0


def test_old_rules_get_scrub_and_town_defaults(real_rules):
    """Rule files saved before scrub/town trees existed take the shipped defaults per biome."""
    from forest.build import fill_rule_defaults
    old = {**real_rules, "biome": [{k: v for k, v in b.items() if k not in ("scrub", "scrub_on_bare", "town_tree")}
                                   for b in real_rules["biome"]]}
    old.pop("town_tree")
    filled = fill_rule_defaults(old, "colombia")
    by = {b["name"]: b for b in filled["biome"]}
    assert by["xeric"]["scrub"] == "lib/g8/shrb_vhot_dry.for" and by["xeric"]["scrub_on_bare"]
    assert by["paramo"]["scrub"] == ""
    assert filled["town_tree"] == real_rules["town_tree"]
    mine = {**real_rules, "biome": [dict(b, scrub="") for b in real_rules["biome"]]}
    assert all(b["scrub"] == "" for b in fill_rule_defaults(mine, "colombia")["biome"])   # explicit "" kept
