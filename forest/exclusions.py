"""Turn OSM features into a no-tree mask on the working grid.

Cut-outs are drawn at `SUPERSAMPLE`× the grid detail and a grid cell is cleared only when
they cover at least `cutout_min_cover` of it. ("Touching" a cell is not enough: in a dense
city that would clear almost every cell and remove real street trees and parks.)
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import shapely
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
# No town trees this far past each end of a runway's centre line: ICAO's strip end (60 m) + runway
# end safety area (240 m), where the approach lights stand. SKRG had town trees 222-252 m past the
# runway 01 end, just outside the airport fence. Only town trees (which we invent): real forest the
# satellite sees and OSM-mapped trees past a runway end are kept ("runway_ends" is not a cut-out).
RUNWAY_END_M = 300
SUPERSAMPLE = 2

DEFAULTS = {
    "road_clearance_scale": 1.0,
    "building_clearance_m": 2.0,
    "runway_clearance_m": 75.0,
    "clear_airport_grounds": 0,
    "cutout_min_cover": 0.5,
}


# Segments per quarter circle when growing building outlines by building_clearance_m. Buildings
# are most of a city's geometry; 16 (shapely's default) made each corner a 17-point arc that
# rasterising at ~9 m sub-cells can hardly tell from 4 (a 2 m clearance: the arcs are < 5 cm apart).
BUILDING_QUAD_SEGS = 4


def _deg(m: float, lat: float) -> float:
    # Longitude scale (the larger one) so buffers are never too thin.
    return m / (111_320.0 * math.cos(math.radians(lat)))


def extend_ends(pts: list, m: float, lat: float) -> list:
    """The line (lon, lat) points lengthened by `m` metres at both ends, along its end segments."""
    if m <= 0 or len(pts) < 2:
        return list(pts)
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_574.0

    def beyond(a, b):  # the point m metres past b, coming from a
        dx, dy = (b[0] - a[0]) * kx, (b[1] - a[1]) * ky
        n = math.hypot(dx, dy)
        if n == 0:
            return None
        return (b[0] + dx / n * m / kx, b[1] + dy / n * m / ky)

    start, end = beyond(pts[1], pts[0]), beyond(pts[-2], pts[-1])
    return ([start] if start else []) + list(pts) + ([end] if end else [])


def _pts(geom: list) -> list:
    """A way's or ring's points: osm.json VERSION 8+ stores [lon, lat] pairs (used as they are,
    no copy); older files and hand-made test data have {"lat", "lon"} objects."""
    if geom and isinstance(geom[0], dict):
        return [(p["lon"], p["lat"]) for p in geom if p]
    return geom


def _coords(el: dict) -> list:
    return _pts(el.get("geometry", []))


def _relation_polys(el: dict) -> list[Polygon]:
    polys = []
    for m in el.get("members", []):
        if m.get("role") != "outer" or not m.get("geometry"):
            continue
        pts = _pts(m["geometry"])
        if len(pts) >= 4 and pts[0] == pts[-1]:
            polys.append(Polygon(pts))
    return polys


def load_osm(osm_json: Path | dict) -> dict:
    """The tile's osm.json (a city tile is hundreds of MB: parse it once and pass the dict on)."""
    if isinstance(osm_json, dict):
        return osm_json
    return json.loads(Path(osm_json).read_text(encoding="utf-8"))


def _bulk(kind: str, pts_list: list) -> np.ndarray:
    """Polygons (kind "P", closed rings) or lines ("L") from lists of (lon, lat), in one shapely
    call instead of one Python object each: the same coordinates, so the same geometries as
    Polygon(pts) / LineString(pts)."""
    if not pts_list:
        return np.empty(0, dtype=object)
    lens = np.fromiter((len(p) for p in pts_list), dtype=np.int64, count=len(pts_list))
    coords = np.fromiter(itertools.chain.from_iterable(itertools.chain.from_iterable(pts_list)),
                         dtype=np.float64, count=2 * int(lens.sum())).reshape(-1, 2)
    idx = np.repeat(np.arange(len(pts_list)), lens)
    if kind == "L":
        return shapely.linestrings(coords, indices=idx)
    return shapely.polygons(shapely.linearrings(coords, indices=idx))


class _ToBuffer:
    """Geometries of one category, built and buffered in one shapely call per kind / cap style
    instead of one Python call each (~10 s for a city tile). Same GEOS operations and parameters,
    same order. A geometry is given either ready-made or as ("P" | "L", points) to build later."""

    def __init__(self, quad_segs: int = 16) -> None:
        self.items: list[tuple] = []   # (geometry, distance or None = keep as is, cap style)
        self.quad_segs = quad_segs

    def add(self, g, dist: float | None = None, cap: str = "round") -> None:
        self.items.append((g, dist, cap))

    def poly(self, pts: list, dist: float | None = None, cap: str = "round") -> None:
        self.items.append((("P", pts), dist, cap))

    def line(self, pts: list, dist: float | None = None, cap: str = "round") -> None:
        self.items.append((("L", pts), dist, cap))

    def result(self) -> list:
        out = [g for g, _, _ in self.items]
        for kind in ("P", "L"):
            idx = [i for i, g in enumerate(out) if type(g) is tuple and g[0] == kind]
            for i, g in zip(idx, _bulk(kind, [out[i][1] for i in idx])):
                out[i] = g
        for cap in ("round", "flat"):
            idx = [i for i, (_, dist, c) in enumerate(self.items) if dist is not None and c == cap]
            if idx:
                geoms = np.array([out[i] for i in idx], dtype=object)
                dists = np.array([self.items[i][1] for i in idx], dtype=float)
                # quad_segs 16 = Polygon.buffer()'s default (shapely.buffer()'s own is 8: coarser).
                for i, g in zip(idx, shapely.buffer(geoms, dists, quad_segs=self.quad_segs, cap_style=cap)):
                    out[i] = g
        return out


def osm_geometries(osm_json: Path | dict, lat_mid: float, profile: dict | None = None) -> dict[str, list]:
    from forest.fetch.osm_pbf import is_bare_area

    p = {**DEFAULTS, **(profile or {})}
    d = lambda m: _deg(m, lat_mid)  # noqa: E731
    road_k = float(p["road_clearance_scale"])
    bld = float(p["building_clearance_m"])
    rwy = float(p["runway_clearance_m"])
    data = load_osm(osm_json)
    cats = ("roads", "buildings", "water", "runways", "runway_ends", "airport_grounds", "corridors", "bare")
    acc = {k: _ToBuffer(BUILDING_QUAD_SEGS if k == "buildings" else 16) for k in cats}

    def building(poly: Polygon) -> None:
        acc["buildings"].add(poly, d(bld) if bld > 0 else None)

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "relation":
            polys = _relation_polys(el)
            if "building" in tags:
                for poly in polys:
                    building(poly)
            elif tags.get("natural") == "water":
                for poly in polys:
                    acc["water"].add(poly)
            elif tags.get("aeroway") == "aerodrome":
                for poly in polys:
                    acc["airport_grounds"].add(poly)
            elif is_bare_area(tags):
                for poly in polys:
                    acc["bare"].add(poly)
            continue
        pts = _coords(el)
        if len(pts) < 2:
            continue
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        hw = tags.get("highway")
        aw = tags.get("aeroway")
        # Ways are passed as points and built in bulk by _ToBuffer (same Polygon / LineString).
        if hw in ROAD_HALF_WIDTH:
            if road_k > 0:
                acc["roads"].line(pts, d(ROAD_HALF_WIDTH[hw] * road_k), "flat")
        elif "building" in tags and closed:
            acc["buildings"].poly(pts, d(bld) if bld > 0 else None)
        elif tags.get("natural") == "water" or tags.get("landuse") == "reservoir" or tags.get("waterway") == "riverbank":
            if closed:
                acc["water"].poly(pts)
        elif tags.get("waterway") in ("river", "canal"):
            acc["water"].line(pts, d(RIVER_HALF_WIDTH))
        elif aw == "aerodrome" and closed:
            acc["airport_grounds"].poly(pts)
        elif aw == "runway":
            # Centre line (usual) → strip of ±runway_clearance_m; area → grown so the
            # strip reaches about the same distance from the centre.
            if closed:
                acc["runways"].poly(pts, d(max(rwy - 25, 5)))
            else:
                acc["runways"].line(pts, d(rwy), "flat")
                if rwy > 0:
                    acc["runway_ends"].line(extend_ends(pts, RUNWAY_END_M, lat_mid), d(rwy), "flat")
        elif aw in ("taxiway", "apron", "helipad"):
            if closed:
                acc["runways"].poly(pts, d(APRON_MARGIN))
            else:
                acc["runways"].line(pts, d(TAXIWAY_HALF_WIDTH))
        elif tags.get("railway") == "rail":
            acc["corridors"].line(pts, d(RAIL_HALF_WIDTH), "flat")
        elif tags.get("power") == "line":
            acc["corridors"].line(pts, d(POWER_HALF_WIDTH), "flat")
        elif closed and is_bare_area(tags):
            acc["bare"].poly(pts)
    out = {k: acc[k].result() for k in cats}
    if not int(p["clear_airport_grounds"]):
        out["airport_grounds"] = []
    return out


# OSM wetland=* values that can hold bushes ("" = natural=wetland without a type). Swamps are
# wooded; mangroves have their own biome; salt marshes, reed beds, tidal flats and salterns are
# grass, reeds or mud and get nothing.
OPEN_WETLAND = ("", "marsh", "bog", "fen", "wet_meadow", "string_bog", "palsa_bog")


def osm_tree_hints(osm_json: Path | dict) -> dict[str, list]:
    """Real trees mapped in OSM: green areas (parks, gardens, golf, woods, forests),
    single trees (points), tree rows (lines), plantations; plus the areas that steer town trees
    (residential vs industrial/commercial land), scrub (natural=scrub/heath), mangroves
    (natural=wetland + wetland=mangrove), swamps (wooded wetland), open wetlands (marsh, bog, fen,
    reedbed, wet meadow, salt marsh or untyped) and woods' leaf type ("leaf": (polygon, leaf_type))."""
    from forest.fetch.osm_pbf import (
        NO_TOWN_TREE_LANDUSE,
        TOWN_LANDUSE,
        is_green_area,
        is_mangrove_area,
        is_parking_area,
        is_plantation,
        is_scrub_area,
        is_wetland_area,
    )

    data = load_osm(osm_json)
    out: dict[str, list] = {"green": [], "points": [], "rows": [], "plantations": [],
                            "residential": [], "no_town_trees": [], "scrub": [], "airports": [], "mangrove": [],
                            "swamp": [], "wetland": [], "leaf": []}

    def area_key(tags: dict) -> str | None:
        """Which list an area with these tags goes to (None = none)."""
        if tags.get("aeroway") == "aerodrome":   # airport grounds: never town trees or scrub
            return "airports"
        if is_mangrove_area(tags):
            return "mangrove"
        if is_wetland_area(tags):
            kind = tags.get("wetland", "")
            return "swamp" if kind == "swamp" else "wetland" if kind in OPEN_WETLAND else None
        lu = tags.get("landuse")
        return ("residential" if lu in TOWN_LANDUSE
                else "no_town_trees" if lu in NO_TOWN_TREE_LANDUSE or is_parking_area(tags)
                else "scrub" if is_scrub_area(tags) else None)

    # Closed ways become polygons only when their tags put them in a list (most closed ways are
    # buildings, which need none here). They're built and checked for validity in bulk at the
    # end: the lists hold _Ring(i) placeholders until then, in the same order as before, and
    # every way polygon is kept only if valid, as before.
    rings: list = []

    def ring(pts: list) -> _Ring:
        rings.append(pts)
        return _Ring(len(rings) - 1)

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            if tags.get("natural") == "tree":
                out["points"].append((el["lon"], el["lat"]))
            continue
        if el["type"] == "relation":
            polys = _relation_polys(el)
            if is_plantation(tags) or (tags.get("landuse") == "forest" and tags.get("leaf_type")):
                out["plantations"] += [(p, tags) for p in polys if p.is_valid]
            if is_green_area(tags):
                out["green"] += polys      # relation green areas: no validity filter (as before)
                if tags.get("leaf_type"):
                    out["leaf"] += [(p, tags["leaf_type"]) for p in polys if p.is_valid]
            key = area_key(tags)
            if key:
                out[key] += [p for p in polys if p.is_valid]
            continue
        pts = _coords(el)
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        r = None
        if (is_plantation(tags) or (tags.get("landuse") == "forest" and (tags.get("leaf_type") or tags.get("genus")))):
            if closed:
                r = ring(pts)
                out["plantations"].append((r, tags))
        if len(pts) < 2:
            continue
        if tags.get("natural") == "tree_row":
            out["rows"].append(LineString(pts))
        elif closed:
            if is_green_area(tags):
                r = r or ring(pts)
                out["green"].append(r)
                if tags.get("leaf_type"):
                    out["leaf"].append((r, tags["leaf_type"]))
            else:
                key = area_key(tags)
                if key:
                    out[key].append(r or ring(pts))
    if rings:
        polys = _bulk("P", rings)
        valid = shapely.is_valid(polys)
        for key, items in out.items():
            if key in ("points", "rows"):
                continue
            res = []
            for it in items:
                g = it[0] if type(it) is tuple else it
                if type(g) is _Ring:
                    if not valid[g.i]:
                        continue
                    g = polys[g.i]
                res.append((g, it[1]) if type(it) is tuple else g)
            out[key] = res
    return out


class _Ring:
    """Placeholder for a way polygon that osm_tree_hints builds in bulk (index into its list)."""
    __slots__ = ("i",)

    def __init__(self, i: int) -> None:
        self.i = i


def _valid(geoms: list) -> list:
    return [g for g in geoms if g is not None and not g.is_empty and g.is_valid]


def coverage_mask(geoms: list, grid, min_cover: float = 0.5) -> np.ndarray:
    valid = _valid(geoms)
    if not valid:
        return np.zeros(grid.shape, dtype=bool)
    return _covered(valid, grid, min_cover)


def _geojson(geoms: list):
    """(GeoJSON-like dict, 1) pairs for rasterize. Rasterio would call each shapely geometry's
    __geo_interface__, which walks every coordinate in Python (≈14 s for a city tile); here the
    coordinates come out of shapely as numpy arrays. Multipolygons are burnt part by part, which
    covers exactly the same cells."""
    parts = shapely.get_parts(np.asarray(geoms, dtype=object))
    is_poly = shapely.get_type_id(parts) == 3
    for g in parts[~is_poly]:            # anything unusual: let rasterio convert it
        yield g, 1
    polys = parts[is_poly]
    for start in range(0, polys.size, _GEOJSON_BATCH):   # batches: never all coordinates at once
        batch = polys[start:start + _GEOJSON_BATCH]
        rings, ring_poly = shapely.get_rings(batch, return_index=True)
        coords, ring_idx = shapely.get_coordinates(rings, return_index=True)
        ring_coords = np.split(coords, np.cumsum(np.bincount(ring_idx, minlength=rings.size))[:-1])
        first = np.searchsorted(ring_poly, np.arange(batch.size + 1))
        for i in range(batch.size):
            yield {"type": "Polygon",
                   "coordinates": [ring_coords[r].tolist() for r in range(first[i], first[i + 1])]}, 1


_GEOJSON_BATCH = 5000
_RASTERIZE_BATCH = 50_000


def _covered(geoms: list, grid, min_cover: float) -> np.ndarray:
    """Cells covered by the geometries for at least `min_cover` of their area (drawn at
    SUPERSAMPLE×). Counts sub-cells in uint8 instead of averaging in float64: same cells,
    an eighth of the memory."""
    s = SUPERSAMPLE
    h, w = grid.shape
    tr: Affine = grid.transform @ Affine.scale(1 / s)
    hi = np.zeros((h * s, w * s), dtype=np.uint8)
    # Burn in batches: rasterio builds every shape of a call in memory before burning
    # (~2.5 GB extra for a city's roads in one call). Every shape burns 1, so it's the same cells.
    shapes_ = _geojson(geoms)
    while batch := list(itertools.islice(shapes_, _RASTERIZE_BATCH)):
        rasterize(batch, out=hi, transform=tr)
    # count / s² >= min_cover  ⇔  count >= min_cover · s²  (s² is a power of 2: exact in float)
    return block_count(hi, s) >= min_cover * s * s


def block_count(a: np.ndarray, k: int) -> np.ndarray:
    """Sum of every k×k block of a 0/1 array (uint8, so k ≤ 15). The same numbers as
    a.reshape(h, k, w, k).sum(axis=(1, 3)), from k² strided slices: ~5× faster at 12000²."""
    out = a[0::k, 0::k].astype(np.uint8)
    for i in range(k):
        for j in range(k):
            if i or j:
                out += a[i::k, j::k]
    return out


def exclusion_mask(geoms: dict[str, list], grid, min_cover: float = 0.5) -> tuple[np.ndarray, dict[str, int]]:
    """Boolean mask (True = no trees) and per-category cell counts."""
    mask = np.zeros(grid.shape, dtype=bool)
    stats = {}
    for name, items in geoms.items():
        valid = _valid(items)
        if not valid:
            stats[name] = 0
            continue
        m = _covered(valid, grid, min_cover)
        stats[name] = int(m.sum())
        mask |= m
    return mask, stats
