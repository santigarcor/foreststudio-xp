"""Regions: where ForestStudio builds, where the data comes from, and which rules apply.

One TOML file per region: the shipped ones in <code>/config/regions/, and the user's own in
<data>/config/regions/ (a file with the same name replaces the shipped one). The file name is
the region's name, which also names its forest types (config/rules/<name>.toml) and its X-Plane
pack (forest_<name>).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from forest import config
from forest.tiles import Tile


@dataclass(frozen=True)
class Region:
    name: str
    label: str
    bounds: tuple[int, int, int, int]        # west, south, east, north (tile edges, whole degrees)
    osm_url: str                              # Geofabrik extract covering the bounds
    evergreen_only: bool = True               # refuse forest types with seasonal variants
    ecoregion_bounds: tuple[float, float, float, float] | None = None   # default: bounds + 1°

    def tiles(self) -> list[Tile]:
        w, s, e, n = self.bounds
        return [Tile(lat, lon) for lat in range(s, n) for lon in range(w, e)]

    def contains(self, tile: Tile) -> bool:
        w, s, e, n = self.bounds
        return w <= tile.lon < e and s <= tile.lat < n

    @property
    def clip_bounds(self) -> tuple[float, float, float, float]:
        if self.ecoregion_bounds:
            return self.ecoregion_bounds
        w, s, e, n = self.bounds
        return (w - 1, s - 1, e + 1, n + 1)

    @property
    def osm_file(self) -> Path:
        from forest.fetch import osm_pbf

        return osm_pbf.PBF_DIR / Path(self.osm_url).name

    def info(self) -> dict:
        """What the app needs to know about the region."""
        return {"name": self.name, "label": self.label, "bounds": list(self.bounds),
                "evergreen_only": self.evergreen_only}


def _dirs() -> list[Path]:
    return [config.DEFAULTS_DIR / "regions", config.USER_CONFIG / "regions"]   # later wins


def _files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for d in _dirs():
        for f in sorted(d.glob("*.toml")) if d.is_dir() else []:
            out[f.stem] = f
    return out


def names() -> list[str]:
    return sorted(_files())


def default_name() -> str:
    """The region the app opens with: Colombia if present, else the first one."""
    n = names()
    if not n:
        raise ValueError("no regions found (config/regions/*.toml)")
    return "colombia" if "colombia" in n else n[0]


def _box(v, where: str, whole: bool) -> tuple:
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v)):
        raise ValueError(f"{where}: must be [west, south, east, north]")
    if whole and not all(float(x).is_integer() for x in v):
        raise ValueError(f"{where}: must be whole degrees (tile edges)")
    w, s, e, n = (int(x) for x in v) if whole else (float(x) for x in v)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError(f"{where}: west < east within ±180 and south < north within ±90")
    return (w, s, e, n)


def parse(name: str, data: dict) -> Region:
    where = f"region {name}"
    url = str(data.get("osm_url", ""))
    if not url.startswith("https://") or not url.endswith(".osm.pbf"):
        raise ValueError(f"{where}: osm_url must be an https:// link to a .osm.pbf file")
    eb = data.get("ecoregion_bounds")
    return Region(
        name=name,
        label=str(data.get("label") or name.replace("_", " ").title()),
        bounds=_box(data.get("bounds"), f"{where}: bounds", whole=True),
        osm_url=url,
        evergreen_only=bool(data.get("evergreen_only", True)),
        ecoregion_bounds=_box(eb, f"{where}: ecoregion_bounds", whole=False) if eb is not None else None,
    )


def get(name: str) -> Region:
    f = _files().get(name)
    if f is None:
        raise ValueError(f"unknown region {name!r} (known: {', '.join(names()) or 'none'})")
    return parse(name, tomllib.loads(f.read_text(encoding="utf-8")))
