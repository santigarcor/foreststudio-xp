"""Forest type by leaf type and climate, the way X-Plane names its own forests.

X-Plane's global-scenery forests (`lib/g8/<family>[_sp]_<temperature>_<moisture>.for`) cover every
combination of leaf type (conifers / broadleaves / mixed) × temperature (vcld … vhot) × moisture
(dry, sdry, wet, rain). A biome with `climate = true` picks from that grid per cell instead of its
elevation bands (and its bushes from lib/g8/shrb_<temperature>_<moisture>):

- leaf type: Copernicus Global Land Cover 100 m, 2019, forest-type layer (one global GeoTIFF on
  Zenodo, stored in blocks: only the tile's 1° window is read, ≈1 MB) → `leaftype.tif` per tile;
- climate: Köppen-Geiger 1 km, 1991–2020 (Beck et al. 2023, CC-BY 4.0): the 131 MB zip is
  downloaded once and only the present-day map (≈12 MB) is kept.

Elevation is already in the climate map (mountains are colder classes), so bands aren't needed.
Regions with `evergreen_only` use the very-hot (evergreen) family whatever the temperature (rule 3).

Special families on top of the leaf type: OSM swamps → wooded wetland (`wetl_wdy`), broadleaf or
mixed forest in cool, wet tropical mountains → montane rainforest / cloud forest (`rainforestm`), and
thin broadleaf or mixed forest in hot, dry climates → savanna trees (`sparse_tr`). Open wetlands get
wetland bushes (`wetl_shrb`), orchards follow the climate (`orch_<temp>_<moist>`), and town trees,
OSM single trees and tree rows use the climate's broadleaf type.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np

from forest import config
from forest.tiles import Grid, Tile

LEAFTYPE_URL = ("https://zenodo.org/records/3939050/files/"
                "PROBAV_LC100_global_v3.0.1_2019-nrt_Forest-Type-layer_EPSG-4326.tif")
KOPPEN_URL = "https://ndownloader.figshare.com/files/61012822"   # koppen_geiger_tif.zip (figshare 21789074)
KOPPEN_MEMBER = "1991_2020/koppen_geiger_0p00833333.tif"
CLIMATE_DIR = config.CACHE_ROOT / "climate"
LEAF_FILE = "leaftype.tif"

# Copernicus forest type: 0 unknown, 1 evergreen needle, 2 evergreen broad, 3 deciduous needle,
# 4 deciduous broad, 5 mixed, 255 not forest.
CONI, BROAD, MIXED, MONTANE, SWAMP = 0, 1, 2, 3, 4
FAMILY = ("coni", "broad", "mixed", "rainforestm", "wetl_wdy")
OSM_LEAF = {"needleleaved": CONI, "broadleaved": BROAD, "mixed": MIXED}   # OSM leaf_type → family
TROPICS = 23.5          # cloud forest only between the tropics
LEAF_WINDOW = 5         # leaf-type pixels (100 m) averaged around each one: 500 m, no salt-and-pepper
LEAF_MAJORITY = 0.6     # share of conifer (or broadleaf) needed; anything in between is mixed

TEMPS = ("vcld", "cld", "tmp", "wrm", "hot", "vhot")
MOISTS = ("dry", "sdry", "wet", "rain")
# Köppen-Geiger class (legend.txt of Beck et al. 2023) → X-Plane temperature, moisture.
KOPPEN = {
    1: ("vhot", "rain"), 2: ("vhot", "wet"), 3: ("vhot", "sdry"),                       # Af Am Aw
    4: ("hot", "dry"), 5: ("tmp", "dry"), 6: ("hot", "sdry"), 7: ("tmp", "sdry"),       # BWh BWk BSh BSk
    8: ("wrm", "sdry"), 9: ("wrm", "sdry"), 10: ("tmp", "sdry"),                        # Csa Csb Csc
    11: ("wrm", "wet"), 12: ("tmp", "wet"), 13: ("cld", "wet"),                         # Cwa Cwb Cwc
    14: ("wrm", "wet"), 15: ("tmp", "wet"), 16: ("cld", "wet"),                         # Cfa Cfb Cfc
    17: ("tmp", "sdry"), 18: ("cld", "sdry"), 19: ("vcld", "sdry"), 20: ("vcld", "sdry"),  # Ds*
    21: ("tmp", "wet"), 22: ("cld", "wet"), 23: ("vcld", "wet"), 24: ("vcld", "wet"),   # Dw*
    25: ("tmp", "wet"), 26: ("cld", "wet"), 27: ("vcld", "wet"), 28: ("vcld", "wet"),   # Df*
    29: ("vcld", "wet"), 30: ("vcld", "dry"),                                           # ET EF
}


def climate_path() -> Path:
    return CLIMATE_DIR / "koppen_geiger_1991_2020.tif"


def uses_climate(rules: dict) -> bool:
    return any(b.get("climate") for b in rules.get("biome", []))


def asset_name(family: int, temp: str, moist: str, sparse: bool) -> str:
    """X-Plane forest for a family and climate."""
    sp = "_sp" if sparse else ""
    if family == MONTANE:
        return f"lib/g8/rainforestm{sp}_vhot_rain.for"
    if family == SWAMP:
        return f"lib/g8/wetl_wdy_{temp}_{moist}.for"
    if family == BROAD and temp == "vhot" and moist == "rain":
        return f"lib/g8/rainforest{sp}_vhot_rain.for"
    if sparse and family != CONI and temp in ("hot", "vhot") and moist in ("dry", "sdry"):
        return f"lib/g8/sparse_tr_{temp}_{moist}.for"     # savanna: a few big scattered trees
    return f"lib/g8/{FAMILY[family]}{sp}_{temp}_{moist}.for"


def shrub_name(temp: str, moist: str, wet: bool = False) -> str:
    return f"lib/g8/{'wetl_shrb' if wet else 'shrb'}_{temp}_{moist}.for"


def town_tree_name(temp: str, moist: str) -> str:
    """Town trees, OSM single trees and tree rows: the climate's broadleaf type."""
    return f"lib/g8/broad_{temp}_{moist}.for"


_ORCHARD = re.compile(r"^lib/g8/(orch|fruit)_[a-z]+_[a-z]+\.for$")
_MISSING = {"lib/g8/orch_wrm_sdry.for": "lib/g8/orch_wrm_dry.for"}   # not in X-Plane's library


def orchard_name(asset: str, temp: str, moist: str) -> str | None:
    """An orchard / fruit-tree plantation type for this climate, or None for other types."""
    m = _ORCHARD.match(asset)
    if not m:
        return None
    temp = "tmp" if temp in ("cld", "vcld") else temp   # no cold-climate orchards in X-Plane
    name = f"lib/g8/{m.group(1)}_{temp}_{moist}.for"
    return _MISSING.get(name, name)


def all_assets() -> set[str]:
    """Every forest and bush type climate picking can use."""
    out = {asset_name(f, t, m, s) for f in range(5) for t in TEMPS for m in MOISTS for s in (False, True)}
    out |= {shrub_name(t, m, w) for t in TEMPS for m in MOISTS for w in (False, True)}
    out |= {town_tree_name(t, m) for t in TEMPS for m in MOISTS}
    out |= {orchard_name(f"lib/g8/{k}_tmp_wet.for", t, m) for k in ("orch", "fruit") for t in TEMPS for m in MOISTS}
    return out


# ---------------------------------------------------------------- downloads

def fetch_leaftype(tile: Tile) -> Path:
    """The tile's 1° window of the Copernicus forest-type layer (cached)."""
    import rasterio

    from forest.fetch.rasters import fetch_window
    with rasterio.Env(GDAL_HTTP_MAX_RETRY=4, GDAL_HTTP_RETRY_DELAY=5):
        return fetch_window(LEAFTYPE_URL, tile, tile.cache / LEAF_FILE)


def ensure_climate(progress=None) -> Path:
    """Download the Köppen-Geiger zip once, keep only the present-day 1 km map."""
    from forest.fetch import sources

    out = climate_path()
    if out.exists():
        return out
    zp = CLIMATE_DIR / "koppen_geiger_tif.zip"
    sources.download(KOPPEN_URL, zp, progress)
    try:
        with zipfile.ZipFile(zp) as z, z.open(KOPPEN_MEMBER) as src:
            part = out.with_name(out.name + ".part")
            with part.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            part.replace(out)
        meta = zp.with_name(zp.name + ".json")
        if meta.exists():
            meta.replace(out.with_name(out.name + ".json"))
    finally:
        zp.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------- on the build grid

def climate_on_grid(tile: Tile, grid: Grid) -> tuple[np.ndarray, np.ndarray] | None:
    """(temperature index, moisture index) per grid cell, or None without the climate map.
    Cells the map leaves empty (sea, coast) take the nearest climate."""
    import rasterio
    from rasterio.windows import from_bounds
    from scipy.ndimage import distance_transform_edt

    p = climate_path()
    if not p.exists():
        return None
    w, s, e, n = tile.bounds
    with rasterio.open(p) as src:
        kg = src.read(1, window=from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths())
    empty = (kg == 0) | (kg > 30)
    if empty.all():
        return None
    if empty.any():
        idx = distance_transform_edt(empty, return_distances=False, return_indices=True)
        kg = kg[tuple(idx)]
    t_lut = np.zeros(31, dtype=np.uint8)
    m_lut = np.zeros(31, dtype=np.uint8)
    for k, (t, m) in KOPPEN.items():
        t_lut[k], m_lut[k] = TEMPS.index(t), MOISTS.index(m)
    return _to_grid(t_lut[kg], grid), _to_grid(m_lut[kg], grid)


def leaf_on_grid(tile: Tile, grid: Grid) -> np.ndarray:
    """Leaf family (CONI / BROAD / MIXED) per grid cell. Each 100 m pixel looks at its 500 m
    neighbourhood: ≥ 60 % conifer → conifers, ≥ 60 % broadleaf → broadleaves, else mixed (Copernicus
    calls much forest "unknown": it counts half and half, like mixed). Without the layer: mixed."""
    import rasterio
    from scipy.ndimage import uniform_filter

    p = tile.cache_path / LEAF_FILE
    if not p.exists():
        return np.full(grid.shape, MIXED, dtype=np.uint8)
    with rasterio.open(p) as src:
        lt = src.read(1)
    forest = lt != 255
    coni = np.isin(lt, (1, 3)) + 0.5 * np.isin(lt, (0, 5))
    broad = np.isin(lt, (2, 4)) + 0.5 * np.isin(lt, (0, 5))
    k = LEAF_WINDOW
    tot = uniform_filter(forest.astype(np.float32), k)
    c = uniform_filter(coni.astype(np.float32), k)
    b = uniform_filter(broad.astype(np.float32), k)
    with np.errstate(invalid="ignore", divide="ignore"):
        c, b = c / tot, b / tot
    fam = np.full(lt.shape, MIXED, dtype=np.uint8)
    fam[c >= LEAF_MAJORITY] = CONI
    fam[b >= LEAF_MAJORITY] = BROAD
    return _to_grid(fam, grid)


def _to_grid(a: np.ndarray, grid: Grid) -> np.ndarray:
    """Nearest-neighbour resize of a north-up 1° array onto the build grid."""
    rows = (np.arange(grid.shape[0]) * a.shape[0] // grid.shape[0]).clip(0, a.shape[0] - 1)
    cols = (np.arange(grid.shape[1]) * a.shape[1] // grid.shape[1]).clip(0, a.shape[1] - 1)
    return a[np.ix_(rows, cols)]
