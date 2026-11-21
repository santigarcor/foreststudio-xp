"""1°×1° X-Plane tiles and the working raster grid."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from forest.config import REPO_ROOT

CACHE = REPO_ROOT / "cache" / "tiles"


@dataclass(frozen=True)
class Tile:
    lat: int  # south edge
    lon: int  # west edge

    @classmethod
    def parse(cls, name: str) -> "Tile":
        m = re.fullmatch(r"([+-]\d{2})([+-]\d{3})", name.strip())
        if not m:
            raise ValueError(f"tile must look like +06-076, got {name!r}")
        return cls(int(m.group(1)), int(m.group(2)))

    @property
    def name(self) -> str:
        return f"{self.lat:+03d}{self.lon:+04d}"

    @property
    def folder(self) -> str:
        return f"{self.lat // 10 * 10:+03d}{self.lon // 10 * 10:+04d}"

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """west, south, east, north"""
        return (self.lon, self.lat, self.lon + 1, self.lat + 1)

    @property
    def cache(self) -> Path:
        """Cache folder for writing (created if missing)."""
        p = CACHE / self.name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_path(self) -> Path:
        """Cache folder for reading/checking (never created)."""
        return CACHE / self.name


@dataclass(frozen=True)
class Grid:
    """North-up raster grid covering a tile. Row 0 = north edge."""

    tile: Tile
    cells: int  # cells per degree (same in x and y)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.cells, self.cells)

    @property
    def res(self) -> float:
        return 1.0 / self.cells

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.tile.lon, self.tile.lat + 1, self.res, self.res)
