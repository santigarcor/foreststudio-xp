"""Read OSM features for one or many tiles from a local Geofabrik .osm.pbf extract.

One pass over the file serves any number of tiles: every element is streamed into each
requested tile it touches, so memory stays low even for a whole country.

Writes cache/tiles/<tile>/osm.json in the same shape as Overpass `out geom`:
- nodes:     {"type": "node", "id", "tags", "lat", "lon"}            (natural=tree)
- ways:      {"type": "way", "id", "tags", "geometry": [{"lat", "lon"}, ...]}
- multipolygon relations:
             {"type": "relation", "id", "tags", "members": [{"role": "outer", "geometry": [...]}]}

Kinds of features:
- no-tree cut-outs: roads, buildings, water, aeroways, rail, power lines;
- tree hints: single trees, tree rows, woods/forests, parks, gardens, golf courses, mangroves;
- plantations: orchards, plantations, vineyards, farmland with a crop/trees tag.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import osmium

from forest import config
from forest.tiles import Tile

PBF_DIR = config.CACHE_ROOT / "osm"
VERSION = 5  # 5: mangroves (natural=wetland + wetland=mangrove). Bump when the set of extracted features changes (old caches are re-extracted)

_WATERWAY = re.compile(r"^(river|riverbank|canal)$")
_AEROWAY = re.compile(r"^(aerodrome|runway|taxiway|apron|helipad)$")
GREEN_LEISURE = ("park", "golf_course", "garden", "nature_reserve")
PLANTATION_LANDUSE = ("orchard", "plantation", "vineyard")
# Town trees go in residential land, never in industrial/commercial zones.
TOWN_LANDUSE = ("residential",)
NO_TOWN_TREE_LANDUSE = ("industrial", "commercial", "retail")
SCRUB_NATURAL = ("scrub", "heath")


def is_town_area(t) -> bool:
    lu = t.get("landuse")
    return lu in TOWN_LANDUSE or lu in NO_TOWN_TREE_LANDUSE


def is_scrub_area(t) -> bool:
    return t.get("natural") in SCRUB_NATURAL


def is_mangrove_area(t) -> bool:
    return t.get("natural") == "wetland" and t.get("wetland") == "mangrove"


def is_green_area(t) -> bool:
    return (t.get("natural") == "wood" or t.get("landuse") == "forest"
            or t.get("leisure") in GREEN_LEISURE)


def is_plantation(t) -> bool:
    lu = t.get("landuse")
    return lu in PLANTATION_LANDUSE or (
        lu == "farmland" and any(t.get(k) for k in ("crop", "trees", "produce")))


def wanted_way(t) -> bool:
    return (
        "highway" in t or "building" in t
        or t.get("natural") in ("water", "tree_row") or t.get("landuse") == "reservoir"
        or bool(_WATERWAY.match(t.get("waterway", "")))
        or bool(_AEROWAY.match(t.get("aeroway", "")))
        or t.get("railway") == "rail" or t.get("power") == "line"
        or is_green_area(t) or is_plantation(t) or is_town_area(t) or is_scrub_area(t)
        or is_mangrove_area(t)
    )


def wanted_area_relation(t) -> bool:
    return ("building" in t or t.get("natural") == "water" or t.get("aeroway") == "aerodrome"
            or is_green_area(t) or is_plantation(t) or is_town_area(t) or is_scrub_area(t)
            or is_mangrove_area(t))


class _TileWriter:
    """Streams elements into one tile's osm.json (written to .part, renamed on close)."""

    def __init__(self, tile: Tile):
        self.out = tile.cache / "osm.json"
        self.part = self.out.with_suffix(".part")
        self.fh = self.part.open("w", encoding="utf-8")
        self.fh.write(f'{{"version": {VERSION}, "elements": [\n')
        self.first = True
        self.count = 0

    def add(self, el: dict) -> None:
        if not self.first:
            self.fh.write(",\n")
        self.first = False
        self.fh.write(json.dumps(el, separators=(",", ":")))
        self.count += 1

    def close(self) -> None:
        self.fh.write("\n]}\n")
        self.fh.close()
        self.part.replace(self.out)


class _Handler(osmium.SimpleHandler):
    def __init__(self, writers: dict[tuple[int, int], _TileWriter]):
        super().__init__()
        self.writers = writers

    def _targets(self, pts) -> list[_TileWriter]:
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        out = []
        for la in range(math.floor(min(lats)), math.floor(max(lats)) + 1):
            for lo in range(math.floor(min(lons)), math.floor(max(lons)) + 1):
                w = self.writers.get((la, lo))
                if w is not None:
                    out.append(w)
        return out

    def node(self, n):
        if n.tags.get("natural") != "tree" or not n.location.valid():
            return
        lon, lat = n.location.lon, n.location.lat
        w = self.writers.get((math.floor(lat), math.floor(lon)))
        if w is not None:
            w.add({"type": "node", "id": n.id, "tags": dict(n.tags), "lat": lat, "lon": lon})

    def way(self, wy):
        if not wanted_way(wy.tags):
            return
        pts = [(n.lon, n.lat) for n in wy.nodes if n.location.valid()]
        if len(pts) < 2:
            return
        targets = self._targets(pts)
        if not targets:
            return
        el = {"type": "way", "id": wy.id, "tags": dict(wy.tags),
              "geometry": [{"lat": lat, "lon": lon} for lon, lat in pts]}
        for w in targets:
            w.add(el)

    def area(self, a):
        if a.from_way() or not wanted_area_relation(a.tags):
            return
        members, pts_all = [], []
        for ring in a.outer_rings():
            pts = [(n.lon, n.lat) for n in ring if n.location.valid()]
            if len(pts) >= 4:
                members.append({"role": "outer", "geometry": [{"lat": la, "lon": lo} for lo, la in pts]})
                pts_all += pts
        if not members:
            return
        targets = self._targets(pts_all)
        if not targets:
            return
        el = {"type": "relation", "id": a.orig_id(), "tags": dict(a.tags), "members": members}
        for w in targets:
            w.add(el)


def is_current(tile: Tile) -> bool:
    out = tile.cache_path / "osm.json"
    if not out.exists():
        return False
    with out.open("r", encoding="utf-8") as fh:
        head = fh.read(64)
    return f'"version": {VERSION}' in head


def extract_tiles(tiles: list[Tile], pbf: Path, force: bool = False) -> dict[str, int]:
    """One pass over the .pbf for all tiles that need it. Returns element counts per tile."""
    todo = [t for t in tiles if force or not is_current(t)]
    if not todo:
        return {}
    if not pbf.exists():
        raise FileNotFoundError(f"OSM extract not found: {pbf}")
    writers = {(t.lat, t.lon): _TileWriter(t) for t in todo}
    try:
        _Handler(writers).apply_file(str(pbf), locations=True, idx="flex_mem")
    except BaseException:
        for w in writers.values():
            w.fh.close()
            w.part.unlink(missing_ok=True)
        raise
    counts = {}
    for t in todo:
        w = writers[(t.lat, t.lon)]
        w.close()
        counts[t.name] = w.count
    return counts


def extract_tile(tile: Tile, pbf: Path) -> Path:
    extract_tiles([tile], pbf)
    return tile.cache / "osm.json"
