"""OpenStreetMap features per tile: cut-outs (roads, buildings, water, airports, rail, power)
and tree hints (single trees, tree rows, woods, parks, plantations, town land use, scrub).

Fallback only: the local Geofabrik .pbf (osm_pbf.py) is preferred. Fetched once per tile from
the Overpass API (only the tile bbox is sent) and cached. Keep QUERY in step with
osm_pbf.wanted_way / wanted_area_relation.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from forest.tiles import Tile

OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "forest-studio/0.1 (X-Plane scenery generator)"

QUERY = """
[out:json][timeout:900][maxsize:1073741824];
(
  way["highway"]({s},{w},{n},{e});
  way["building"]({s},{w},{n},{e});
  relation["building"]({s},{w},{n},{e});
  way["natural"="water"]({s},{w},{n},{e});
  relation["natural"="water"]({s},{w},{n},{e});
  way["waterway"~"^(river|riverbank|canal)$"]({s},{w},{n},{e});
  way["landuse"="reservoir"]({s},{w},{n},{e});
  way["aeroway"~"^(aerodrome|runway|taxiway|apron|helipad)$"]({s},{w},{n},{e});
  relation["aeroway"="aerodrome"]({s},{w},{n},{e});
  way["railway"="rail"]({s},{w},{n},{e});
  way["power"="line"]({s},{w},{n},{e});
  node["natural"="tree"]({s},{w},{n},{e});
  way["natural"~"^(tree_row|wood|scrub|heath)$"]({s},{w},{n},{e});
  relation["natural"~"^(wood|scrub|heath)$"]({s},{w},{n},{e});
  way["natural"="wetland"]["wetland"="mangrove"]({s},{w},{n},{e});
  relation["natural"="wetland"]["wetland"="mangrove"]({s},{w},{n},{e});
  way["landuse"~"^(forest|orchard|plantation|vineyard|residential|industrial|commercial|retail)$"]({s},{w},{n},{e});
  relation["landuse"~"^(forest|orchard|plantation|vineyard|residential|industrial|commercial|retail)$"]({s},{w},{n},{e});
  way["landuse"="farmland"]["crop"]({s},{w},{n},{e});
  way["landuse"="farmland"]["trees"]({s},{w},{n},{e});
  way["landuse"="farmland"]["produce"]({s},{w},{n},{e});
  relation["landuse"="farmland"]["crop"]({s},{w},{n},{e});
  relation["landuse"="farmland"]["trees"]({s},{w},{n},{e});
  relation["landuse"="farmland"]["produce"]({s},{w},{n},{e});
  way["leisure"~"^(park|golf_course|garden|nature_reserve)$"]({s},{w},{n},{e});
  relation["leisure"~"^(park|golf_course|garden|nature_reserve)$"]({s},{w},{n},{e});
);
out geom qt;
"""


def _fetch_adaptive(s: float, w: float, n: float, e: float, out: Path, depth: int = 0) -> list[Path]:
    """Fetch a box; if the server keeps failing, split it into 4 and recurse (max 2 levels)."""
    if out.exists():
        return [out]
    split_dir = out.with_suffix("")
    if not split_dir.is_dir():  # not split on a previous run: try the whole box first
        try:
            _fetch_bbox(s, w, n, e, out, retries=2 if depth < 2 else 4)
            return [out]
        except RuntimeError:
            if depth >= 2:
                raise
    split_dir.mkdir(exist_ok=True)
    ms, mw = (s + n) / 2, (w + e) / 2
    got: list[Path] = []
    for k, (a, b, c, d) in enumerate([(s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)]):
        got += _fetch_adaptive(a, b, c, d, split_dir / f"{k}.json", depth + 1)
        time.sleep(2)
    return got


def _fetch_bbox(s: float, w: float, n: float, e: float, out: Path, retries: int = 4) -> None:
    if out.exists():
        return
    body = urllib.parse.urlencode({"data": QUERY.format(s=s, w=w, n=n, e=e)}).encode()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=1200) as resp:
                data = resp.read()
            json.loads(data)  # validate
            tmp = out.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(out)
            return
        except Exception as exc:  # noqa: BLE001 - retry on any network/server error
            last = exc
            print(f"  overpass {out.name}: {exc}; retry in {30 * (attempt + 1)} s", flush=True)
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"Overpass fetch failed for {out.name}: {last}")


def fetch_osm(tile: Tile, region: str) -> Path:
    """Use the region's local Geofabrik extract when present (preferred), else Overpass."""
    from forest import regions
    from forest.fetch.osm_pbf import extract_tile

    pbf = regions.get(region).osm_file
    if pbf.exists():
        return extract_tile(tile, pbf)
    return fetch_overpass(tile)


def fetch_overpass(tile: Tile, splits: int = 4) -> Path:
    """Fetch in splits×splits sub-boxes (one whole degree is too heavy for Overpass),
    cache each piece, then merge into osm.json (deduplicated by element id)."""
    out = tile.cache / "osm.json"
    if out.exists():
        return out
    parts = tile.cache / "osm_parts"
    parts.mkdir(exist_ok=True)
    step = 1.0 / splits
    seen: set[tuple[str, int]] = set()
    elements: list[dict] = []
    for i in range(splits):
        for j in range(splits):
            s = tile.lat + i * step
            w = tile.lon + j * step
            files = _fetch_adaptive(s, w, s + step, w + step, parts / f"{i}_{j}.json")
            mb = sum(f.stat().st_size for f in files) / 1e6
            print(f"  osm piece {i}_{j}: {len(files)} file(s), {mb:.1f} MB", flush=True)
            for f in files:
                for el in json.loads(f.read_text(encoding="utf-8")).get("elements", []):
                    key = (el["type"], el["id"])
                    if key not in seen:
                        seen.add(key)
                        elements.append(el)
            time.sleep(2)  # be polite to the public server
    tmp = out.with_suffix(".part")
    tmp.write_text(json.dumps({"elements": elements}), encoding="utf-8")
    tmp.replace(out)
    return out
