"""Forest type by satellite leaf type and Köppen climate (forest/fetch/climate.py) and a real build
whose biomes have `climate = true`."""

import dataclasses
import shutil
import tomllib

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from forest.tiles import Grid, Tile
from tests.conftest import ROOT, fake_catalog_entries, rules_assets

TILE = Tile.parse("+06-076")


def _tif(path, arr):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = arr.shape[0]
    with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=1, dtype=arr.dtype, crs="EPSG:4326",
                       transform=from_origin(TILE.lon, TILE.lat + 1, 1 / n, 1 / n)) as d:
        d.write(arr, 1)


def _leaf(west=1, east=4):
    lt = np.full((200, 200), 255, np.uint8)
    lt[:, :100], lt[:, 100:] = west, east
    _tif(TILE.cache / "leaftype.tif", lt)


def _koppen(sandbox, value=14):
    from forest.fetch import climate
    kg = np.full((120, 120), value, np.uint8)
    kg[:, 100:] = 0                       # sea: takes the nearest climate
    _tif(climate.climate_path(), kg)


def test_koppen_table_and_asset_names():
    from forest.fetch import climate as c
    assert sorted(c.KOPPEN) == list(range(1, 31))
    assert all(t in c.TEMPS and m in c.MOISTS for t, m in c.KOPPEN.values())
    assert c.KOPPEN[14] == ("wrm", "wet")           # Cfa: Florida, south-east US
    assert c.asset_name(c.CONI, "wrm", "wet", False) == "lib/g8/coni_wrm_wet.for"
    assert c.asset_name(c.MIXED, "tmp", "sdry", True) == "lib/g8/mixed_sp_tmp_sdry.for"
    assert c.asset_name(c.BROAD, "vhot", "rain", False) == "lib/g8/rainforest_vhot_rain.for"
    assert c.asset_name(c.MONTANE, "tmp", "wet", False) == "lib/g8/rainforestm_vhot_rain.for"
    assert c.asset_name(c.SWAMP, "wrm", "wet", True) == "lib/g8/wetl_wdy_wrm_wet.for"
    assert c.asset_name(c.BROAD, "hot", "sdry", True) == "lib/g8/sparse_tr_hot_sdry.for"     # savanna
    assert c.asset_name(c.CONI, "hot", "sdry", True) == "lib/g8/coni_sp_hot_sdry.for"
    assert c.shrub_name("wrm", "wet", wet=True) == "lib/g8/wetl_shrb_wrm_wet.for"
    assert c.orchard_name("lib/g8/orch_tmp_wet.for", "wrm", "sdry") == "lib/g8/orch_wrm_dry.for"
    assert c.orchard_name("lib/g8/orch_tmp_wet.for", "vcld", "wet") == "lib/g8/orch_tmp_wet.for"
    assert c.orchard_name("lib/vegetation/trees/palm/date_palm_small.for", "wrm", "wet") is None


def test_every_climate_type_exists_in_xplane_names():
    """Every name climate picking can produce follows X-Plane's lib/g8 grid (checked against the real
    library by hand: 246 names, none missing)."""
    from forest.fetch import climate as c
    names = c.all_assets()
    assert len(names) == 246
    assert all(n.startswith("lib/g8/") and n.endswith(".for") for n in names)
    assert not any("_cld_" in n or "_vcld_" in n for n in names if "/orch_" in n or "/fruit_" in n)
    assert "lib/g8/orch_wrm_sdry.for" not in names


def test_osm_hints_sort_wetlands_and_leaf_types():
    from forest.exclusions import osm_tree_hints

    def sq(x, y, d=0.01):
        return [{"lon": x, "lat": y}, {"lon": x + d, "lat": y}, {"lon": x + d, "lat": y + d},
                {"lon": x, "lat": y + d}, {"lon": x, "lat": y}]
    els = [
        {"type": "way", "id": 1, "tags": {"natural": "wetland", "wetland": "swamp"}, "geometry": sq(0, 0)},
        {"type": "way", "id": 2, "tags": {"natural": "wetland", "wetland": "marsh"}, "geometry": sq(1, 0)},
        {"type": "way", "id": 3, "tags": {"natural": "wetland"}, "geometry": sq(2, 0)},
        {"type": "way", "id": 4, "tags": {"natural": "wetland", "wetland": "tidalflat"}, "geometry": sq(3, 0)},
        {"type": "way", "id": 5, "tags": {"natural": "wetland", "wetland": "mangrove"}, "geometry": sq(4, 0)},
        {"type": "way", "id": 6, "tags": {"natural": "wood", "leaf_type": "broadleaved"}, "geometry": sq(5, 0)},
    ]
    h = osm_tree_hints({"elements": els})
    assert len(h["swamp"]) == 1 and len(h["wetland"]) == 2 and len(h["mangrove"]) == 1
    assert [lt for _, lt in h["leaf"]] == ["broadleaved"] and len(h["green"]) == 1


def test_leaf_type_on_grid(sandbox):
    from forest.fetch import climate as c
    grid = Grid(TILE, 300)
    assert (c.leaf_on_grid(TILE, grid) == c.MIXED).all()          # not downloaded: mixed
    _leaf(west=1, east=4)
    fam = c.leaf_on_grid(TILE, grid)
    assert fam[150, 20] == c.CONI and fam[150, 280] == c.BROAD
    (TILE.cache / "leaftype.tif").unlink()
    _leaf(west=0, east=5)                                          # unknown and mixed → mixed
    assert (c.leaf_on_grid(TILE, grid) == c.MIXED).all()


def test_climate_on_grid_fills_the_sea(sandbox):
    from forest.fetch import climate as c
    grid = Grid(TILE, 300)
    assert c.climate_on_grid(TILE, grid) is None                   # no climate map yet
    _koppen(sandbox, 14)
    temp, moist = c.climate_on_grid(TILE, grid)
    assert (temp == c.TEMPS.index("wrm")).all() and (moist == c.MOISTS.index("wet")).all()


def test_template_regions_get_climate_colombia_does_not(sandbox):
    from forest.build import fill_rule_defaults
    shutil.copy2(ROOT / "config" / "rules" / "_global.toml", sandbox.repo / "config" / "rules" / "_global.toml")
    shutil.copy2(ROOT / "config" / "rules" / "colombia.toml", sandbox.repo / "config" / "rules" / "colombia.toml")
    tpl = tomllib.loads((ROOT / "config" / "rules" / "_global.toml").read_text(encoding="utf-8"))
    old = {**tpl, "biome": [{k: v for k, v in b.items() if k != "climate"} for b in tpl["biome"]]}
    filled = {b["name"]: b for b in fill_rule_defaults(old, "somewhere")["biome"]}
    assert filled["temperate_grassland"]["climate"] is True
    assert "climate" not in filled["mangrove"]
    col = tomllib.loads((ROOT / "config" / "rules" / "colombia.toml").read_text(encoding="utf-8"))
    assert not any("climate" in b for b in fill_rule_defaults(col, "colombia")["biome"])


def test_rules_round_trip_keeps_climate(sandbox, real_rules):
    from forest.app.settings import validate_rules
    rules = dict(real_rules)
    rules["biome"] = [dict(b, climate=(b["name"] == "default")) for b in real_rules["biome"]]
    allowed = rules_assets(real_rules)
    d = validate_rules(rules, allowed)
    assert [b["climate"] for b in d["biome"]].count(True) == 1
    assert all("climate" not in b for b in validate_rules(real_rules, allowed)["biome"])


def _build(sandbox, monkeypatch, real_rules, evergreen: bool, koppen: int = 14, extra_osm=()):
    import forest.build as build
    import forest.fetch.ecoregions as eco
    from forest import regions
    from forest.fetch import climate as c
    from tests.test_integration_build import _make_tile_data, _write_profile
    _make_tile_data(sandbox)
    if extra_osm:
        import json
        osm = json.loads((TILE.cache / "osm.json").read_text(encoding="utf-8"))
        osm["elements"] += list(extra_osm)
        (TILE.cache / "osm.json").write_text(json.dumps(osm), encoding="utf-8")
    _leaf(west=1, east=4)
    _koppen(sandbox, koppen)
    rules = (ROOT / "config" / "rules" / "colombia.toml").read_text(encoding="utf-8")
    rules = rules.replace("[[biome]]\n", "[[biome]]\nclimate = true\n")
    (sandbox.root / "config" / "rules" / "colombia.toml").write_text(rules, encoding="utf-8")
    prof = tomllib.loads((ROOT / "config" / "profiles" / "medium.toml").read_text(encoding="utf-8"))
    prof.update(grid_cells=300, chunks_per_degree=2, tile_tree_budget=100_000_000)
    _write_profile(sandbox, prof)
    monkeypatch.setattr(eco, "load_features", lambda region: [])
    real_get = regions.get
    monkeypatch.setattr(regions, "get", lambda n: dataclasses.replace(real_get(n), evergreen_only=evergreen))
    assets = rules_assets(real_rules) | c.all_assets()
    seasonal = () if evergreen else {a for a in c.all_assets() if "_vhot_" not in a}
    cat = {"forests": fake_catalog_entries(assets, seasonal=seasonal)}
    monkeypatch.setattr(build, "load_catalog", lambda *a, **k: cat)
    txt = build.build_tile(TILE, profile="test", region="colombia")
    return {ln.split(None, 1)[1].strip() for ln in txt.read_text(encoding="utf-8").splitlines()
            if ln.startswith("POLYGON_DEF")}


@pytest.mark.integration
def test_build_picks_trees_by_leaf_type_and_climate(sandbox, monkeypatch, real_rules):
    defs = _build(sandbox, monkeypatch, real_rules, evergreen=False)
    assert "lib/g8/coni_wrm_wet.for" in defs           # west: conifers in a Cfa climate
    assert not any(d.startswith("lib/g8/rainforest") for d in defs)


@pytest.mark.integration
def test_evergreen_regions_get_the_very_hot_family(sandbox, monkeypatch, real_rules):
    defs = _build(sandbox, monkeypatch, real_rules, evergreen=True)
    assert "lib/g8/coni_vhot_wet.for" in defs
    assert not any(("_wrm_" in d or "_tmp_" in d) for d in defs if d.startswith("lib/g8/coni"))


def _sq(x, y, d):
    return [{"lon": x, "lat": y}, {"lon": x + d, "lat": y}, {"lon": x + d, "lat": y + d},
            {"lon": x, "lat": y + d}, {"lon": x, "lat": y}]


@pytest.mark.integration
def test_swamps_wetlands_cloud_forest_and_osm_leaf_type(sandbox, monkeypatch, real_rules):
    """Cool, wet tropics (Köppen Cfb at +06): an OSM broadleaf wood becomes cloud forest, an OSM
    swamp in the forest becomes swamp forest, an OSM marsh on grass gets wetland bushes; the
    satellite's conifers stay conifers; single trees take the climate's broadleaf type."""
    els = [
        {"type": "way", "id": 101, "tags": {"natural": "wood", "leaf_type": "broadleaved"}, "geometry": _sq(-75.85, 6.25, 0.1)},
        {"type": "way", "id": 102, "tags": {"natural": "wetland", "wetland": "swamp"}, "geometry": _sq(-75.9, 6.6, 0.1)},
        {"type": "way", "id": 103, "tags": {"natural": "wetland", "wetland": "marsh"}, "geometry": _sq(-75.45, 6.6, 0.05)},
    ]
    defs = _build(sandbox, monkeypatch, real_rules, evergreen=False, koppen=15, extra_osm=els)
    assert "lib/g8/rainforestm_vhot_rain.for" in defs
    assert "lib/g8/wetl_wdy_tmp_wet.for" in defs
    assert "lib/g8/wetl_shrb_tmp_wet.for" in defs
    assert "lib/g8/coni_tmp_wet.for" in defs
    assert "lib/g8/broad_tmp_wet.for" in defs           # the OSM single tree
