"""RESOLVE Ecoregions 2017 (Dinerstein et al.), clipped to the region we build.

Source: https://storage.googleapis.com/teow2016/Ecoregions2017.zip (CC-BY 4.0).
The global shapefile is read once per region and the ecoregions inside the region's box
(`Region.clip_bounds`) are saved (simplified) to cache/ecoregions/<region>.geojson, which tile
builds rasterise.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np

from forest import config

ECO_DIR = config.CACHE_ROOT / "ecoregions"
ZIP = ECO_DIR / "raw" / "Ecoregions2017.zip"
SIMPLIFY_DEG = 0.002  # ≈ 200 m, far finer than the source accuracy


def clipped_path(region: str) -> Path:
    return ECO_DIR / f"{region}.geojson"


def build_clip(region: str) -> Path:
    import shapefile  # pyshp
    from shapely.geometry import box, mapping, shape

    from forest import regions

    out = clipped_path(region)
    if out.exists():
        return out
    if not ZIP.exists():
        raise FileNotFoundError(f"ecoregions zip not found: {ZIP}")
    bounds = regions.get(region).clip_bounds
    area = box(*bounds)
    with zipfile.ZipFile(ZIP) as z:
        shp = io.BytesIO(z.read("Ecoregions2017.shp"))
        dbf = io.BytesIO(z.read("Ecoregions2017.dbf"))
        shx = io.BytesIO(z.read("Ecoregions2017.shx"))
    r = shapefile.Reader(shp=shp, dbf=dbf, shx=shx, encoding="latin-1")
    fields = [f[0] for f in r.fields[1:]]
    feats = []
    for sr in r.iterShapeRecords():
        x0, y0, x1, y1 = sr.shape.bbox
        if x1 < bounds[0] or x0 > bounds[2] or y1 < bounds[1] or y0 > bounds[3]:
            continue
        rec = dict(zip(fields, sr.record))
        g = shape(sr.shape.__geo_interface__)
        if not g.is_valid:
            g = g.buffer(0)
        g = g.intersection(area).simplify(SIMPLIFY_DEG, preserve_topology=True)
        if g.is_empty:
            continue
        feats.append({"type": "Feature", "geometry": mapping(g), "properties": {
            "eco_id": int(rec["ECO_ID"]), "eco_name": rec["ECO_NAME"],
            "biome_name": rec["BIOME_NAME"], "biome_num": int(rec["BIOME_NUM"]),
        }})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return out


# The clipped GeoJSON, parsed once per file version: the app asks for the ecoregion names
# on every get_state (each build process still reads it once).
_features: dict[str, tuple[tuple, list[dict]]] = {}


def load_features(region: str) -> list[dict]:
    path = build_clip(region)
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _features.get(region)
    if hit is None or hit[0] != key:
        hit = _features[region] = (key, json.loads(path.read_text(encoding="utf-8"))["features"])
    return hit[1]


def ecoregion_names(region: str) -> list[str]:
    return sorted({f["properties"]["eco_name"] for f in load_features(region)})


def ecoregion_biome_names(region: str) -> dict[str, str]:
    """Ecoregion name → its RESOLVE biome name (e.g. "Tropical & Subtropical Moist Broadleaf Forests")."""
    return {f["properties"]["eco_name"]: f["properties"].get("biome_name") or ""
            for f in load_features(region)}


def ecoregion_raster(tile, grid, region: str) -> tuple[np.ndarray, dict[int, str]]:
    """ECO_ID per grid cell (0 = none) and an id → name map for this tile."""
    from rasterio.features import rasterize
    from shapely.geometry import box, shape

    tb = box(*tile.bounds)
    shapes_, names = [], {}
    for f in load_features(region):
        g = shape(f["geometry"])
        if not g.intersects(tb):
            continue
        p = f["properties"]
        names[p["eco_id"]] = p["eco_name"]
        shapes_.append((g.intersection(tb), p["eco_id"]))
    if not shapes_:
        return np.zeros(grid.shape, dtype=np.int32), names
    # Ecoregions are coarse: rasterise at 1/4 detail and upsample (fast, exact enough).
    k = 4
    coarse = type(grid)(grid.tile, grid.cells // k)
    r = rasterize(shapes_, out_shape=coarse.shape, transform=coarse.transform, dtype="int32")
    r = np.repeat(np.repeat(r, k, axis=0), k, axis=1)
    out = np.zeros(grid.shape, dtype=np.int32)
    h, w = min(out.shape[0], r.shape[0]), min(out.shape[1], r.shape[1])
    out[:h, :w] = r[:h, :w]
    return out, names
