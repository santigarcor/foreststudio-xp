"""OSM cut-outs (roads, buildings, water, airports) and OSM tree hints."""

import math

import numpy as np

from forest.exclusions import (coverage_mask, exclusion_mask, osm_geometries, osm_tree_hints)
from forest.tiles import Grid, Tile
from tests.conftest import write_json

LAT = 6.5


def way(i, tags, pts):
    return {"type": "way", "id": i, "tags": tags, "geometry": [{"lon": x, "lat": y} for x, y in pts]}


def sq(x, y, d):
    return [(x, y), (x + d, y), (x + d, y + d), (x, y + d), (x, y)]


def osm_file(tmp_path, elements):
    return write_json(tmp_path / "osm.json", {"version": 3, "elements": elements})


def test_geometries_by_category(tmp_path):
    els = [
        way(1, {"highway": "residential"}, [(-75.6, 6.5), (-75.5, 6.5)]),
        way(2, {"building": "yes"}, sq(-75.55, 6.55, 0.0005)),
        way(3, {"natural": "water"}, sq(-75.3, 6.3, 0.01)),
        way(4, {"aeroway": "runway"}, [(-75.42, 6.15), (-75.42, 6.18)]),
        way(5, {"aeroway": "aerodrome"}, sq(-75.45, 6.14, 0.05)),
        way(6, {"power": "line"}, [(-75.9, 6.9), (-75.8, 6.9)]),
        way(7, {"highway": "footway"}, [(-75.6, 6.6), (-75.5, 6.6)]),   # footpaths are not cut
    ]
    g = osm_geometries(osm_file(tmp_path, els), LAT)
    assert len(g["roads"]) == 1 and len(g["buildings"]) == 1 and len(g["water"]) == 1
    assert len(g["runways"]) == 1 and len(g["corridors"]) == 1
    assert g["airport_grounds"] == []  # whole grounds only with clear_airport_grounds = 1
    g2 = osm_geometries(osm_file(tmp_path, els), LAT, {"clear_airport_grounds": 1})
    assert len(g2["airport_grounds"]) == 1


def test_road_width_scales_with_setting(tmp_path):
    els = [way(1, {"highway": "primary"}, [(-75.6, 6.5), (-75.5, 6.5)])]
    w1 = osm_geometries(osm_file(tmp_path, els), LAT, {"road_clearance_scale": 1.0})["roads"][0]
    w2 = osm_geometries(osm_file(tmp_path, els), LAT, {"road_clearance_scale": 2.0})["roads"][0]
    assert w2.area == pytest_approx(w1.area * 2, 0.02)
    assert osm_geometries(osm_file(tmp_path, els), LAT, {"road_clearance_scale": 0})["roads"] == []


def pytest_approx(v, rel):
    import pytest
    return pytest.approx(v, rel=rel)


def test_runway_strip_half_width(tmp_path):
    els = [way(1, {"aeroway": "runway"}, [(-75.42, 6.10), (-75.42, 6.20)])]
    strip = osm_geometries(osm_file(tmp_path, els), LAT, {"runway_clearance_m": 75})["runways"][0]
    deg_per_m = 1 / (111_320 * math.cos(math.radians(LAT)))
    width_deg = strip.bounds[2] - strip.bounds[0]
    assert width_deg == pytest_approx(150 * deg_per_m, 0.01)


def test_cutouts_need_real_coverage_not_a_touch():
    """A small building touching a cell must not clear it; a big one covering it must."""
    from shapely.geometry import box
    g = Grid(Tile(6, -76), 600)            # cells of 1/600°
    cell = 1 / 600
    tiny = box(-76 + 0.1 * cell, 7 - 0.2 * cell, -76 + 0.2 * cell, 7 - 0.1 * cell)
    big = box(-76 + 10 * cell, 7 - 11 * cell, -76 + 11 * cell, 7 - 10 * cell)
    m, stats = exclusion_mask({"buildings": [tiny, big]}, g, min_cover=0.5)
    assert not m[0, 0]
    assert m[10, 10]
    assert stats["buildings"] == int(m.sum())


def test_coverage_mask_empty():
    g = Grid(Tile(6, -76), 50)
    assert coverage_mask([], g).sum() == 0


def test_tree_hints(tmp_path):
    els = [
        {"type": "node", "id": 1, "tags": {"natural": "tree"}, "lat": 6.5, "lon": -75.5},
        way(2, {"natural": "tree_row"}, [(-75.6, 6.6), (-75.59, 6.6)]),
        way(3, {"leisure": "golf_course"}, sq(-75.7, 6.7, 0.01)),
        way(4, {"landuse": "orchard", "trees": "oil_palms"}, sq(-75.8, 6.8, 0.01)),
        way(5, {"landuse": "forest", "leaf_type": "needleleaved"}, sq(-75.85, 6.85, 0.01)),
        way(6, {"highway": "primary"}, [(-75.0, 6.0), (-75.1, 6.1)]),
    ]
    h = osm_tree_hints(osm_file(tmp_path, els))
    assert h["points"] == [(-75.5, 6.5)]
    assert len(h["rows"]) == 1
    assert len(h["green"]) == 2               # golf course + forest
    assert len(h["plantations"]) == 2         # orchard + needleleaved forest
    assert h["plantations"][0][1]["trees"] == "oil_palms"
