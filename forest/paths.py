"""Where build results live.

out/forest_studio_xp/                ← THE X-Plane pack (one folder for every region's tiles,
    Earth nav data/<10° folder>/<tile>.dsf       linked into Custom Scenery; only what X-Plane needs)
out/studio/<tile>.<ext>              ← the app's own files: overlay/preview PNG, stats, DSF text

Before 2026-11-23 every region had its own pack (out/forest_<region>, out/studio_<region>);
migrate.run_packs() merges those into these folders.
"""

from __future__ import annotations

from pathlib import Path

from forest import config
from forest.tiles import Tile

PACK_NAME = "forest_studio_xp"


def pack_root() -> Path:
    return config.OUT_ROOT / PACK_NAME


def dsf_path(tile: Tile) -> Path:
    return pack_root() / "Earth nav data" / tile.folder / f"{tile.name}.dsf"


def studio_dir() -> Path:
    return config.OUT_ROOT / "studio"


def studio_path(tile: Tile, suffix: str) -> Path:
    """suffix like '.stats.json', '.overlay.png', '.preview.png', '.txt'"""
    return studio_dir() / f"{tile.name}{suffix}"


def studio_files(tile: Tile) -> list[Path]:
    d = studio_dir()
    return [p for p in d.glob(f"{tile.name}.*") if p.is_file()] if d.exists() else []


def legacy_packs() -> list[Path]:
    """Old per-region pack folders (out/forest_<region>) still in the data folder."""
    out = config.OUT_ROOT
    return sorted(p for p in out.glob("forest_*") if p.is_dir() and p.name != PACK_NAME
                  and p.name != "forest_poc") if out.exists() else []
