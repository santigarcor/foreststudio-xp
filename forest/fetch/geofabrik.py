"""Geofabrik's list of OpenStreetMap downloads (continents → countries → states), used to add
regions: https://download.geofabrik.de/index-v1.json (≈3.4 MB, every extract with its outline).

The index is downloaded on request (Data view → Regions) into <data>/cache/osm/ and read from
there; nothing else is sent to Geofabrik. Outlines tell which 1° tiles an extract covers.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from forest import config

INDEX_URL = "https://download.geofabrik.de/index-v1.json"


def index_path() -> Path:
    return config.CACHE_ROOT / "osm" / "geofabrik-index.json"


def download_index() -> Path:
    from forest.fetch import sources

    p = index_path()
    sources.download(INDEX_URL, p)
    _load.cache_clear()
    return p


def has_index() -> bool:
    return index_path().exists()


@lru_cache(maxsize=1)
def _load(path: str, mtime_ns: int) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for f in data.get("features", []):
        p = f.get("properties", {})
        pbf = (p.get("urls") or {}).get("pbf")
        if not p.get("id") or not pbf or not f.get("geometry"):
            continue
        out[p["id"]] = {"id": p["id"], "name": p.get("name") or p["id"], "parent": p.get("parent"),
                        "pbf": pbf, "geometry": f["geometry"]}
    return out


def entries() -> dict[str, dict]:
    """id → {id, name, parent, pbf, geometry}; {} when the index isn't downloaded."""
    p = index_path()
    if not p.exists():
        return {}
    return _load(str(p), p.stat().st_mtime_ns)


def tree() -> list[dict]:
    """Every extract with its depth, in reading order (continent, its countries, their states)."""
    es = entries()
    kids: dict[str | None, list[dict]] = {}
    for e in es.values():
        parent = e["parent"] if e["parent"] in es else None
        kids.setdefault(parent, []).append(e)
    out: list[dict] = []

    def walk(parent, depth, top):
        for e in sorted(kids.get(parent, []), key=lambda x: x["name"]):
            t = top or e["id"]
            out.append({"id": e["id"], "name": e["name"], "parent": e["parent"], "depth": depth,
                        "continent": t, "children": len(kids.get(e["id"], [])), "pbf": e["pbf"]})
            walk(e["id"], depth + 1, t)

    walk(None, 0, None)
    return out


def by_file_name() -> dict[str, dict]:
    """The extract for a downloaded file name (e.g. colombia-latest.osm.pbf)."""
    return {Path(e["pbf"]).name: e for e in entries().values()}


@lru_cache(maxsize=64)
def _shares(eid: str, index: str, mtime_ns: int) -> dict[str, float]:
    from shapely.geometry import shape

    from forest.fetch.osm_tiles import shares_of

    g = shape(entries()[eid]["geometry"])
    if not g.is_valid:
        g = g.buffer(0)
    return shares_of(g)


def tile_shares(eid: str) -> dict[str, float]:
    """1° tiles the extract's outline touches → how much of the tile it covers (0–1)."""
    p = index_path()
    if eid not in entries():
        return {}
    return _shares(eid, str(p), p.stat().st_mtime_ns)


def tiles_of(eid: str) -> frozenset[str]:
    """Names of the 1° tiles the extract's outline touches."""
    return frozenset(tile_shares(eid))


# ---------- regions made from an extract ----------

TROPICS = 23.5   # regions whose middle lies between the tropics are evergreen-only (rule 3)


def region_name(eid: str) -> str:
    """A region file name for an extract id (ids are already lowercase, e.g. "panama")."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in eid.lower())[:40]


def region_toml(eid: str) -> str:
    """The region file for an extract: its tiles, its OSM download, seasonal or not."""
    e = entries().get(eid)
    if e is None:
        raise ValueError(f"unknown Geofabrik extract {eid!r} (download the list first)")
    from shapely.geometry import shape

    from forest.tiles import Tile

    tiles = sorted(tiles_of(eid))
    if not tiles:
        raise ValueError(f"{e['name']}: the extract covers no land tiles")
    ts = [Tile.parse(t) for t in tiles]
    w, s = min(t.lon for t in ts), min(t.lat for t in ts)
    east, n = max(t.lon for t in ts) + 1, max(t.lat for t in ts) + 1
    mid = shape(e["geometry"]).centroid.y
    evergreen = abs(mid) < TROPICS
    q = lambda x: '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'   # noqa: E731
    lines = [
        f"# Region added from Geofabrik's list ({e['name']}, id {eid}).",
        "# Delete it in the Data view (Regions). Forest types: config/rules/<this name>.toml.",
        f"label = {q(e['name'])}",
        f"bounds = [{w}, {s}, {east}, {n}]",
        f"osm_url = {q(e['pbf'])}",
        f"# {'Between the tropics: evergreen forest types only' if evergreen else 'Seasonal forest types allowed'}"
        " (XP12's season maps can show bare trees in tropical highlands).",
        f"evergreen_only = {'true' if evergreen else 'false'}",
        f"geofabrik_id = {q(eid)}",
        "# The 1° tiles the extract's outline touches (the map offers only these).",
        "tiles = [",
    ]
    for i in range(0, len(tiles), 8):
        lines.append("  " + ", ".join(q(t) for t in tiles[i:i + 8]) + ",")
    lines.append("]")
    return "\n".join(lines) + "\n"


def sizes(urls: list[str], workers: int = 8) -> dict[str, int | None]:
    """Download size of each file (HEAD requests, in parallel); None when unknown."""
    from concurrent.futures import ThreadPoolExecutor

    from forest.fetch import sources

    def one(u):
        try:
            return u, sources.remote_info(u, timeout=20).get("remote_size")
        except OSError:
            return u, None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, urls))
