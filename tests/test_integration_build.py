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
    ]
    write_json(c / "osm.json", {"version": 3, "elements": els})


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
    monkeypatch.setattr(eco, "load_features", lambda: feats)
    cat = {"forests": fake_catalog_entries(rules_assets(real_rules))}
    monkeypatch.setattr(build, "load_catalog", lambda *a, **k: cat)
    txt = build.build_tile(TILE, profile="test", region="colombia")
    return txt


@pytest.mark.integration
def test_build_tile_end_to_end(built_tile, sandbox):
    from forest.paths import studio_path
    txt = built_tile
    assert txt.exists() and txt.parent.name == "studio_colombia"
    stats = json.loads(studio_path("colombia", TILE, ".stats.json").read_text())
    assert stats["polygons"] > 0 and stats["forest_km2"] > 1000
    assert set(stats["biome_km2"]) >= {"rainforest", "mangrove"}
    assert "oil_palm" in stats["plantation_km2"] and "no_trees" in stats["plantation_km2"]
    assert stats["osm_single_trees"] == 1
    assert studio_path("colombia", TILE, ".overlay.png").exists()
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
    monkeypatch.setattr(eco, "load_features", lambda: [])
    assets = rules_assets(real_rules)
    cat = {"forests": fake_catalog_entries(assets, seasonal=assets)}   # pretend all are seasonal
    monkeypatch.setattr(build, "load_catalog", lambda *a, **k: cat)
    with pytest.raises(ValueError, match="seasonal"):
        build.build_tile(TILE, profile="test", region="colombia")


@pytest.mark.integration
@pytest.mark.dsftool
def test_compile_with_real_dsftool(built_tile, sandbox):
    from forest import cli, config
    real = ROOT / "tools" / "xptools" / "windows" / "DSFTool.exe"
    if not real.exists():
        pytest.skip("bundled DSFTool not available on this platform")
    config.save(sandbox.xplane, real)
    from forest.paths import dsf_path
    dsf = dsf_path("colombia", TILE)
    assert cli.compile_dsf(built_tile, dsf) == 0
    assert dsf.exists() and dsf.stat().st_size > 1000
    assert not dsf.with_name(dsf.stem + ".building.dsf").exists()   # temp file swapped in
