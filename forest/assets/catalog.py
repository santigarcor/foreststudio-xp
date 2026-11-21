"""Build a catalog of X-Plane 12 default forest assets usable from a DSF.

For every virtual path in `1200 forests/library.txt` that points at a .for, record
which seasons it has, its visibility, tree spacing (→ max trees/km²) and species.
"""

from __future__ import annotations

import json
from pathlib import Path

from forest import config as _config
from forest.xplane.forest import read_forest
from forest.xplane.library import parse_library

SEASONS = ("spr", "sum", "fal", "win")


def build_catalog(forests_dir: Path) -> dict:
    lib = parse_library(forests_dir / "library.txt")
    parsed: dict[str, dict] = {}  # real path -> info (shared between seasons)
    entries = []
    for virtual, exports in sorted(lib.by_virtual().items()):
        if not virtual.endswith(".for"):
            continue
        exports = [e for e in exports if e.kind in ("EXPORT", "EXPORT_SEASON")]
        if not exports:
            continue
        files: dict[str, str] = {}
        for e in exports:
            files[e.season or "all"] = e.real
        # Describe using the summer (or only) variant.
        ref = files.get("sum") or files.get("all") or next(iter(files.values()))
        if ref not in parsed:
            fi = read_forest(forests_dir / ref)
            parsed[ref] = {
                "spacing_m": fi.spacing,
                "random_m": fi.random,
                "lod_m": fi.lod,
                "max_trees_per_km2": round(fi.max_trees_per_km2) if fi.max_trees_per_km2 else None,
                "seasonal_texture": fi.seasonal_texture,
                "skip_surfaces": fi.skip_surfaces,
                "mesh_count": len(fi.meshes),
                "species": fi.species,
                "avg_tree_tris": round(fi.avg_tree_tris) if fi.avg_tree_tris else None,
                # Rough GPU load per km² of forest at full density: trees × triangles.
                "tris_per_km2_m": (round(fi.max_trees_per_km2 * fi.avg_tree_tris / 1e6, 1)
                                   if fi.max_trees_per_km2 and fi.avg_tree_tris else None),
            }
        entries.append(
            {
                "virtual": virtual,
                "visibility": exports[0].visibility,
                "regions": sorted({e.region for e in exports}),
                "seasons": sorted(k for k in files if k in SEASONS),
                "evergreen_export": "all" in files,
                "files": files,
                **parsed[ref],
            }
        )
    return {"source": str(forests_dir), "count": len(entries), "forests": entries}


DEFAULT_CATALOG = _config.USER_CONFIG / "catalog_xp12.json"   # generated per machine


def load_catalog(path: Path | None = None) -> dict:
    """The forest catalog. The default one is generated from the user's own X-Plane install
    the first time it is needed (it is not shipped in the repository)."""
    if path is None:
        path = DEFAULT_CATALOG
        if not path.exists():
            ensure_catalog()
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_catalog(force: bool = False) -> bool:
    """Build ~/.foreststudio/config/catalog_xp12.json from X-Plane's `1200 forests` library if missing.
    Returns False when X-Plane's location isn't set up yet (the app then asks for it)."""
    from forest import config

    if DEFAULT_CATALOG.exists() and not force:
        return True
    forests = config.load().default_forests
    if not (forests / "library.txt").exists():
        return False
    write_catalog(build_catalog(forests), DEFAULT_CATALOG)
    return True


def seasonal_assets(path: Path | None = None) -> list[str]:
    """Virtual paths that change art by season (avoid them in the tropics)."""
    return [f["virtual"] for f in load_catalog(path)["forests"] if f["seasons"]]


def write_catalog(catalog: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=1), encoding="utf-8")
