"""Read OSM features for one or many tiles from a local Geofabrik .osm.pbf extract.

One pass over the file serves any number of tiles: every element is streamed into each
requested tile it touches, so memory stays low even for a whole country. A tile on a border is
filled from every downloaded extract that covers it (one pass per file, elements found in two
files are written once); the files used are listed in the header ("sources").

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
VERSION = 6  # 5: mangroves (natural=wetland + wetland=mangrove); 6: every natural=wetland (swamps, marshes…). Bump when the set of extracted features changes (old caches are re-extracted)

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
        or is_wetland_area(t)
    )


def wanted_area_relation(t) -> bool:
    return ("building" in t or t.get("natural") == "water" or t.get("aeroway") == "aerodrome"
            or is_green_area(t) or is_plantation(t) or is_town_area(t) or is_scrub_area(t)
            or is_wetland_area(t))


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


def header_sources(tile: Tile) -> list[str] | None:
    """The extract files named in the tile's osm.json header (None: one file, the older header)."""
    out = tile.cache_path / "osm.json"
    with out.open("r", encoding="utf-8") as fh:
        head = fh.read(4096)
    m = re.search(r'"sources": (\[[^\]]*\])', head.split('"elements"', 1)[0])
    return json.loads(m.group(1)) if m else None


def is_current(tile: Tile, sources: list[Path] | None = None) -> bool:
    """osm.json exists, has the current format and (when `sources` is given) was read from
    exactly these extract files."""
    out = tile.cache_path / "osm.json"
    if not out.exists():
        return False
    with out.open("r", encoding="utf-8") as fh:
        head = fh.read(64)
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
            print(f"reading OpenStreetMap: {f.name} ({f.stat().st_size / 1e6:.0f} MB) "
                  f"for {len(use)} tile(s)", flush=True)
            _Handler(use).apply_file(str(f), locations=True, idx="flex_mem")
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
