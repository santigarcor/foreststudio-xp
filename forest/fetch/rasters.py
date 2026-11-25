"""Fetch only the 1° window of remote global rasters (HTTP range reads) into cache.

Sources (open data, no login):
- ESA WorldCover 2021 v200, 10 m, 3°×3° COG tiles (tiled → only the window is fetched).
- Hansen Global Forest Change v1.12: treecover2000 (%), lossyear (1-24 = 2001-2024).
  10°×10° strip-organised GeoTIFFs → the 1° rows are fetched (≈1/10 of the file).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from forest.tiles import Tile

WORLDCOVER = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{ns}{lat:02d}{ew}{lon:03d}_Map.tif"
)
HANSEN = (
    "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/"
    "Hansen_GFC-2024-v1.12_{layer}_{lat:02d}{ns}_{lon:03d}{ew}.tif"
)

# WorldCover classes
WC_TREE, WC_SHRUB, WC_GRASS, WC_CROP, WC_BUILT, WC_BARE, WC_SNOW, WC_WATER, WC_WETLAND, WC_MANGROVE = (
    10, 20, 30, 40, 50, 60, 70, 80, 90, 95,
)


def worldcover_url(tile: Tile) -> str:
    lat3 = math.floor(tile.lat / 3) * 3  # tiles are named by their SW corner, 3° grid
    lon3 = math.floor(tile.lon / 3) * 3
    return WORLDCOVER.format(
        ns="N" if lat3 >= 0 else "S", lat=abs(lat3), ew="E" if lon3 >= 0 else "W", lon=abs(lon3)
    )


def hansen_url(tile: Tile, layer: str) -> str:
    lat10 = math.ceil((tile.lat + 1) / 10) * 10  # named by NW corner, 10° grid
    lon10 = math.floor(tile.lon / 10) * 10
    return HANSEN.format(
        layer=layer,
        lat=abs(lat10), ns="N" if lat10 >= 0 else "S",
        lon=abs(lon10), ew="E" if lon10 >= 0 else "W",
    )


def fetch_window(url: str, tile: Tile, out: Path) -> Path:
    """Read the tile window from a remote raster and save it as a local GeoTIFF."""
    if out.exists():
        return out
    w, s, e, n = tile.bounds
    # Retries: one dropped request (network blip, server 5xx) must not fail the whole tile.
    with rasterio.Env(GDAL_HTTP_MULTIRANGE="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_HTTP_MAX_RETRY=4, GDAL_HTTP_RETRY_DELAY=5):
        with rasterio.open("/vsicurl/" + url) as src:
            win = from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths()
            data = src.read(1, window=win)
            prof = src.profile.copy()
            prof.update(
                width=data.shape[1], height=data.shape[0],
                transform=src.window_transform(win),
                tiled=True, blockxsize=512, blockysize=512, compress="deflate",
            )
    tmp = out.with_suffix(".part.tif")
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(data, 1)
    tmp.replace(out)
    return out


def fetch_all(tile: Tile) -> dict[str, Path]:
    c = tile.cache
    return {
        "worldcover": fetch_window(worldcover_url(tile), tile, c / "worldcover.tif"),
        "treecover2000": fetch_window(hansen_url(tile, "treecover2000"), tile, c / "treecover2000.tif"),
        "lossyear": fetch_window(hansen_url(tile, "lossyear"), tile, c / "lossyear.tif"),
    }


def read_on_grid(path: Path, grid, resampling) -> np.ndarray:
    """Read a cached raster resampled onto the working grid."""
    from rasterio.warp import reproject

    out = np.zeros(grid.shape, dtype=np.float32)
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1), destination=out,
            dst_transform=grid.transform, dst_crs="EPSG:4326", resampling=resampling,
        )
    return out
