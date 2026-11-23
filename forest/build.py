"""Phase 1 build: one tile, end to end.

worldcover + hansen + OSM + base-mesh elevation
  -> tree mask / density / class on a working grid
  -> polygons per (asset, density level), chunked and simplified
  -> tree budget -> overlay DSF text (+ tile-wide sim/exclude_for)
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import rasterio.errors
import shapely

# Preview/overlay PNGs are plain images (no map coordinates); that is intended.
warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.windows import Window
from shapely.geometry import Polygon, box, shape
from shapely.geometry.polygon import orient

from forest import config
from forest.assets.catalog import load_catalog
from forest.dsf.text import FILL_LINE, FILL_POINTS, FILL_SOLID, ForestPolygon, OverlayTile, signed_area
from forest.exclusions import coverage_mask, exclusion_mask, load_osm, osm_geometries, osm_tree_hints
from forest.fetch.elevation import elevation_on_grid
from forest.fetch.osm import fetch_osm
from forest.fetch.rasters import (
    WC_BARE,
    WC_BUILT,
    WC_GRASS,
    WC_MANGROVE,
    WC_SHRUB,
    WC_TREE,
    WC_WETLAND,
    fetch_all,
    read_on_grid,
)
from forest.tiles import Grid, Tile


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


@dataclass
class Settings:
    profile: dict
    rules: dict
    region: str = "colombia"   # not in the fingerprint: the stats record the region separately

    @classmethod
    def load(cls, profile: str, region: str) -> "Settings":
        return cls(
            profile={**PROFILE_DEFAULTS, **_toml(config.profile_path(profile))},
            rules=fill_rule_defaults(_toml(config.rules_path(region)), region),
            region=region,
        )


# WorldCover mangrove share that makes a cell "mangrove" (biome + kept in water). Not a profile
# setting; a hand-set `mangrove_min_frac` in a profile still wins. Kept out of PROFILE_DEFAULTS
# on purpose: that would change every tile's settings fingerprint and mark them all outdated.
MANGROVE_MIN_FRAC = 0.3
# Inside OSM-mapped mangroves, grass, herbaceous wetland and shrubland count this much as trees.
OSM_MANGROVE_LOW_VEG = 0.5

# Profile keys added after the first release: older profile files build as if they had these.
PROFILE_DEFAULTS = {
    "scrub_min_frac": 0.5,
    "scrub_density": 0.5,
    "town_trees_per_km2": 150,
    "town_min_built": 0.3,
}


def fill_rule_defaults(rules: dict, region: str) -> dict:
    """Fill keys added after the first release (scrub, town trees) from the shipped default
    rules: a biome without them takes the values of the default biome with the same name.
    An explicit "" (no scrub) is kept."""
    path = config.DEFAULTS_DIR / "rules" / f"{region}.toml"
    shipped = _toml(path) if path.exists() else {}
    by_name = {b["name"]: b for b in shipped.get("biome", [])}
    out = dict(rules)
    out.setdefault("town_tree", shipped.get("town_tree") or rules.get("single_tree", ""))
    biomes = []
    for b in rules.get("biome", []):
        b = dict(b)
        ref = by_name.get(b.get("name"), {})
        b.setdefault("scrub", ref.get("scrub", ""))
        b.setdefault("scrub_on_bare", bool(ref.get("scrub_on_bare", False)))
        b.setdefault("town_tree", ref.get("town_tree", ""))
        biomes.append(b)
    if biomes:
        out["biome"] = biomes
    return out


def km2_per_deg2(lat: int) -> float:
    """km² in one square degree at the middle of the tile whose south edge is `lat`."""
    return (111.32 * math.cos(math.radians(lat + 0.5))) * 110.574


def _pad3(pts: list) -> list:
    """A winding needs 3 points: repeat the last one (duplicates just stack trees)."""
    while len(pts) < 3:
        pts.append(pts[-1])
    return pts


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def _class_share(cls: np.ndarray, src, grid: Grid, classes: tuple[int, ...]) -> np.ndarray:
    """Share (0-1) of the source pixels in `classes` per grid cell."""
    k = _block_factor(src, grid)
    if k:
        # The grid is the source pixel grid made k× coarser (WorldCover at 6000 cells: k = 2): the
        # average of k×k pixels of 0/100 is 100·count/k², exact in uint8 for k = 1 or 2, so this
        # gives the same values as GDAL's average resampling without a full reprojection.
        h, w = grid.shape
        hit = cls == classes[0]
        for c in classes[1:]:
            hit |= cls == c
        count = hit.reshape(h, k, w, k).sum(axis=(1, 3), dtype=np.uint8)
        return (count * np.uint8(100 // (k * k))).astype(np.float32) / 100.0
    from rasterio.warp import reproject

    hit = np.where(np.isin(cls, classes), 100, 0).astype(np.uint8)  # percent, 1 byte/px
    out = np.zeros(grid.shape, dtype=np.uint8)
    reproject(hit, out, src_transform=src.transform, src_crs=src.crs,
              dst_transform=grid.transform, dst_crs="EPSG:4326", resampling=Resampling.average)
    return out.astype(np.float32) / 100.0


def _block_factor(src, grid: Grid) -> int | None:
    """k when every grid cell is exactly k×k source pixels (k = 1 or 2), else None."""
    t = src.transform
    if not src.crs or src.crs.to_epsg() != 4326 or t.b or t.d or t.a <= 0 or t.e >= 0:
        return None
    k = round(grid.res / t.a)
    tol = t.a * 1e-3   # a thousandth of a pixel
    if k not in (1, 2) or abs(t.a * k - grid.res) > 1e-12 or abs(t.e + t.a) > 1e-12:
        return None
    if abs(t.c - grid.tile.lon) > tol or abs(t.f - (grid.tile.lat + 1)) > tol:
        return None
    if (src.height, src.width) != (grid.cells * k, grid.cells * k):
        return None
    return k


def rule_biomes(rules: dict) -> list[dict]:
    """Biomes from the rules; the last one is the catch-all "default".
    Old rule files with only top-level [[band]] become a single default biome."""
    biomes = [dict(b) for b in rules.get("biome", [])]
    if not biomes:
        biomes = [{"name": "default", "label": "All", "color": [60, 160, 70], "ecoregions": [],
                   "band": rules["band"]}]
    default = [b for b in biomes if b["name"] == "default"]
    others = [b for b in biomes if b["name"] != "default"]
    if not default:
        default = [dict(others[-1], name="default", ecoregions=[])]
    return others + default[:1]


def biome_raster(tile: Tile, grid: Grid, biomes: list[dict], region: str) -> np.ndarray:
    """Index into `biomes` per grid cell (unmapped ecoregions → default, the last)."""
    from forest.fetch.ecoregions import ecoregion_raster

    eco, names = ecoregion_raster(tile, grid, region)
    by_name = {e: i for i, b in enumerate(biomes) for e in b.get("ecoregions", [])}
    lut = np.full(int(eco.max()) + 1 if eco.size else 1, len(biomes) - 1, dtype=np.uint8)
    for eco_id, name in names.items():
        if name in by_name and eco_id < lut.size:
            lut[eco_id] = by_name[name]
    return lut[eco]


def classify(tile: Tile, s: Settings, t0: float) -> dict:
    p = s.profile
    grid = Grid(tile, p["grid_cells"])
    paths = fetch_all(tile)
    _log("rasters cached", t0)

    with rasterio.open(paths["worldcover"]) as src:
        wc = src.read(1)
        frac = _class_share(wc, src, grid, (WC_TREE, WC_MANGROVE))
        mangrove_frac = _class_share(wc, src, grid, (WC_MANGROVE,))
        shrub_frac = _class_share(wc, src, grid, (WC_SHRUB,))
        bare_frac = _class_share(wc, src, grid, (WC_BARE,))
        built_frac = _class_share(wc, src, grid, (WC_BUILT,))
        # Grass and herbaceous wetland (+ shrubland below): inside OSM mangroves they count as half trees.
        low_veg_frac = _class_share(wc, src, grid, (WC_GRASS, WC_WETLAND))
        del wc
    tc = read_on_grid(paths["treecover2000"], grid, Resampling.bilinear) / 100.0
    loss = read_on_grid(paths["lossyear"], grid, Resampling.nearest)

    lat_mid = tile.lat + 0.5
    # osm.json is parsed once for cut-outs and tree hints, and freed before the heavy rasterising
    # (a city tile's parsed JSON is over a GB of Python objects).
    osm = load_osm(fetch_osm(tile, s.region))
    geoms = osm_geometries(osm, lat_mid, p)
    hints = osm_tree_hints(osm)
    del osm

    # Mangroves: cells WorldCover calls mangrove, plus OSM-mapped mangroves (natural=wetland +
    # wetland=mangrove). They get the mangrove biome and are not cut by water. WorldCover often
    # calls low or young mangrove "herbaceous wetland" or "grass" (SKCG: the mangroves between the
    # runway and the Ciénaga de la Virgen, the Juan Angola canal banks), so inside OSM mangroves
    # those count as half trees; open water, sand and built-up still get none.
    osm_mangrove = coverage_mask(hints["mangrove"], grid, 0.5)
    if osm_mangrove.any():
        frac = np.where(osm_mangrove, np.minimum(frac + OSM_MANGROVE_LOW_VEG * (low_veg_frac + shrub_frac), 1.0),
                        frac).astype(np.float32)
    del low_veg_frac
    mangrove = (mangrove_frac >= p.get("mangrove_min_frac", MANGROVE_MIN_FRAC)) | osm_mangrove
    del mangrove_frac
    _log(f"rasters on grid (mangrove cells: {int(mangrove.sum()):,}, "
         f"OSM mangroves: {len(hints['mangrove'])} areas)", t0)
    min_cover = p.get("cutout_min_cover", 0.5)
    excl, excl_stats = exclusion_mask({k: v for k, v in geoms.items() if k != "water"}, grid, min_cover)
    water = coverage_mask(geoms["water"], grid, min_cover)
    excl |= water & ~mangrove  # mangroves stand in water: OSM lagoons/ciénagas must not remove them
    excl_stats["water"] = int(water.sum())
    excl_stats["water_kept_for_mangroves"] = int((water & mangrove).sum())
    _log(f"exclusions rasterised {excl_stats}", t0)

    green = coverage_mask(hints["green"], grid, 0.5)
    _log(f"OSM tree hints: {len(hints['green'])} green areas, {len(hints['points'])} single trees, "
         f"{len(hints['rows'])} tree rows", t0)

    elev = elevation_on_grid(tile, grid)

    from scipy.ndimage import gaussian_filter, median_filter

    # Parks, gardens, golf courses and OSM woods: accept trees at a lower satellite share,
    # because 10 m land cover misses tree lines and scattered trees there.
    threshold = np.where(green | osm_mangrove, p.get("osm_green_min_frac", p["min_tree_frac"]), p["min_tree_frac"])
    raw = (frac >= threshold) & ~(loss >= p["recent_loss_from"])
    unsmoothed = raw
    # Majority filter: removes isolated cells and pinholes → far fewer tiny polygons.
    for _ in range(p.get("smooth_passes", 1)):
        raw = median_filter(raw.astype(np.uint8), size=3).astype(bool)
    raw |= unsmoothed & mangrove  # keep narrow mangrove fringes along shores
    trees = raw & ~excl  # cut-outs applied after smoothing so roads stay open
    density = np.clip(0.6 * frac + 0.4 * tc, 0.0, 1.0)
    # Hansen's 2000 tree cover misses most Caribbean mangroves (≈5 % on SKCG's), which made them
    # thin and gave them the sparse shrub type: their thickness comes from WorldCover alone.
    density[mangrove] = frac[mangrove]
    del osm_mangrove
    if p.get("density_blur_cells"):
        density = gaussian_filter(density, sigma=p["density_blur_cells"])
    # Full-tile arrays add up (a 6000² float64 is 288 MB): free each one once it's used.
    del threshold, unsmoothed, frac, tc, loss

    # Biome per cell from the ecoregion map, then asset from that biome's elevation band
    # and dense/sparse.
    biomes = rule_biomes(s.rules)
    biome_idx = biome_raster(tile, grid, biomes, s.region)
    mg = next((i for i, b in enumerate(biomes) if b["name"] == "mangrove"), None)
    if mg is not None:
        biome_idx[mangrove] = mg  # WorldCover's 10 m mangrove class beats the coarse ecoregion map
    _log("biomes: " + ", ".join(f"{b['name']} {100 * (biome_idx == i).mean():.0f}%"
                                for i, b in enumerate(biomes) if (biome_idx == i).any()), t0)
    assets: list[str] = []
    for bm in biomes:
        for b in bm["band"]:
            for k in ("dense", "sparse"):
                if b[k] not in assets:
                    assets.append(b[k])
    sparse = density < s.rules["sparse_below"]
    asset_idx = np.zeros(grid.shape, dtype=np.int16)
    for bi, bm in enumerate(biomes):
        in_biome = biome_idx == bi
        if not in_biome.any():
            continue
        bands = bm["band"]
        band_idx = np.digitize(elev, [b["elev_max"] for b in bands[:-1]])
        for i, b in enumerate(bands):
            sel = in_biome & (band_idx == i)
            asset_idx[sel & ~sparse] = assets.index(b["dense"])
            asset_idx[sel & sparse] = assets.index(b["sparse"])
    del elev, sparse

    levels = p["density_levels"]
    assert len(levels) <= 16, "density levels share the cell code with the asset (asset * 16 + level)"
    # uint8 instead of digitize's int64 (values 0-15): 36 MB instead of 288 MB at 6000 cells.
    level_idx = np.digitize(density, [(a + b) / 2 for a, b in zip(levels, levels[1:])]).astype(np.uint8)
    del density

    # Plantations (OSM): replace the biome forest inside their outline, planted regardless of
    # the satellite tree share; rules with asset "" clear trees (sugar cane, pineapple, rice…).
    plants = s.rules.get("plantation", [])
    plant_idx = plantation_raster(hints["plantations"], plants, grid, min_cover)
    if (plant_idx >= 0).any():
        for i, pr in enumerate(plants):
            sel = plant_idx == i
            if not sel.any():
                continue
            if pr.get("asset"):
                if pr["asset"] not in assets:
                    assets.append(pr["asset"])
                asset_idx[sel] = assets.index(pr["asset"])
                level_idx[sel] = int(np.argmin([abs(lv - float(pr.get("density", 0.7))) for lv in levels]))
        planted = plant_idx >= 0
        has_asset = np.isin(plant_idx, [i for i, pr in enumerate(plants) if pr.get("asset")])
        trees = ((raw & ~planted) | (planted & has_asset)) & ~excl
        _log("plantations: " + ", ".join(f"{pr['name']} {int((plant_idx == i).sum()):,} cells"
                                         for i, pr in enumerate(plants) if (plant_idx == i).any()), t0)
    else:
        planted = np.zeros(grid.shape, dtype=bool)
    # Airport grounds (OSM aerodrome boundary): WorldCover calls the pavement "built-up", so without
    # this the grass between taxiways became town (SKRG: town trees 20-35 m from taxiways).
    airport = coverage_mask(hints["airports"], grid, 0.25)

    # Scrub: bushes on WorldCover shrubland (and bare/sparse land in biomes that ask for it,
    # e.g. La Guajira), plus OSM natural=scrub/heath; never on forest, plantations or cut-outs.
    scrub = np.zeros(grid.shape, dtype=bool)
    if float(p.get("scrub_density", 0)) > 0:
        on_bare = np.array([bool(b.get("scrub_on_bare")) for b in biomes])[biome_idx]
        scrub_share = shrub_frac + np.where(on_bare, bare_frac, 0.0)
        osm_scrub = coverage_mask(hints["scrub"], grid, 0.5)
        scrub_share = np.where(osm_scrub, np.maximum(scrub_share, 0.7), scrub_share)
        scrub = scrub_share >= p["scrub_min_frac"]
        for _ in range(p.get("smooth_passes", 1)):
            scrub = median_filter(scrub.astype(np.uint8), size=3).astype(bool)
        has_scrub = np.array([bool(b.get("scrub")) for b in biomes])[biome_idx]
        scrub &= has_scrub & ~raw & ~planted & ~excl & ~mangrove & ~airport
        if scrub.any():
            sd = np.clip(scrub_share * float(p["scrub_density"]), 0.0, 1.0)
            for bi, bm in enumerate(biomes):
                sel = scrub & (biome_idx == bi)
                if not sel.any():
                    continue
                if bm["scrub"] not in assets:
                    assets.append(bm["scrub"])
                asset_idx[sel] = assets.index(bm["scrub"])
            # Nearest density step, computed for scrub cells only (not a cells × levels array).
            level_idx[scrub] = np.argmin(np.abs(sd[scrub][:, None] - np.asarray(levels, dtype=np.float32)), axis=-1)
            del sd
            _log(f"scrub: {int(scrub.sum()):,} cells", t0)
        del on_bare, scrub_share, osm_scrub, has_scrub
    del shrub_frac, bare_frac

    # Town: built-up land (WorldCover) or OSM residential, not industrial/commercial, not forest.
    town = (built_frac >= p.get("town_min_built", 0.3)) | coverage_mask(hints["residential"], grid, 0.5)
    town &= ~coverage_mask(hints["no_town_trees"], grid, 0.5) & ~raw & ~planted & ~water & ~airport
    del built_frac
    # Cell code = asset * 16 + density level + 1 (0 = no trees), built in int32 directly.
    code = np.zeros(grid.shape, dtype=np.int32)
    m = trees | scrub
    code[m] = asset_idx[m].astype(np.int32) * 16 + level_idx[m] + 1
    del m

    return {
        "grid": grid, "code": code, "assets": assets, "levels": levels,
        "excl_stats": excl_stats, "trees": trees, "excl": excl, "biome_idx": biome_idx, "raw": raw,
        "biomes": biomes, "level_idx": level_idx, "green": green, "hints": hints, "geoms": geoms,
        "plant_idx": plant_idx, "plants": plants, "scrub": scrub, "town": town,
        "tree_cells": int(trees.sum()), "scrub_cells": int(scrub.sum()),
    }


def _tag_match(tags: dict, entry: str) -> bool:
    for cond in entry.split("&"):
        k, _, v = cond.strip().partition("=")
        if str(tags.get(k.strip(), "")).strip().lower() != v.strip().lower():
            return False
    return True


def plantation_rule(tags: dict, plants: list[dict]) -> int:
    """Index of the first plantation rule matching these OSM tags, or -1."""
    for i, pr in enumerate(plants):
        if any(_tag_match(tags, m) for m in pr.get("match", [])):
            return i
    return -1


def plantation_raster(items: list, plants: list[dict], grid: Grid, min_cover: float) -> np.ndarray:
    """Plantation rule index per cell (-1 = none). Earlier rules win where outlines overlap."""
    out = np.full(grid.shape, -1, dtype=np.int16)
    if not plants or not items:
        return out
    groups: dict[int, list] = {}
    for poly, tags in items:
        i = plantation_rule(tags, plants)
        if i >= 0:
            groups.setdefault(i, []).append(poly)
    for i in sorted(groups, reverse=True):  # paint later rules first, earlier ones on top
        out[coverage_mask(groups[i], grid, min_cover)] = i
    return out


def osm_trees(c: dict, s: Settings, t0: float) -> list[ForestPolygon]:
    """Single trees (X-Plane point forests) and tree rows (line forests) from OSM."""
    from shapely import STRtree
    from shapely.geometry import Point

    p = s.profile
    grid: Grid = c["grid"]
    asset = s.rules.get("single_tree")
    if not asset:
        return []
    w0, s0, e0, n0 = grid.tile.bounds
    tile_box = box(w0, s0, e0, n0)
    blockers = [g for g in c["geoms"]["buildings"] + c["geoms"]["runways"] if g.is_valid]
    tree_idx = STRtree(blockers) if blockers else None

    def blocked(pt) -> bool:
        if tree_idx is None:
            return False
        return any(blockers[i].contains(pt) for i in tree_idx.query(pt))

    def in_forest(lon: float, lat: float) -> bool:
        r = int((n0 - lat) * grid.cells)
        col = int((lon - w0) * grid.cells)
        return 0 <= r < grid.cells and 0 <= col < grid.cells and bool(c["trees"][r, col])

    polys: list[ForestPolygon] = []
    if int(p.get("osm_single_trees", 1)):
        step = 1.0 / p["chunks_per_degree"]
        groups: dict[tuple[int, int], list] = {}
        kept = 0
        for lon, lat in c["hints"]["points"]:
            if not (w0 <= lon <= e0 and s0 <= lat <= n0) or in_forest(lon, lat) or blocked(Point(lon, lat)):
                continue
            groups.setdefault((int((lon - w0) / step), int((lat - s0) / step)), []).append((lon, lat))
            kept += 1
        for pts in groups.values():
            polys.append(ForestPolygon(asset=asset, exterior=_pad3(pts), density=1.0, fill=FILL_POINTS))
        _log(f"OSM single trees: {kept} placed in {len(groups)} groups", t0)
    if int(p.get("osm_tree_rows", 1)):
        n_rows = 0
        for row in c["hints"]["rows"]:
            g = row.intersection(tile_box)
            for part in getattr(g, "geoms", [g]):
                if part.geom_type == "LineString" and len(part.coords) >= 2:
                    pts = list(part.coords)
                    if len(pts) == 2:
                        pts.insert(1, ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2))
                    polys.append(ForestPolygon(asset=asset, exterior=pts, density=1.0, fill=FILL_LINE))
                    n_rows += 1
        _log(f"OSM tree rows: {n_rows}", t0)
    return polys


# Points per point-forest polygon (several polygons per chunk if a town has more trees).
MAX_POINTS_PER_POLYGON = 1000
TOWN_OVERSAMPLE = 3.0


def town_trees(c: dict, s: Settings, t0: float) -> list[ForestPolygon]:
    """Scattered trees in towns (X-Plane point forests): random spots in town cells, kept off
    buildings, roads, runways and rail/power corridors. The same tile always gets the same trees."""
    import shapely
    from shapely import STRtree

    p = s.profile
    per_km2 = float(p.get("town_trees_per_km2", 0))
    town = c.get("town")
    if per_km2 <= 0 or town is None or not town.any():
        return []
    grid: Grid = c["grid"]
    w0, s0, e0, n0 = grid.tile.bounds
    cell_km2 = km2_per_deg2(grid.tile.lat) / grid.cells ** 2
    seed = int(hashlib.sha256(grid.tile.name.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    rows, cols = np.nonzero(town)
    target = int(round(per_km2 * cell_km2 * rows.size))
    # Draw extra spots (many fall on buildings/roads: 36 % in Medellín, 54 % in Barranquilla), then trim to the target,
    # so "trees per km²" is the number of trees actually planted.
    n = rng.poisson(TOWN_OVERSAMPLE * per_km2 * cell_km2, size=rows.size)
    rows, cols = np.repeat(rows, n), np.repeat(cols, n)
    if not rows.size:
        return []
    d = 1.0 / grid.cells
    lon = w0 + (cols + rng.random(cols.size)) * d
    lat = n0 - (rows + rng.random(rows.size)) * d
    g = c["geoms"]
    blockers = [x for k in ("buildings", "roads", "runways", "corridors") for x in g.get(k, [])
                if x is not None and x.is_valid and not x.is_empty]
    blockers += [x for x in c["hints"].get("airports", []) if not x.is_empty]
    keep = np.ones(lon.size, dtype=bool)
    if blockers:
        hit = STRtree(blockers).query(shapely.points(lon, lat), predicate="intersects")[0]
        keep[np.unique(hit)] = False
    dropped = int((~keep).sum())
    free = np.flatnonzero(keep)
    if free.size > target:
        free = np.sort(rng.choice(free, size=target, replace=False))
    lon, lat, rows, cols = lon[free], lat[free], rows[free], cols[free]

    default = s.rules.get("town_tree") or s.rules.get("single_tree")
    asset_of = [b.get("town_tree") or default for b in c["biomes"]]
    step = 1.0 / p["chunks_per_degree"]
    groups: dict[tuple, list] = {}
    biome = c["biome_idx"][rows, cols]
    for x, y, bi in zip(lon.tolist(), lat.tolist(), biome.tolist()):
        groups.setdefault((asset_of[bi], int((x - w0) / step), int((y - s0) / step)), []).append((x, y))
    polys: list[ForestPolygon] = []
    for (asset, _, _), pts in groups.items():
        for i in range(0, len(pts), MAX_POINTS_PER_POLYGON):
            part = _pad3(pts[i:i + MAX_POINTS_PER_POLYGON])
            polys.append(ForestPolygon(asset=asset, exterior=part, density=1.0, fill=FILL_POINTS,
                                       kind="town"))
    _log(f"town trees: {lon.size:,} in {int(town.sum()):,} town cells "
         f"({dropped:,} of {keep.size:,} candidate spots fell on buildings/roads)", t0)
    return polys


def vectorize(c: dict, s: Settings, t0: float) -> list[ForestPolygon]:
    p = s.profile
    grid: Grid = c["grid"]
    code = c["code"]
    lat_mid = grid.tile.lat + 0.5
    m_per_deg_x = 111_320.0 * math.cos(math.radians(lat_mid))
    m_per_deg_y = 110_574.0
    tol = p["simplify_m"] / m_per_deg_x
    min_area_deg2 = p["min_area_m2"] / (m_per_deg_x * m_per_deg_y)
    step = grid.cells // p["chunks_per_degree"]
    w0, s0, e0, n0 = grid.tile.bounds
    tile_box = box(w0, s0, e0, n0)

    polys: list[ForestPolygon] = []
    for r in range(0, grid.cells, step):
        for col in range(0, grid.cells, step):
            win = Window(col, r, min(step, grid.cells - col), min(step, grid.cells - r))
            sub = np.ascontiguousarray(code[r : r + win.height, col : col + win.width])
            if not sub.any():
                continue
            tr = rasterio.windows.transform(win, grid.transform)
            found = [(shape(geom), int(val)) for geom, val in shapes(sub, mask=sub > 0, transform=tr, connectivity=4)]
            # One shapely call per chunk instead of one per shape (same GEOS operations).
            geoms = shapely.simplify(np.array([g for g, _ in found], dtype=object), tol, preserve_topology=True)
            # Clip only what crosses the tile edge. Clipping a polygon that lies inside keeps
            # exactly its vertices but restarts its rings elsewhere (checked on +06-076); it
            # cost ~7 s per city tile for nothing.
            b = shapely.bounds(geoms)
            cross = (b[:, 0] < w0) | (b[:, 1] < s0) | (b[:, 2] > e0) | (b[:, 3] > n0)
            if cross.any():
                geoms[cross] = shapely.intersection(geoms[cross], tile_box)
            for g, (_, val) in zip(geoms, found):
                for part in getattr(g, "geoms", [g]):
                    if part.geom_type != "Polygon" or part.area < min_area_deg2:
                        continue
                    piece = part
                    rings = part.interiors
                    if len(rings):
                        areas = shapely.area(shapely.polygons(np.array(list(rings), dtype=object)))
                        keep = areas >= min_area_deg2 / 4
                        if not keep.all():
                            piece = Polygon(part.exterior, [h for h, k in zip(rings, keep) if k])
                    v = val - 1
                    for sub_poly in _split_for_dsf(piece):
                        sub_poly = orient(sub_poly, sign=1.0)
                        polys.append(ForestPolygon(
                            asset=c["assets"][v // 16],
                            exterior=shapely.get_coordinates(sub_poly.exterior).tolist(),
                            holes=[shapely.get_coordinates(h).tolist() for h in sub_poly.interiors],
                            density=c["levels"][v % 16],  # 0-1 for now; scaled in budget()
                        ))
    _log(f"vectorised {len(polys)} polygons", t0)
    return polys


# A DSF polygon holds at most 255 windings (outline + holes); stay safely below.
MAX_DSF_HOLES = 250


def _split_for_dsf(poly, depth: int = 0) -> list:
    """Cut a polygon with too many holes into halves (recursively) until every piece fits
    the DSF limit. Holes are kept exactly; the trees look the same in the sim."""
    if len(poly.interiors) <= MAX_DSF_HOLES or depth > 12:
        return [poly]
    x0, y0, x1, y1 = poly.bounds
    if (x1 - x0) >= (y1 - y0):
        xm = (x0 + x1) / 2
        halves = [box(x0, y0, xm, y1), box(xm, y0, x1, y1)]
    else:
        ym = (y0 + y1) / 2
        halves = [box(x0, y0, x1, ym), box(x0, ym, x1, y1)]
    out = []
    for h in halves:
        g = poly.intersection(h)
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "Polygon" and not part.is_empty:
                out += _split_for_dsf(part, depth + 1)
    return out


def budget(polys: list[ForestPolygon], tile: Tile, s: Settings) -> dict:
    """Scale densities to fit the tree budget; convert to DSF 0-255."""
    cat = {f["virtual"]: f for f in load_catalog()["forests"]}
    missing = sorted({p.asset for p in polys} - cat.keys())
    if missing:
        raise ValueError(f"forest type(s) not in this X-Plane's forest library: {missing}. "
                         "Pick another type in Forest types, or rebuild the catalog (Settings).")
    from forest import regions

    seasonal = [p.asset for p in polys if cat[p.asset]["seasons"]]
    if seasonal and regions.get(s.region).evergreen_only:
        raise ValueError(f"seasonal assets not allowed in region {s.region}: {sorted(set(seasonal))}")
    lat_mid = tile.lat + 0.5
    deg2_km2 = km2_per_deg2(tile.lat)

    def area_km2(p: ForestPolygon) -> float:
        a = abs(signed_area(p.exterior)) - sum(abs(signed_area(h)) for h in p.holes)
        return max(a, 0.0) * deg2_km2

    m_per_deg = 111_320.0 * math.cos(math.radians(lat_mid))

    def line_trees(p: ForestPolygon) -> float:
        length_m = sum(math.dist(a, b) for a, b in zip(p.exterior, p.exterior[1:])) * m_per_deg
        spacing = (cat[p.asset].get("spacing_m") or [10, 10])[0] or 10
        return length_m / spacing

    solid = [p for p in polys if p.fill == FILL_SOLID]
    g = s.profile["global_density"]
    est = sum(area_km2(p) * p.density * g * cat[p.asset]["max_trees_per_km2"] for p in solid)
    scale = min(1.0, s.profile["tile_tree_budget"] / est) if est else 1.0
    per_asset: dict[str, dict] = {}
    osm_points = osm_row_trees = town = 0
    for p in polys:
        if p.fill == FILL_POINTS:  # one tree per point (OSM or town), never thinned
            p.density = 255
            if p.kind == "town":
                town += len(set(p.exterior))
            else:
                osm_points += len(set(p.exterior))
            continue
        if p.fill == FILL_LINE:
            p.density = 255
            osm_row_trees += line_trees(p)
            continue
        d = p.density * g * scale
        p.density = max(1, min(255, round(d * 255)))
        a = per_asset.setdefault(p.asset, {"polygons": 0, "km2": 0.0, "trees": 0.0})
        a["polygons"] += 1
        a["km2"] += area_km2(p)
        a["trees"] += area_km2(p) * d * cat[p.asset]["max_trees_per_km2"]
    forest_trees = sum(a["trees"] for a in per_asset.values())
    return {
        "estimated_trees_before_budget": round(est),
        "budget_scale": round(scale, 3),
        "estimated_trees": round(forest_trees + osm_points + osm_row_trees + town),
        "forest_km2": round(sum(a["km2"] for a in per_asset.values()), 1),
        "osm_single_trees": osm_points,
        "osm_tree_row_trees": round(osm_row_trees),
        "town_trees": town,
        "per_asset": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in per_asset.items()},
    }


def preview(c: dict, out: Path, size: int = 1500) -> None:
    """RGB PNG of the classification: forest by band, exclusions red."""
    from scipy.ndimage import zoom

    f = size / c["grid"].cells
    trees = zoom(c["trees"].astype(np.uint8), f, order=0).astype(bool)
    excl = zoom(c["excl"].astype(np.uint8), f, order=0).astype(bool)
    biome = zoom(c["biome_idx"].astype(np.uint8), f, order=0)
    palette = np.array([b.get("color", [60, 160, 70]) for b in c["biomes"]], dtype=np.uint8)
    img = np.full((3, *trees.shape), 235, dtype=np.uint8)
    for i in range(3):
        img[i][trees] = palette[biome][trees][:, i]
    if c.get("scrub") is not None:
        scrub = zoom(c["scrub"].astype(np.uint8), f, order=0).astype(bool)
        for i in range(3):
            img[i][scrub] = OVERLAY_SCRUB[i]
    img[0][excl], img[1][excl], img[2][excl] = 220, 60, 60
    with rasterio.open(out, "w", driver="PNG", width=trees.shape[1], height=trees.shape[0],
                       count=3, dtype="uint8") as dst:
        dst.write(img)


# Overlay colour per elevation band (bands beyond the 4th reuse the last colour), and the
# brightness range from thinnest to thickest density step. Shared with the app's legend.
OVERLAY_PALETTE = [[0, 120, 60], [40, 170, 70], [120, 200, 60], [190, 190, 120]]
OVERLAY_SHADE = (0.55, 1.0)
OVERLAY_CUT = [235, 50, 50]
OVERLAY_OSM = [255, 215, 40]  # OSM single trees and tree rows
OVERLAY_SCRUB = [205, 150, 225]  # scrub / bushes (brightness = thickness); unlike any biome colour
OVERLAY_TOWN = [90, 210, 255]  # scattered town trees


def overlay(c: dict, out: Path, size: int = 2400, osm_polys: list[ForestPolygon] | None = None) -> None:
    """Transparent RGBA PNG for the app's map: forest coloured by band (brighter = denser),
    red where trees were cut out by roads/buildings/water/airports, transparent elsewhere."""
    from scipy.ndimage import zoom

    f = size / c["grid"].cells
    z = lambda a: zoom(a.astype(np.uint8), f, order=0)  # noqa: E731
    trees, cut = z(c["trees"]).astype(bool), z(c["raw"] & c["excl"]).astype(bool)
    biome, level = z(c["biome_idx"]), z(c["level_idx"])
    palette = np.array([b.get("color", [60, 160, 70]) for b in c["biomes"]], dtype=np.float32)
    lo, hi = OVERLAY_SHADE
    shade = lo + (hi - lo) * (level.astype(np.float32) / max(1, len(c["levels"]) - 1))
    img = np.zeros((4, *trees.shape), dtype=np.uint8)
    rgb = palette[np.clip(biome, 0, len(palette) - 1)] * shade[..., None]
    for i in range(3):
        img[i][trees] = rgb[..., i][trees].astype(np.uint8)
    img[3][trees] = 210
    if c.get("scrub") is not None and c["scrub"].any():
        sc = z(c["scrub"]).astype(bool)
        tone = np.array(OVERLAY_SCRUB, dtype=np.float32) * shade[..., None]
        for i in range(3):
            img[i][sc] = tone[..., i][sc].astype(np.uint8)
        img[3][sc] = 190
    if c.get("plants") and (c["plant_idx"] >= 0).any():
        pidx = zoom(c["plant_idx"].astype(np.int16) + 1, f, order=0).astype(np.int16) - 1
        for j, pr in enumerate(c["plants"]):
            m = (pidx == j) & trees
            if m.any():
                for i in range(3):
                    img[i][m] = pr.get("color", [230, 120, 40])[i]
                img[3][m] = 230
    img[0][cut], img[1][cut], img[2][cut] = OVERLAY_CUT
    img[3][cut] = 230
    if osm_polys:
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        from shapely.geometry import LineString, MultiPoint

        w0, s0, e0, n0 = c["grid"].tile.bounds
        tr = from_origin(w0, n0, 1.0 / size, 1.0 / size)
        for kind, colour in (("town", OVERLAY_TOWN), ("", OVERLAY_OSM)):  # OSM trees drawn on top
            shapes_ = [(MultiPoint(p.exterior) if p.fill == FILL_POINTS else LineString(p.exterior), 1)
                       for p in osm_polys if p.kind == kind]
            if not shapes_:
                continue
            m = rasterize(shapes_, out_shape=trees.shape, transform=tr, all_touched=True,
                          dtype="uint8").astype(bool)
            img[0][m], img[1][m], img[2][m] = colour
            img[3][m] = 255
    with rasterio.open(out, "w", driver="PNG", width=trees.shape[1], height=trees.shape[0],
                       count=4, dtype="uint8") as dst:
        dst.write(img)


# Bump when a change to the build itself (not the settings) changes the result, so every tile
# built before shows up as outdated.
BUILD_VERSION = 4  # 2: scrub and town trees; 3: none of them inside airport grounds;
# 4: OSM mangroves, mangrove thickness from WorldCover only
DATA_FILES = ("worldcover.tif", "treecover2000.tif", "lossyear.tif", "osm.json")


def settings_fingerprint(s: Settings) -> str:
    """Short hash of everything that decides how a tile looks: profile, forest types, build."""
    blob = json.dumps({"build": BUILD_VERSION, "profile": s.profile, "rules": s.rules},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def data_stamp(tile: Tile) -> dict:
    """When each input file of the tile was last downloaded/extracted (unix seconds)."""
    out = {}
    for n in DATA_FILES:
        p = tile.cache_path / n
        if p.exists():
            out[n] = int(p.stat().st_mtime)
    return out


def outdated_reasons(stats: dict | None, tile: Tile, profile: str, region: str,
                     fingerprint: str | None = None) -> list[str]:
    """Why a built tile no longer matches the current settings/data ([] = up to date)."""
    if not stats:
        return ["no build record"]
    reasons = []
    if not stats.get("fingerprint"):
        return ["built before ForestStudio tracked settings"]
    if stats.get("profile") != profile:
        reasons.append(f"built with profile '{stats.get('profile')}'")
    elif stats.get("region", region) != region:
        reasons.append(f"built for region '{stats.get('region')}'")
    else:
        fp = fingerprint or settings_fingerprint(Settings.load(profile, region))
        if stats["fingerprint"] != fp:
            reasons.append("profile or forest types changed")
    old, now = stats.get("data", {}), data_stamp(tile)
    changed = [n for n, t in now.items() if old.get(n) and t > old[n] + 1]
    if changed:
        reasons.append("newer data: " + ", ".join(sorted(changed)))
    return reasons


def build_tile(tile: Tile, profile: str = "medium", region: str = "colombia") -> Path:
    """Build one tile. Writes the DSF text and the app's files (overlay, preview, stats) to
    out/studio_<region>/ and returns the text path; compiling puts the .dsf in the pack."""
    from forest.paths import studio_path
    t0 = time.time()
    s = Settings.load(profile, region)
    c = classify(tile, s, t0)
    _log(f"classified: {c['tree_cells']:,} tree cells", t0)
    polys = vectorize(c, s, t0)
    extra = osm_trees(c, s, t0) + town_trees(c, s, t0)
    polys += extra
    stats = budget(polys, tile, s)
    _log(f"budget: {stats['estimated_trees']:,} trees (scale {stats['budget_scale']})", t0)

    ot = OverlayTile(lat=tile.lat, lon=tile.lon, polygons=polys,
                     agent=f"forest-studio {region}/{profile}")
    ot.exclude_for.append(tile.bounds)  # our forests replace everything below
    txt = ot.write_to(studio_path(region, tile, ".txt"))
    preview(c, studio_path(region, tile, ".preview.png"))
    overlay(c, studio_path(region, tile, ".overlay.png"), osm_polys=extra)
    cell_km2 = km2_per_deg2(tile.lat) / c["grid"].cells ** 2
    stats["biome_km2"] = {b["name"]: round(float((c["trees"] & (c["biome_idx"] == i)).sum()) * cell_km2, 1)
                          for i, b in enumerate(c["biomes"]) if (c["trees"] & (c["biome_idx"] == i)).any()}
    stats["scrub_km2"] = round(float(c["scrub"].sum()) * cell_km2, 1)
    stats["town_km2"] = round(float(c["town"].sum()) * cell_km2, 1)
    stats["plantation_km2"] = {pr["name"]: round(float((c["plant_idx"] == i).sum()) * cell_km2, 1)
                               for i, pr in enumerate(c.get("plants", [])) if (c["plant_idx"] == i).any()}
    stats.update(tile=tile.name, profile=profile, region=region, polygons=len(polys),
                 fingerprint=settings_fingerprint(s), data=data_stamp(tile), build_version=BUILD_VERSION,
                 exclusion_cells=c["excl_stats"], seconds=round(time.time() - t0, 1))
    studio_path(region, tile, ".stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    _log(f"wrote {txt}", t0)
    return txt
