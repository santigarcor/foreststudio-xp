"""Elevation for a tile, taken from X-Plane's own base mesh (no download needed).

DSFTool writes the DSF raster layers as raw files next to the decoded text.
The elevation layer is int16, (N)x(N) posts, rows south → north.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from forest import config
from forest.tiles import Tile


def base_mesh_dsf(tile: Tile) -> Path:
    cfg = config.load()
    return (
        cfg.xplane_root / "Global Scenery" / "X-Plane 12 Global Scenery"
        / "Earth nav data" / tile.folder / f"{tile.name}.dsf"
    )


def fetch_elevation(tile: Tile) -> Path:
    out = tile.cache / "elevation_int16.npy"
    if out.exists():
        return out
    cfg = config.load()
    txt = tile.cache / "base_mesh.txt"
    from forest import proc

    r = proc.run([str(cfg.dsftool), "--dsf2text", str(base_mesh_dsf(tile)), str(txt)])  # hidden
    if r.returncode != 0:
        raise RuntimeError(f"DSFTool could not read the base mesh for {tile.name}: {r.stderr.strip()[-300:]}")
    raw = Path(str(txt) + ".elevation.raw")
    data = np.fromfile(raw, dtype="<i2")
    n = int(round(len(data) ** 0.5))
    elev = data.reshape(n, n)[::-1]  # → north-up
    np.save(out, elev)
    # The decoded text and other rasters are large and not needed.
    for p in tile.cache.glob("base_mesh.txt*"):
        p.unlink()
    return out


def elevation_on_grid(tile: Tile, grid) -> np.ndarray:
    from scipy.ndimage import zoom

    elev = np.load(fetch_elevation(tile)).astype(np.float32)
    # Posts are on cell corners; zoom to the grid size (approximation, fine for bands).
    return zoom(elev, grid.cells / elev.shape[0], order=1)[: grid.cells, : grid.cells]
