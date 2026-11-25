"""Read OSM features for one or many tiles from a local Geofabrik .osm.pbf extract.

One pass over the file serves any number of tiles: every element is streamed into each
requested tile it touches, so memory stays low even for a whole country. A tile on a border is
filled from every downloaded extract that covers it (one pass per file, elements found in two
files are written once); the files used are listed in the header ("sources").

Writes cache/tiles/<tile>/osm.json (since VERSION 8 coordinates are [lon, lat] pairs, not
Overpass-style {"lat", "lon"} objects: ~40 % smaller and faster to read; same numbers):
- nodes:     {"type": "node", "id", "tags", "lat", "lon"}            (natural=tree)
- ways:      {"type": "way", "id", "tags", "geometry": [[lon, lat], ...]}
- multipolygon relations:
             {"type": "relation", "id", "tags", "members": [{"role": "outer", "geometry": [[lon, lat], ...]}]}

Kinds of features:
- no-tree cut-outs: roads, buildings, water, aeroways, rail, power lines, bare land
  (construction sites, quarries, brownfields, sand, rock);
- no town trees: car parks, industrial/commercial/retail land, landfills;
- tree hints: single trees, tree rows, woods/forests, parks, gardens, golf courses, mangroves;
- plantations: orchards, plantations, vineyards, farmland with a crop/trees tag.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

import numpy as np
import osmium
import osmium.geom

from forest import config
from forest.tiles import Tile

PBF_DIR = config.CACHE_ROOT / "osm"
VERSION = 8  # 8: [lon, lat] pairs (same features as 7; a 7 file is converted in place, see _upgrade_lean); 5: mangroves (natural=wetland + wetland=mangrove); 6: every natural=wetland (swamps, marshes…); 7: bare land (construction, quarry, brownfield, sand, rock), car parks, landfills. Bump when the set of extracted features changes (old caches are re-extracted)

_WATERWAY = re.compile(r"^(river|riverbank|canal)$")
_AEROWAY = re.compile(r"^(aerodrome|runway|taxiway|apron|helipad)$")
GREEN_LEISURE = ("park", "golf_course", "garden", "nature_reserve")
PLANTATION_LANDUSE = ("orchard", "plantation", "vineyard")
# Town trees go in residential land, never in industrial/commercial zones.
TOWN_LANDUSE = ("residential",)
NO_TOWN_TREE_LANDUSE = ("industrial", "commercial", "retail", "landfill")
SCRUB_NATURAL = ("scrub", "heath")
# Land with no trees now, whatever the (older) satellite maps say: building sites cleared after
# 2021-2024 (Jacksonville: 2 of 6.7 km² of construction sites still had forest), quarries, sand.
BARE_LANDUSE = ("construction", "quarry", "brownfield")
BARE_NATURAL = ("sand", "bare_rock")


def is_bare_area(t) -> bool:
    return t.get("landuse") in BARE_LANDUSE or t.get("natural") in BARE_NATURAL


def is_parking_area(t) -> bool:
    """Car parks: no town trees (WorldCover calls them built-up, so they count as town)."""
    return t.get("amenity") == "parking"


def is_town_area(t) -> bool:
    lu = t.get("landuse")
    return lu in TOWN_LANDUSE or lu in NO_TOWN_TREE_LANDUSE


def is_scrub_area(t) -> bool:
    return t.get("natural") in SCRUB_NATURAL


def is_mangrove_area(t) -> bool:
    return t.get("natural") == "wetland" and t.get("wetland") == "mangrove"


def is_wetland_area(t) -> bool:
    """Any OSM wetland: mangrove, swamp (wooded), marsh, bog, fen, reedbed, wet meadow…"""
    return t.get("natural") == "wetland"


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
        or is_wetland_area(t) or is_bare_area(t) or is_parking_area(t)
    )


def wanted_area_relation(t) -> bool:
    return ("building" in t or t.get("natural") == "water" or t.get("aeroway") == "aerodrome"
            or is_green_area(t) or is_plantation(t) or is_town_area(t) or is_scrub_area(t)
            or is_wetland_area(t) or is_bare_area(t) or is_parking_area(t))


# Speed: a country file holds tens of millions of points and lines; calling Python for each one
# just to reject it took ~90 % of a pass (Colombia: 3.7 s to decode in C++, 69 s with a Python
# test per object). These osmium filters drop, in C++, everything the tests above would reject:
# only tree points, and ways / relations / areas carrying at least one of these keys, reach
# Python, where wanted_way / wanted_area_relation still decide. Node locations are stored BEFORE
# the filters (apply_file puts the location handler first), so every way keeps its geometry.
# EVERY key wanted_way / wanted_area_relation look at must be listed here (test_osm_extract).
PREFILTER_KEYS = ("highway", "building", "natural", "landuse", "waterway", "aeroway", "railway",
                  "power", "leisure", "amenity")
PREFILTER = True   # tests compare with and without
_ALL_NODES = osmium.geom.use_nodes.ALL   # keep repeated nodes: the same points as the per-node path


def _prefilters() -> list:
    if not PREFILTER:
        return []
    from osmium import filter as f
    from osmium.osm import AREA, NODE, RELATION, WAY
    return [f.TagFilter(("natural", "tree")).enable_for(NODE),
            f.KeyFilter(*PREFILTER_KEYS).enable_for(WAY | RELATION | AREA)]


class _TileWriter:
    """Streams elements into one tile's osm.json (written to .part, renamed on close).
    With several source files, elements already written (same type and id) are skipped."""

    _TYPE = {"node": 0, "way": 1, "relation": 2}

    def __init__(self, tile: Tile, sources: list[str] | None = None):
        self.out = tile.cache / "osm.json"
        self.part = self.out.with_suffix(".part")
        self.fh = self.part.open("w", encoding="utf-8")
        if sources and len(sources) > 1:
            self.fh.write(f'{{"version": {VERSION}, "sources": {json.dumps(sources)}, "elements": [\n')
            self.seen: set[int] | None = set()
        else:   # one file: the same header as before multi-file tiles existed
            self.fh.write(f'{{"version": {VERSION}, "elements": [\n')
            self.seen = None
        self.first = True
        self.count = 0

    def add(self, el: dict) -> None:
        if self.seen is not None:
            key = el["id"] * 3 + self._TYPE[el["type"]]
            if key in self.seen:
                return
            self.seen.add(key)
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
        self._wkb = osmium.geom.WKBFactory()

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

    def _box_targets(self, x0: float, y0: float, x1: float, y1: float) -> list[_TileWriter]:
        out = []
        for la in range(math.floor(y0), math.floor(y1) + 1):
            for lo in range(math.floor(x0), math.floor(x1) + 1):
                w = self.writers.get((la, lo))
                if w is not None:
                    out.append(w)
        return out

    def way(self, wy):
        if not wanted_way(wy.tags):
            return
        # Speed (same result): osmium builds the line's coordinates in C++ (WKB, every node kept)
        # and numpy reads them in one go, instead of one Python object per node (~30 % of a pass).
        # Lines with a missing location or < 2 points raise and take the exact per-node path.
        try:
            wkb = self._wkb.create_linestring(wy, _ALL_NODES)
        except Exception:  # noqa: BLE001 - osmium's geometry errors
            return self._way_slow(wy)
        xy = np.frombuffer(bytes.fromhex(wkb), "<f8", offset=9).reshape(-1, 2)
        (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
        targets = self._box_targets(float(x0), float(y0), float(x1), float(y1))
        if not targets:
            return
        el = {"type": "way", "id": wy.id, "tags": dict(wy.tags),
              "geometry": xy.tolist()}
        for w in targets:
            w.add(el)

    def _way_slow(self, wy):
        pts = [(n.lon, n.lat) for n in wy.nodes if n.location.valid()]
        if len(pts) < 2:
            return
        targets = self._targets(pts)
        if not targets:
            return
        el = {"type": "way", "id": wy.id, "tags": dict(wy.tags),
              "geometry": pts}
        for w in targets:
            w.add(el)

    def area(self, a):
        if a.from_way() or not wanted_area_relation(a.tags):
            return
        members, pts_all = [], []
        for ring in a.outer_rings():
            pts = [(n.lon, n.lat) for n in ring if n.location.valid()]
            if len(pts) >= 4:
                members.append({"role": "outer", "geometry": pts})
                pts_all += pts
        if not members:
            return
        targets = self._targets(pts_all)
        if not targets:
            return
        el = {"type": "relation", "id": a.orig_id(), "tags": dict(a.tags), "members": members}
        for w in targets:
            w.add(el)


def header_sources(tile: Tile) -> list[str] | None:
    """The extract files named in the tile's osm.json header (None: one file, the older header)."""
    out = tile.cache_path / "osm.json"
    with out.open("r", encoding="utf-8") as fh:
        head = fh.read(4096)
    m = re.search(r'"sources": (\[[^\]]*\])', head.split('"elements"', 1)[0])
    return json.loads(m.group(1)) if m else None


LEAN_FROM = 8   # the first VERSION with [lon, lat] pairs


def _lean(geom: list) -> list:
    return [[p["lon"], p["lat"]] for p in geom if p]


def _upgrade_lean(out: Path) -> bool:
    """A version-7 osm.json (same features, {"lat", "lon"} points) → the current layout, in place.
    Much faster than reading the country file again, and the file keeps its date, so built tiles
    don't turn outdated over it and the Data view still shows when the data was read.
    False when not possible (a later VERSION changed the features: the file must be read again)."""
    if VERSION != LEAN_FROM:
        return False
    st = out.stat()
    data = json.loads(out.read_text(encoding="utf-8"))
    for el in data.get("elements", []):
        if "geometry" in el:
            el["geometry"] = _lean(el["geometry"])
        for m in el.get("members", []):
            if "geometry" in m:
                m["geometry"] = _lean(m["geometry"])
    part = out.with_suffix(".part")
    with part.open("w", encoding="utf-8") as fh:   # the same text _TileWriter writes
        src = data.get("sources")
        fh.write(f'{{"version": {VERSION}, "sources": {json.dumps(src)}, "elements": [\n' if src
                 else f'{{"version": {VERSION}, "elements": [\n')
        fh.write(",\n".join(json.dumps(el, separators=(",", ":")) for el in data.get("elements", [])))
        fh.write("\n]}\n")
    os.utime(part, ns=(st.st_atime_ns, st.st_mtime_ns))
    part.replace(out)
    return True


def is_current(tile: Tile, sources: list[Path] | None = None) -> bool:
    """osm.json exists, has the current format and (when `sources` is given) was read from
    exactly these extract files."""
    out = tile.cache_path / "osm.json"
    if not out.exists():
        return False
    with out.open("r", encoding="utf-8") as fh:
        head = fh.read(64)
    if f'"version": {LEAN_FROM - 1},' in head and _upgrade_lean(out):
        return is_current(tile, sources)
    if f'"version": {VERSION}' not in head:
        return False
    if sources is None:
        return True
    have = header_sources(tile)
    want = sorted(p.name for p in sources)
    return (have is None and len(want) <= 1) or have == want


def _sources_of(tile: Tile, pbf, region: str | None) -> list[Path]:
    if pbf is None:
        from forest import regions
        return regions.osm_sources(tile, region)
    return sorted(pbf, key=lambda p: p.name) if isinstance(pbf, (list, tuple)) else [pbf]


def extract_tiles(tiles: list[Tile], pbf: Path | list[Path] | None = None,
                  force: bool = False, region: str | None = None) -> dict[str, int]:
    """Write osm.json for every tile that needs it. `pbf`: the extract file(s) to read; None =
    every downloaded extract covering each tile (regions.osm_sources; `region`'s own file is
    always included when downloaded). One pass per file serves
    all tiles that use it. Returns element counts per tile."""
    plan = {t.name: (t, _sources_of(t, pbf, region)) for t in tiles}
    todo = {n: (t, src) for n, (t, src) in plan.items() if src and (force or not is_current(t, src))}
    if not todo:
        return {}
    files = sorted({p for _, src in todo.values() for p in src}, key=lambda p: p.name)
    for f in files:
        if not f.exists():
            raise FileNotFoundError(f"OSM extract not found: {f}")
    writers = {(t.lat, t.lon): _TileWriter(t, [p.name for p in src]) for t, src in todo.values()}
    try:
        for f in files:
            use = {(t.lat, t.lon): writers[(t.lat, t.lon)] for t, src in todo.values() if f in src}
            from forest import events
            events.emit("osm_reading", file=f.name, mb=round(f.stat().st_size / 1e6), n=len(use),
                        tiles=sorted(t.name for t, src in todo.values() if f in src))
            _Handler(use).apply_file(str(f), locations=True, idx="flex_mem", filters=_prefilters())
    except BaseException:
        for w in writers.values():
            w.fh.close()
            w.part.unlink(missing_ok=True)
        raise
    counts = {}
    for t, _ in todo.values():
        w = writers[(t.lat, t.lon)]
        w.close()
        counts[t.name] = w.count
    return counts


def extract_tile(tile: Tile, pbf: Path | list[Path] | None = None) -> Path:
    extract_tiles([tile], pbf)
    return tile.cache / "osm.json"
