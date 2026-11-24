"""How much disk ForestStudio uses, by category, and safe clean-up actions.

Everything lives in the data folder (~/.foreststudio: cache/ and out/). The X-Plane side is only a
folder link to out/forest_studio_xp, so it takes no space of its own.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from forest import config
from forest.fsutil import size_bytes

CACHE = config.CACHE_ROOT
OUT = config.OUT_ROOT


def _mb(n: int) -> float:
    return round(n / 1e6, 1)


def _leftovers() -> list[Path]:
    """Files that are safe to delete: debug decodes, abandoned downloads, kept DSF text,
    unfinished compiles."""
    out: list[Path] = []
    for d in (CACHE / "decoded", CACHE / "canopy"):
        if d.exists():
            out.append(d)
    if (CACHE / "tiles").exists():
        out += [p for p in (CACHE / "tiles").glob("*/osm_parts") if p.is_dir()]
        out += list((CACHE / "tiles").glob("*/base_mesh.txt*"))
        out += list((CACHE / "tiles").glob("*/osm.part"))
    if OUT.exists():
        out += list(OUT.glob("studio/*.txt"))
        out += list(OUT.glob("studio/*.raw"))
        out += list(OUT.glob("forest_*/Earth nav data/*/*.building.dsf"))
    return out


def _unbuilt_tile_data() -> list[Path]:
    """Downloaded data of tiles that have no built .dsf in the pack."""
    built = {p.stem for p in OUT.glob("forest_*/Earth nav data/*/*.dsf")} if OUT.exists() else set()
    tiles = CACHE / "tiles"
    return [p for p in tiles.iterdir() if p.is_dir() and p.name not in built] if tiles.exists() else []


def usage() -> dict:
    tiles_dir = CACHE / "tiles"
    tile_dirs = [p for p in tiles_dir.iterdir() if p.is_dir()] if tiles_dir.exists() else []
    rasters = osm = elev = 0
    per_tile = []
    tile_bytes: dict[Path, int] = {}   # each tile folder is walked once
    for d in tile_dirs:
        r = sum(size_bytes(d / n) for n in ("worldcover.tif", "treecover2000.tif", "lossyear.tif"))
        o = size_bytes(d / "osm.json")
        e = size_bytes(d / "elevation_int16.npy")
        rasters, osm, elev = rasters + r, osm + o, elev + e
        tile_bytes[d] = size_bytes(d)
        per_tile.append({"tile": d.name, "mb": _mb(tile_bytes[d])})
    per_tile.sort(key=lambda x: -x["mb"])
    loose = sum(f.stat().st_size for f in tiles_dir.iterdir() if f.is_file()) if tiles_dir.exists() else 0
    left = _leftovers()
    left_mb = _mb(sum(size_bytes(p) for p in left))
    unbuilt = _unbuilt_tile_data()
    unbuilt_mb = _mb(sum(tile_bytes[p] if p in tile_bytes else size_bytes(p) for p in unbuilt))
    cats = [
        {"key": "tile_data", "label": "Downloaded tile data", "mb": _mb(sum(tile_bytes.values()) + loose),
         "detail": f"{len(tile_dirs)} tiles · satellite maps {_mb(rasters)} MB · OSM extracts {_mb(osm)} MB"
                   f" · elevation {_mb(elev)} MB",
         "path": str(tiles_dir)},
        {"key": "osm_country", "label": "OpenStreetMap country file", "mb": _mb(sum(size_bytes(p) for p in (CACHE / "osm").glob("*.pbf"))),
         "detail": "Geofabrik extract, read once per build job", "path": str(CACHE / "osm")},
        {"key": "ecoregions", "label": "Ecoregions map (biomes)", "mb": _mb(size_bytes(CACHE / "ecoregions")),
         "detail": "RESOLVE Ecoregions 2017 download + each region's cut-out", "path": str(CACHE / "ecoregions")},
        {"key": "built", "label": "Built tiles (the X-Plane pack)", "mb": _mb(sum(size_bytes(p) for p in OUT.glob("forest_*"))),
         "detail": f"{len(list(OUT.glob('forest_*/Earth nav data/*/*.dsf')))} .dsf tiles; X-Plane sees them through a link (no copy)",
         "path": str(OUT)},
        {"key": "studio", "label": "App files (overlays, previews, stats)", "mb": _mb(sum(size_bytes(p) for p in OUT.glob("studio*"))),
         "detail": "what the map overlay and Tile info show", "path": str(OUT)},
        {"key": "jobs", "label": "Job history and logs", "mb": _mb(size_bytes(CACHE / "jobs")),
         "detail": f"{len(list((CACHE / 'jobs').glob('*.json')))} saved jobs" if (CACHE / "jobs").exists() else "none",
         "path": str(CACHE / "jobs")},
        {"key": "leftovers", "label": "Leftovers (safe to delete)", "mb": left_mb,
         "detail": "debug decodes, abandoned downloads, kept DSF text, unfinished compiles",
         "path": str(CACHE)},
    ]
    total = sum(c["mb"] for c in cats if c["key"] != "leftovers") + next(c["mb"] for c in cats if c["key"] == "leftovers")
    try:
        du = shutil.disk_usage(config.DATA_ROOT)
        free, disk_total = round(du.free / 1e9, 1), round(du.total / 1e9, 1)
    except OSError:
        free = disk_total = None
    return {
        "total_mb": round(total, 1), "categories": cats, "largest_tiles": per_tile[:15],
        "free_gb": free, "disk_total_gb": disk_total, "project": str(config.DATA_ROOT),
        "cleanable": {
            "leftovers": {"items": len(left), "mb": left_mb},
            "unbuilt_tile_data": {"items": len(unbuilt), "mb": unbuilt_mb},
        },
    }


def clean(kind: str) -> dict:
    """Delete one cleanable category. Only ever touches cache/ and out/ inside the data folder."""
    if kind == "leftovers":
        targets = _leftovers()
    elif kind == "unbuilt_tile_data":
        targets = _unbuilt_tile_data()
    else:
        return {"ok": False, "error": f"unknown clean-up '{kind}'"}
    allowed = (CACHE.resolve(), OUT.resolve())
    freed = 0
    for p in targets:
        rp = p.parent.resolve() / p.name
        if not any(rp.is_relative_to(a) for a in allowed):
            return {"ok": False, "error": f"refusing to delete outside the data folder: {p}"}
        freed += size_bytes(p)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
    return {"ok": True, "deleted": len(targets), "freed_mb": _mb(freed)}
