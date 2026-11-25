"""The fast paths give exactly what the slower code they replaced gave (tiles must not change)."""

import numpy as np
import pytest
import shapely
from affine import Affine
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.warp import reproject
from scipy.ndimage import median_filter
from shapely.geometry import LineString, Polygon, shape

from forest import build, exclusions
from forest.tiles import Grid, Tile


@pytest.mark.parametrize("shape_", [(1, 1), (2, 7), (31, 29), (200, 201)])
@pytest.mark.parametrize("dens", [0.2, 0.5, 0.8])
def test_majority3_is_scipy_median(shape_, dens):
    b = np.random.default_rng(hash((shape_, dens)) % 2**32).random(shape_) < dens
    assert np.array_equal(build.majority3(b), median_filter(b.astype(np.uint8), size=3).astype(bool))


@pytest.mark.parametrize("k", [1, 2, 3, 4])
def test_block_count_is_reshape_sum(k):
    a = np.random.default_rng(k).random((40 * k, 30 * k)) < 0.4
    assert np.array_equal(exclusions.block_count(a, k), a.reshape(40, k, 30, k).sum(axis=(1, 3)))


def test_tie_free_factors():
    assert {1, 2, 3, 5, 6}.issubset(build._TIE_FREE)
    assert not {4, 8, 12} & build._TIE_FREE    # 100·c/16 = 37.5 …: GDAL rounds those either way


class _Src:
    def __init__(self, transform):
        self.transform, self.crs = transform, CRS.from_epsg(4326)
        self.height = self.width = None


@pytest.mark.parametrize("k", [2, 3, 6])
def test_class_share_block_path_equals_gdal_average(k):
    """Low profile (k = 3) and medium (k = 2) take the block-count path; it must give GDAL's
    average resampling value for value."""
    cells = 60
    tile = Tile(6, -76)
    grid = Grid(tile, cells)
    n = cells * k
    src = _Src(Affine(1 / n, 0, -76, 0, -1 / n, 7))
    src.height = src.width = n
    cls = np.random.default_rng(k).choice(np.array([10, 20, 30, 95], dtype=np.uint8), size=(n, n))
    assert build._block_factor(src, grid) == k
    fast = build._class_share(cls, src, grid, (10, 95))
    hit = np.where(np.isin(cls, (10, 95)), 100, 0).astype(np.uint8)
    out = np.zeros(grid.shape, dtype=np.uint8)
    reproject(hit, out, src_transform=src.transform, src_crs=src.crs, dst_transform=grid.transform,
              dst_crs="EPSG:4326", resampling=Resampling.average)
    assert np.array_equal(fast, out.astype(np.float32) / 100.0)


def test_polygons_from_geojson_is_shape():
    a = (np.random.default_rng(0).random((120, 120)) < 0.55).astype(np.int32) * 3
    found = [g for g, _ in shapes(a, mask=a > 0, transform=Affine(1 / 6000, 0, -76, 0, -1 / 6000, 7),
                                  connectivity=4)]
    assert any(len(g["coordinates"]) > 1 for g in found)          # some have holes
    new = build._polygons_from_geojson(found)
    assert [shapely.to_wkb(g) for g in new] == [shapely.to_wkb(shape(g)) for g in found]


def test_bulk_ways_are_polygon_and_linestring():
    rings = [[(0, 0), (1, 0), (1, 1), (0, 0)], [(2, 2), (3, 2), (3, 3), (2, 3), (2, 2)]]
    lines = [[(0, 0), (1, 1)], [(5, 5), (6, 5), (7, 6)]]
    assert [g.wkb for g in exclusions._bulk("P", rings)] == [Polygon(r).wkb for r in rings]
    assert [g.wkb for g in exclusions._bulk("L", lines)] == [LineString(p).wkb for p in lines]


def _way(i, tags, pts):
    return {"type": "way", "id": i, "tags": tags, "geometry": [{"lon": x, "lat": y} for x, y in pts]}


def test_tree_hints_keep_order_and_validity_filter():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    bow = [(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]           # self-crossing: invalid
    osm = {"elements": [
        _way(1, {"landuse": "residential"}, sq),
        _way(2, {"leisure": "park", "leaf_type": "broadleaved"}, sq),
        _way(3, {"landuse": "residential"}, bow),
        _way(4, {"building": "yes"}, sq),                     # no list: never built here
        _way(5, {"landuse": "orchard", "leisure": "park"}, sq),
        _way(6, {"landuse": "industrial"}, [(5, 5), (6, 5), (6, 6), (5, 5)]),
        {"type": "relation", "id": 7, "tags": {"leisure": "park"},
         "members": [{"role": "outer", "geometry": [{"lon": x, "lat": y} for x, y in bow]}]},
    ]}
    h = exclusions.osm_tree_hints(osm)
    assert [g.wkb for g in h["residential"]] == [Polygon(sq).wkb]
    assert len(h["green"]) == 3 and not h["green"][2].is_valid   # relation green: kept as before
    assert h["leaf"][0][1] == "broadleaved" and h["leaf"][0][0].wkb == Polygon(sq).wkb
    assert len(h["plantations"]) == 1 and h["plantations"][0][1]["landuse"] == "orchard"
    assert len(h["no_town_trees"]) == 1
