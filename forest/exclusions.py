"""Turn OSM features into a no-tree mask on the working grid.

Cut-outs are drawn at `SUPERSAMPLE`× the grid detail and a grid cell is cleared only when
they cover at least `cutout_min_cover` of it. ("Touching" a cell is not enough: in a dense
city that would clear almost every cell and remove real street trees and parks.)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import LineString, Polygon

# Half-widths in metres ≈ half the paved carriageway + ~1 m shoulder (scaled by
# profile `road_clearance_scale`).
ROAD_HALF_WIDTH = {
    "motorway": 12, "trunk": 9, "primary": 7, "secondary": 6, "tertiary": 5,
    "motorway_link": 5, "trunk_link": 5, "primary_link": 4.5, "secondary_link": 4, "tertiary_link": 4,
    "residential": 4, "unclassified": 4, "living_street": 3, "service": 3, "road": 4,
    "track": 2.5,
}
RAIL_HALF_WIDTH = 5
POWER_HALF_WIDTH = 10   # cleared corridors under power lines
RIVER_HALF_WIDTH = 12
TAXIWAY_HALF_WIDTH = 20
APRON_MARGIN = 5
SUPERSAMPLE = 2

DEFAULTS = {
    "road_clearance_scale": 1.0,
    "building_clearance_m": 2.0,
    "runway_clearance_m": 75.0,
    "clear_airport_grounds": 0,
    "cutout_min_cover": 0.5,
}


def _deg(m: float, lat: float) -> float:
    # Longitude scale (the larger one) so buffers are never too thin.
    return m / (111_320.0 * math.cos(math.radians(lat)))


def _coords(el: dict) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]


def _relation_polys(el: dict) -> list[Polygon]:
    polys = []
    for m in el.get("members", []):
        if m.get("role") != "outer" or not m.get("geometry"):
            continue
        pts = [(p["lon"], p["lat"]) for p in m["geometry"] if p]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            polys.append(Polygon(pts))
    return polys


def osm_geometries(osm_json: Path, lat_mid: float, profile: dict | None = None) -> dict[str, list]:
    p = {**DEFAULTS, **(profile or {})}
    d = lambda m: _deg(m, lat_mid)  # noqa: E731
    road_k = float(p["road_clearance_scale"])
    bld = float(p["building_clearance_m"])
    rwy = float(p["runway_clearance_m"])
    data = json.loads(osm_json.read_text(encoding="utf-8"))
    out: dict[str, list] = {"roads": [], "buildings": [], "water": [], "runways": [],
                            "airport_grounds": [], "corridors": []}

    def building(poly: Polygon) -> None:
        out["buildings"].append(poly.buffer(d(bld)) if bld > 0 else poly)

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "relation":
            polys = _relation_polys(el)
            if "building" in tags:
                for poly in polys:
                    building(poly)
            elif tags.get("natural") == "water":
                out["water"] += polys
            elif tags.get("aeroway") == "aerodrome":
                out["airport_grounds"] += polys
            continue
        pts = _coords(el)
        if len(pts) < 2:
            continue
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        hw = tags.get("highway")
        aw = tags.get("aeroway")
        if hw in ROAD_HALF_WIDTH:
            if road_k > 0:
                out["roads"].append(LineString(pts).buffer(d(ROAD_HALF_WIDTH[hw] * road_k), cap_style="flat"))
        elif "building" in tags and closed:
            building(Polygon(pts))
        elif tags.get("natural") == "water" or tags.get("landuse") == "reservoir" or tags.get("waterway") == "riverbank":
            if closed:
                out["water"].append(Polygon(pts))
        elif tags.get("waterway") in ("river", "canal"):
            out["water"].append(LineString(pts).buffer(d(RIVER_HALF_WIDTH)))
        elif aw == "aerodrome" and closed:
            out["airport_grounds"].append(Polygon(pts))
        elif aw == "runway":
            # Centre line (usual) → strip of ±runway_clearance_m; area → grown so the
            # strip reaches about the same distance from the centre.
            if closed:
                out["runways"].append(Polygon(pts).buffer(d(max(rwy - 25, 5))))
            else:
                out["runways"].append(LineString(pts).buffer(d(rwy), cap_style="flat"))
        elif aw in ("taxiway", "apron", "helipad"):
            g = Polygon(pts).buffer(d(APRON_MARGIN)) if closed else LineString(pts).buffer(d(TAXIWAY_HALF_WIDTH))
            out["runways"].append(g)
        elif tags.get("railway") == "rail":
            out["corridors"].append(LineString(pts).buffer(d(RAIL_HALF_WIDTH), cap_style="flat"))
        elif tags.get("power") == "line":
            out["corridors"].append(LineString(pts).buffer(d(POWER_HALF_WIDTH), cap_style="flat"))
    if not int(p["clear_airport_grounds"]):
        out["airport_grounds"] = []
    return out


def osm_tree_hints(osm_json: Path) -> dict[str, list]:
    """Real trees mapped in OSM: green areas (parks, gardens, golf, woods, forests),
    single trees (points), tree rows (lines), plantations; plus the areas that steer town trees
    (residential vs industrial/commercial land) and scrub (natural=scrub/heath)."""
    from forest.fetch.osm_pbf import NO_TOWN_TREE_LANDUSE, TOWN_LANDUSE, is_green_area, is_plantation, is_scrub_area

    data = json.loads(osm_json.read_text(encoding="utf-8"))
    out: dict[str, list] = {"green": [], "points": [], "rows": [], "plantations": [],
                            "residential": [], "no_town_trees": [], "scrub": [], "airports": []}

    def areas(tags: dict, polys: list) -> None:
        if tags.get("aeroway") == "aerodrome":   # airport grounds: never town trees or scrub
            out["airports"] += [p for p in polys if p.is_valid]
            return
        lu = tags.get("landuse")
        key = ("residential" if lu in TOWN_LANDUSE else "no_town_trees" if lu in NO_TOWN_TREE_LANDUSE
               else "scrub" if is_scrub_area(tags) else None)
        if key:
            out[key] += [p for p in polys if p.is_valid]

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            if tags.get("natural") == "tree":
                out["points"].append((el["lon"], el["lat"]))
            continue
        if el["type"] == "relation":
            if is_plantation(tags) or (tags.get("landuse") == "forest" and tags.get("leaf_type")):
                out["plantations"] += [(p, tags) for p in _relation_polys(el) if p.is_valid]
            if is_green_area(tags):
                out["green"] += _relation_polys(el)
            areas(tags, _relation_polys(el))
            continue
        if (is_plantation(tags) or (tags.get("landuse") == "forest" and (tags.get("leaf_type") or tags.get("genus")))):
            pts = _coords(el)
            if len(pts) >= 4 and pts[0] == pts[-1]:
                poly = Polygon(pts)
                if poly.is_valid:
                    out["plantations"].append((poly, tags))
        pts = _coords(el)
        if len(pts) < 2:
            continue
        if tags.get("natural") == "tree_row":
            out["rows"].append(LineString(pts))
        elif len(pts) >= 4 and pts[0] == pts[-1]:
            if is_green_area(tags):
                poly = Polygon(pts)
                if poly.is_valid:
                    out["green"].append(poly)
            else:
                areas(tags, [Polygon(pts)])
    return out


def coverage_mask(geoms: list, grid, min_cover: float = 0.5) -> np.ndarray:
    valid = [g for g in geoms if g is not None and not g.is_empty and g.is_valid]
    if not valid:
        return np.zeros(grid.shape, dtype=bool)
    return _coverage(valid, grid) >= min_cover


def _coverage(geoms: list, grid) -> np.ndarray:
    """Share (0-1) of each grid cell covered by the geometries (drawn at SUPERSAMPLE×)."""
    s = SUPERSAMPLE
    h, w = grid.shape
    tr: Affine = grid.transform @ Affine.scale(1 / s)
    hi = rasterize(((g, 1) for g in geoms), out_shape=(h * s, w * s), transform=tr, dtype="uint8")
    return hi.reshape(h, s, w, s).mean(axis=(1, 3))


def exclusion_mask(geoms: dict[str, list], grid, min_cover: float = 0.5) -> tuple[np.ndarray, dict[str, int]]:
    """Boolean mask (True = no trees) and per-category cell counts."""
    mask = np.zeros(grid.shape, dtype=bool)
    stats = {}
    for name, items in geoms.items():
        valid = [g for g in items if g is not None and not g.is_empty and g.is_valid]
        if not valid:
            stats[name] = 0
            continue
        m = _coverage(valid, grid) >= min_cover
        stats[name] = int(m.sum())
        mask |= m
    return mask, stats
