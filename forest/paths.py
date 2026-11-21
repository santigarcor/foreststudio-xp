"""Where build results live.

out/forest_<region>/                 ← the X-Plane pack (linked into Custom Scenery as one folder)
    Earth nav data/<10° folder>/<tile>.dsf       only what X-Plane needs
out/studio_<region>/<tile>.<ext>     ← the app's own files: overlay/preview PNG, stats, DSF text
"""

from __future__ import annotations

from pathlib import Path

from forest.config import REPO_ROOT
from forest.tiles import Tile


def pack_name(region: str) -> str:
    return f"forest_{region}"


def pack_root(region: str) -> Path:
    return REPO_ROOT / "out" / pack_name(region)


def dsf_path(region: str, tile: Tile) -> Path:
    return pack_root(region) / "Earth nav data" / tile.folder / f"{tile.name}.dsf"


def studio_dir(region: str) -> Path:
    return REPO_ROOT / "out" / f"studio_{region}"


def studio_path(region: str, tile: Tile, suffix: str) -> Path:
    """suffix like '.stats.json', '.overlay.png', '.preview.png', '.txt'"""
    return studio_dir(region) / f"{tile.name}{suffix}"


def studio_files(region: str, tile: Tile) -> list[Path]:
    d = studio_dir(region)
    return [p for p in d.glob(f"{tile.name}.*") if p.is_file()] if d.exists() else []
