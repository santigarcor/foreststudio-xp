"""Pure build helpers: DSF polygon splitting, plantation tag matching, biomes."""

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from forest.build import (
    MAX_DSF_HOLES,
    _split_for_dsf,
    _tag_match,
    biome_raster,
    plantation_raster,
    plantation_rule,
    rule_biomes,
)
from forest.tiles import Grid, Tile


# ---------------- DSF polygon limit (255 windings) ----------------
def _swiss_cheese(n_holes: int) -> Polygon:
    holes = []
    side = int(np.ceil(np.sqrt(n_holes)))
    for i in range(n_holes):
        x, y = (i % side) / side, (i // side) / side
        holes.append(box(x + 0.2 / side, y + 0.2 / side, x + 0.6 / side, y + 0.6 / side).exterior.coords)
    return Polygon(box(0, 0, 1, 1).exterior.coords, holes)


def test_small_polygon_is_not_split():
    p = _swiss_cheese(10)
    assert _split_for_dsf(p) == [p]


@pytest.mark.parametrize("n", [251, 336, 1000])
def test_polygon_with_too_many_holes_is_split_without_losing_area(n):
    p = _swiss_cheese(n)
    pieces = _split_for_dsf(p)
    assert len(pieces) > 1
    assert all(len(q.interiors) <= MAX_DSF_HOLES for q in pieces)
    assert sum(q.area for q in pieces) == pytest.approx(p.area, rel=1e-9)  # holes kept, nothing added
    assert all(q.is_valid for q in pieces)


# ---------------- plantations (OSM tags) ----------------
PLANTS = [
    {"name": "oil_palm", "match": ["trees=oil_palms", "genus=Elaeis"], "asset": "palm.for", "density": 0.8},
    {"name": "conifer", "match": ["landuse=forest&leaf_type=needleleaved"], "asset": "pine.for"},
    {"name": "no_trees", "match": ["crop=sugarcane"], "asset": ""},
    {"name": "orchard", "match": ["landuse=orchard"], "asset": "kapok.for"},
]


@pytest.mark.parametrize("tags,expected", [
    ({"landuse": "orchard", "trees": "oil_palms"}, "oil_palm"),   # first matching rule wins
    ({"landuse": "orchard", "genus": "elaeis"}, "oil_palm"),      # values are case-insensitive
    ({"landuse": "orchard"}, "orchard"),
    ({"landuse": "forest", "leaf_type": "needleleaved"}, "conifer"),
    ({"landuse": "forest"}, None),                                # & needs every condition
    ({"landuse": "farmland", "crop": "sugarcane"}, "no_trees"),
    ({"natural": "wood"}, None),
])
def test_plantation_rule_matching(tags, expected):
    i = plantation_rule(tags, PLANTS)
    assert (PLANTS[i]["name"] if i >= 0 else None) == expected


def test_tag_match_trims_and_ignores_case():
    assert _tag_match({"crop": "Café"}, " crop = café ")
    assert not _tag_match({"crop": "cafe"}, "crop=café")


def test_plantation_raster_earlier_rules_win_on_overlap():
    g = Grid(Tile(6, -76), 100)
    big = box(-76, 6, -75, 7)              # orchard over the whole tile
    small = box(-75.6, 6.4, -75.4, 6.6)    # oil palm inside it
    items = [(big, {"landuse": "orchard"}), (small, {"trees": "oil_palms"})]
    r = plantation_raster(items, PLANTS, g, 0.5)
    assert r[50, 50] == 0                  # oil_palm (rule 0) on top
    assert r[5, 5] == 3                    # orchard elsewhere
    assert plantation_raster([], PLANTS, g, 0.5).max() == -1


# ---------------- biomes ----------------
def test_rule_biomes_default_last_and_legacy_rules():
    rules = {"biome": [{"name": "default", "band": []}, {"name": "rain", "band": []}]}
    assert [b["name"] for b in rule_biomes(rules)] == ["rain", "default"]
    legacy = {"band": [{"name": "x", "elev_max": 9000, "dense": "a", "sparse": "b"}]}
    bs = rule_biomes(legacy)
    assert len(bs) == 1 and bs[0]["name"] == "default" and bs[0]["band"] == legacy["band"]


def test_biome_raster_maps_ecoregions_and_falls_back_to_default(monkeypatch):
    import forest.fetch.ecoregions as eco
    g = Grid(Tile(6, -76), 10)
    ecoids = np.zeros(g.shape, dtype=np.int32)
    ecoids[:, :5] = 7       # listed ecoregion
    ecoids[:, 5:] = 9       # unlisted ecoregion → default
    monkeypatch.setattr(eco, "ecoregion_raster", lambda t, grid: (ecoids, {7: "Napo moist forests", 9: "Somewhere"}))
    biomes = rule_biomes({"biome": [{"name": "rainforest", "ecoregions": ["Napo moist forests"], "band": []},
                                    {"name": "default", "ecoregions": [], "band": []}]})
    r = biome_raster(Tile(6, -76), g, biomes)
    assert (r[:, :5] == 0).all() and (r[:, 5:] == 1).all()


def test_real_rules_only_use_evergreen_and_have_default(real_rules):
    """The shipped Colombia rules: every biome has bands with increasing elevation, a default
    biome exists, mangroves come from WorldCover (no ecoregions)."""
    names = [b["name"] for b in real_rules["biome"]]
    assert "default" in names and "mangrove" in names
    assert next(b for b in real_rules["biome"] if b["name"] == "mangrove")["ecoregions"] == []
    for bm in real_rules["biome"]:
        elev = [b["elev_max"] for b in bm["band"]]
        assert elev == sorted(elev) and len(set(elev)) == len(elev)
    listed = [e for b in real_rules["biome"] for e in b["ecoregions"]]
    assert len(listed) == len(set(listed)), "an ecoregion is listed in two biomes"


def test_worldcover_shares_fast_path_matches_reprojection(tmp_path):
    # At 2 source pixels per grid cell the block count must equal GDAL's average resampling.
    import rasterio
    from rasterio.transform import from_origin

    import forest.build as b

    tile = Tile(6, -76)
    rng = np.random.default_rng(3)
    data = rng.choice(np.array([10, 20, 30, 50, 60, 95], dtype=np.uint8), size=(400, 400))
    path = tmp_path / "wc.tif"
    with rasterio.open(path, "w", driver="GTiff", width=400, height=400, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(-76, 7, 1 / 400, 1 / 400)) as dst:
        dst.write(data, 1)
    grid = Grid(tile, 200)
    with rasterio.open(path) as src:
        cls = src.read(1)
        assert b._block_factor(src, grid) == 2
        for classes in ((10, 95), (95,), (20,), (60,), (50,)):
            fast = b._class_share(cls, src, grid, classes)
            from rasterio.enums import Resampling
            from rasterio.warp import reproject
            hit = np.where(np.isin(cls, classes), 100, 0).astype(np.uint8)
            out = np.zeros(grid.shape, dtype=np.uint8)
            reproject(hit, out, src_transform=src.transform, src_crs=src.crs, dst_transform=grid.transform,
                      dst_crs="EPSG:4326", resampling=Resampling.average)
            assert fast.dtype == np.float32 and (fast == out.astype(np.float32) / 100.0).all()
        assert b._block_factor(src, Grid(tile, 150)) is None    # not a whole number of pixels: reproject
