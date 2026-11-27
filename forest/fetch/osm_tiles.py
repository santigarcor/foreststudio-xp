"""Which 1° tiles a downloaded OpenStreetMap file covers: `<file>.tiles.json` next to the .pbf,
{"version": 1, "source": ..., "tiles": {"+06-076": share 0–1, …}}.

Written when the file is downloaded or updated (and on start for files downloaded before this
existed: `ensure_all`). The outline is the extract's own Geofabrik .poly (the polygon Geofabrik
cut the data with, a few KB next to the .pbf), or the same outline from Geofabrik's index when it
is already here. The map only enables tiles listed in one of these files, so a tile in a region's
box but outside its data (Panama in Colombia's box, MPTO 2026-11-27) can't be selected or built.

The list stays when the .pbf is deleted to save space (the tiles' osm.json still work); it goes
with the region (regions.remove).
"""

from __future__ import annotations

import json
import math
import urllib.request
from functools import lru_cache
from pathlib import Path

VERSION = 1


def db_path(pbf: Path) -> Path:
    return pbf.with_name(pbf.name + ".tiles.json")


def poly_url(osm_url: str) -> str | None:
    """Geofabrik keeps each extract's outline next to it: …/colombia-latest.osm.pbf → …/colombia.poly."""
    if osm_url.endswith("-latest.osm.pbf"):
        return osm_url[: -len("-latest.osm.pbf")] + ".poly"
    return None


def parse_poly(text: str):
    """Osmosis polygon file → shapely geometry. Sections are rings; a name starting with '!' is a
    hole in the ring before it."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union

    lines = [x.strip() for x in text.splitlines()]
    i, shells, holes = 1, [], []            # line 0 is the file's name
    while i < len(lines):
        name = lines[i]
        i += 1
        if not name or name == "END":
            continue
        ring = []
        while i < len(lines) and lines[i] != "END":
            parts = lines[i].split()
            if len(parts) >= 2:
                ring.append((float(parts[0]), float(parts[1])))
            i += 1
        i += 1
        if len(ring) >= 3:
            (holes if name.startswith("!") else shells).append(Polygon(ring))
    if not shells:
        raise ValueError("polygon file has no rings")
    g = unary_union(shells)
    if holes:
        g = g.difference(unary_union(holes))
    if not g.is_valid:
        g = g.buffer(0)
    return g if isinstance(g, (Polygon, MultiPolygon)) else unary_union([g])


def shares_of(g) -> dict[str, float]:
    """1° tiles the geometry touches → how much of each it covers (square degrees, 0–1)."""
    from shapely.geometry import box
    from shapely.prepared import prep

    from forest.tiles import Tile

    pg = prep(g)
    w, s, e, n = g.bounds
    out = {}
    for lat in range(math.floor(s), math.ceil(n)):
        for lon in range(math.floor(w), math.ceil(e)):
            b = box(lon, lat, lon + 1, lat + 1)
            if -90 <= lat < 90 and -180 <= lon < 180 and pg.intersects(b):
                share = round(g.intersection(b).area, 6)
                if share > 0:
                    out[Tile(lat, lon).name] = share
    return out


def write_tiles(pbf: Path, tiles: dict[str, float], source: str) -> Path:
    p = db_path(pbf)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"version": VERSION, "source": source, "tiles": dict(sorted(tiles.items()))}),
                   encoding="utf-8")
    tmp.replace(p)
    return p


def write(pbf: Path, osm_url: str, timeout: float = 30) -> Path:
    """Make the file's tile list from its outline: Geofabrik's index if it's here, else the .poly
    (network). Raises OSError / ValueError when neither works."""
    from forest.fetch import geofabrik

    e = geofabrik.by_file_name().get(pbf.name)
    if e:
        return write_tiles(pbf, geofabrik.tile_shares(e["id"]), "geofabrik-index")   # shares > 0 only
    url = poly_url(osm_url)
    if not url:
        raise ValueError(f"{pbf.name}: not a Geofabrik download, its outline is unknown")
    from forest.fetch.sources import USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", "replace")
    return write_tiles(pbf, shares_of(parse_poly(text)), url)


@lru_cache(maxsize=32)
def _read(path: str, mtime_ns: int, size: int) -> dict[str, float] | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if d.get("version") != VERSION or not isinstance(d.get("tiles"), dict):
        return None
    return {str(k): float(v) for k, v in d["tiles"].items()}


def read(pbf: Path) -> dict[str, float] | None:
    """The file's tiles → share, or None when there is no (valid) list yet."""
    p = db_path(pbf)
    try:
        st = p.stat()
    except OSError:
        return None
    return _read(str(p), st.st_mtime_ns, st.st_size)


def ensure_all() -> list[str]:
    """On start: a tile list for every downloaded OSM file that has none (files downloaded by
    older versions). Offline → tried again next start. Returns notes for the log."""
    from forest import regions

    notes, seen = [], set()
    for n in regions.names():
        try:
            r = regions.get(n)
        except ValueError:
            continue
        pbf = r.osm_file
        if pbf.name in seen or not pbf.exists() or read(pbf) is not None:
            continue
        seen.add(pbf.name)
        try:
            write(pbf, r.osm_url, timeout=10)   # don't hold the app's start long when offline
            notes.append(f"listed the tiles {pbf.name} covers")
        except (OSError, ValueError) as exc:
            notes.append(f"could not list the tiles {pbf.name} covers yet ({exc})")
    return notes
