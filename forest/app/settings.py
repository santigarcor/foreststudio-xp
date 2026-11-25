"""Profile / rules schema, validation and TOML writing (tomllib only reads)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from forest import config


def profiles_dir() -> Path:
    """The user's profiles (~/.foreststudio/config/profiles), seeded from the defaults."""
    return config.ensure_user_config() / "profiles"


def rules_dir() -> Path:
    return config.ensure_user_config() / "rules"


NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")

# key: (type, min, max, step, group, label, help)
PROFILE_SCHEMA: dict[str, tuple] = {
    "min_tree_frac": (float, 0.2, 0.9, 0.05, "Where trees go", "How much forest a spot needs",
                      "The satellite map says how much of each ~18 m square is trees. A square gets "
                      "trees only if at least this share is forest. 0.5 = half. Lower = more trees, "
                      "also on hedges and lone trees; higher = only solid forest."),
    "recent_loss_from": (int, 1, 24, 1, "Where trees go", "Ignore forest cut since year",
                         "Forest that satellite data shows was cut down from this year on is left "
                         "empty. 21 = since 2021, 1 = since 2001 (removes much more)."),
    "smooth_passes": (int, 0, 5, 1, "Where trees go", "Tidy-up passes",
                      "Each pass removes lone specks of forest and fills tiny holes, so forests "
                      "have cleaner shapes. More passes = rounder, simpler forests, fewer details."),
    "min_area_m2": (int, 0, 30000, 500, "Where trees go", "Smallest forest kept (m²)",
                    "Forest patches smaller than this are dropped. 3000 m² ≈ a 55 × 55 m patch. "
                    "Higher = fewer small groves, better performance."),
    "road_clearance_scale": (float, 0.0, 2.0, 0.1, "Clearances", "Road clearance",
                             "How wide the tree-free strip along roads is. 1.0 ≈ the paved road plus "
                             "~1 m (e.g. 8 m for a residential street, 24 m for a motorway). "
                             "0.5 = half, lets street trees touch the road; 0 = ignore roads."),
    "building_clearance_m": (float, 0.0, 10.0, 0.5, "Clearances", "Building clearance (m)",
                             "Tree-free margin around each building outline. 0 = trees can stand "
                             "right next to walls; 2 m keeps tree crowns off most roofs."),
    "cutout_min_cover": (float, 0.1, 1.0, 0.05, "Clearances", "Clear a spot when roads/buildings cover",
                         "A ~18 m spot loses its trees only if roads, buildings etc. cover at least "
                         "this share of it. 0.5 = half. Lower = clears more (cleaner streets, "
                         "fewer city trees); higher = keeps more trees between buildings."),
    "runway_clearance_m": (int, 0, 200, 5, "Clearances", "Runway clear strip (m)",
                           "Trees are kept this far from a runway's centre line (each side). "
                           "Taxiways and aprons get a ~20 m margin. 75 m ≈ an ICAO runway strip."),
    "clear_airport_grounds": (int, 0, 1, 1, "Clearances", "Clear whole airport grounds (0/1)",
                              "1 = no trees anywhere inside the airport boundary from OpenStreetMap. "
                              "0 = keep real trees on the grounds (only runways, taxiways and aprons "
                              "are cleared)."),
    "osm_green_min_frac": (float, 0.05, 0.9, 0.05, "OpenStreetMap trees", "Tree share needed in parks & golf",
                           "Inside OSM parks, gardens, golf courses and woods, a spot gets trees at this "
                           "lower satellite tree share (normal: 'How much forest a spot needs'). The "
                           "satellite map misses tree lines and scattered trees in these places."),
    "osm_single_trees": (int, 0, 1, 1, "OpenStreetMap trees", "Place OSM single trees (0/1)",
                         "1 = put a tree on every tree mapped individually in OpenStreetMap "
                         "(street trees, gardens, golf). Skipped inside buildings, runway strips and "
                         "existing forest."),
    "osm_tree_rows": (int, 0, 1, 1, "OpenStreetMap trees", "Place OSM tree rows (0/1)",
                      "1 = plant trees along every tree row mapped in OpenStreetMap "
                      "(avenues, windbreaks, field edges)."),
    "scrub_density": (float, 0.0, 1.0, 0.05, "Scrub & town trees", "Scrub thickness",
                      "Bushes on open bushland the satellite map shows (dry Caribbean coast, La Guajira, "
                      "Llanos) and on OSM scrub areas. 0 = no scrub at all; 0.5 = medium; 1 = as thick "
                      "as the bush type allows. Each forest type (biome) picks its own bush type."),
    "scrub_min_frac": (float, 0.2, 0.9, 0.05, "Scrub & town trees", "How much bushland a spot needs",
                       "A ~18 m spot gets bushes only if at least this share of it is bushland on the "
                       "satellite map. Lower = more scrub, also on patchy ground; higher = only "
                       "solid bushland."),
    "town_trees_per_km2": (int, 0, 600, 10, "Scrub & town trees", "Town trees per km²",
                           "Trees scattered through towns and cities, in yards, plazas and along "
                           "streets, never on buildings or roads. 150 ≈ one tree per 80 × 80 m. "
                           "0 = none (only trees mapped in OpenStreetMap)."),
    "town_min_built": (float, 0.1, 0.9, 0.05, "Scrub & town trees", "What counts as town",
                       "A spot counts as town when at least this share of it is built-up on the "
                       "satellite map (OSM residential areas always count; industrial and "
                       "commercial areas never get town trees). Lower = also scattered houses "
                       "and villages."),
    "tile_tree_budget": (int, 1_000_000, 100_000_000, 1_000_000, "Density & performance", "Max trees per tile",
                         "The main frame-rate setting. If a tile would have more trees than this, "
                         "all its forests are thinned evenly until it fits. 30 M is a medium value."),
    "global_density": (float, 0.1, 1.0, 0.05, "Density & performance", "Overall thickness",
                       "Thins every forest by this factor before the max-trees limit. "
                       "1.0 = as thick as X-Plane's forest allows, 0.5 = half the trees."),
    "density_blur_cells": (float, 0.0, 6.0, 0.5, "Density & performance", "Soften thickness changes",
                           "Blends thick and thin forest into each other so there are fewer sharp "
                           "steps. Higher = smoother transitions and fewer forest pieces."),
    "grid_cells": (int, 2000, 12000, 500, "Detail", "Map detail",
                   "How finely the tile is analysed. 6000 = squares of ~18 m. Higher = forest "
                   "edges follow roads and fields more closely, but bigger files and slower builds."),
    "simplify_m": (int, 0, 40, 1, "Detail", "Edge smoothing (m)",
                   "Straightens forest edges by up to this distance, removing the blocky "
                   "'staircase' look. About 2/3 of the map detail square works well (12 m at 6000)."),
    "chunks_per_degree": (int, 4, 32, 4, "Detail", "Split forests into pieces",
                          "Big forests are cut into square pieces so X-Plane can skip the ones you "
                          "can't see. 16 = pieces of ~7 km. Rarely needs changing."),
}
# How much each setting changes frame rate in X-Plane (trees drawn), shown as a badge.
# high = directly sets the number of trees; medium = changes forest area or tree count a bit;
# low = small effect; none = only file size / loading / build time.
FPS_IMPACT = {
    "tile_tree_budget": "high",
    "global_density": "high",
    "min_tree_frac": "medium",
    "min_area_m2": "low",
    "smooth_passes": "low",
    "road_clearance_scale": "low",
    "building_clearance_m": "low",
    "cutout_min_cover": "low",
    "density_blur_cells": "low",
    "density_levels": "low",
    "recent_loss_from": "low",
    "osm_green_min_frac": "low",
    "osm_single_trees": "low",
    "osm_tree_rows": "low",
    "scrub_density": "medium",
    "scrub_min_frac": "medium",
    "town_trees_per_km2": "medium",
    "town_min_built": "low",
    "runway_clearance_m": "none",
    "clear_airport_grounds": "none",
    "grid_cells": "none",
    "simplify_m": "none",
    "chunks_per_degree": "none",
}

DENSITY_LEVELS_HELP = (
    "Forest thickness is rounded to these steps (1.0 = full). Three steps "
    "(0.35, 0.65, 1.0) means thin, medium and thick forest. Fewer steps = fewer forest "
    "pieces and better performance; more steps = finer variation."
)


def list_profiles() -> list[str]:
    return sorted(p.stem for p in profiles_dir().glob("*.toml"))


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _num(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"{v:_}" if abs(v) >= 100_000 else str(v)
    return repr(round(float(v), 4))


def validate_profile(data: dict) -> dict:
    from forest.build import PROFILE_DEFAULTS

    data = {**PROFILE_DEFAULTS, **data}
    out: dict = {}
    for key, (typ, lo, hi, _step, *_rest) in PROFILE_SCHEMA.items():
        if key not in data:
            raise ValueError(f"missing {key}")
        v = typ(data[key])
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        out[key] = v
    levels = sorted(float(x) for x in data.get("density_levels", []))
    if not levels or not all(0 < x <= 1 for x in levels) or len(levels) > 8:
        raise ValueError("density_levels: 1-8 values in (0, 1]")
    out["density_levels"] = levels
    return out


def write_profile(name: str, data: dict) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("profile name: lowercase letters, digits, - and _")
    d = validate_profile(data)
    lines = ["# Forest Studio performance profile (written by the app).", ""]
    for key, (_t, _lo, _hi, _s, _g, label, help_) in PROFILE_SCHEMA.items():
        lines.append(f"{key} = {_num(d[key])}  # {label}: {help_}")
    lines.append("density_levels = [" + ", ".join(_num(x) for x in d["density_levels"]) + "]")
    path = profiles_dir() / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _validate_bands(bands_in: list, evergreen: set[str], where: str) -> list[dict]:
    bands, last = [], -1e9
    for b in bands_in:
        name = str(b["name"]).strip() or "band"
        em = float(b["elev_max"])
        if em <= last:
            raise ValueError(f"{where}: band upper elevations must increase")
        last = em
        for k in ("dense", "sparse"):
            if b[k] not in evergreen:
                raise ValueError(f"{where} / {name} / {k}: {b[k]} is not an allowed forest type")
        bands.append({"name": name, "elev_max": em, "dense": b["dense"], "sparse": b["sparse"]})
    if not bands:
        raise ValueError(f"{where}: at least one band")
    return bands


def validate_rules(data: dict, evergreen: set[str]) -> dict:
    sb = float(data["sparse_below"])
    if not 0 <= sb <= 1:
        raise ValueError("sparse_below must be 0-1")
    biomes_in = data.get("biome") or [{"name": "default", "label": "All", "color": [60, 160, 70],
                                       "ecoregions": [], "band": data.get("band", [])}]
    biomes, seen = [], set()
    for bm in biomes_in:
        name = str(bm["name"]).strip()
        if not NAME_RE.match(name.replace(" ", "_")):
            raise ValueError(f"biome name {name!r}: lowercase letters, digits, - and _")
        if name in seen:
            raise ValueError(f"duplicate biome {name}")
        seen.add(name)
        color = [max(0, min(255, int(x))) for x in bm.get("color", [60, 160, 70])][:3]
        ecos = [str(e) for e in bm.get("ecoregions", [])]
        scrub = str(bm.get("scrub") or "")
        if scrub and scrub not in evergreen:
            raise ValueError(f"{name} / scrub: {scrub} is not an allowed forest type")
        town = str(bm.get("town_tree") or "")
        if town and town not in evergreen:
            raise ValueError(f"{name} / town trees: {town} is not an allowed forest type")
        entry = {"name": name, "label": str(bm.get("label", name)), "color": color,
                 "ecoregions": ecos, "scrub": scrub, "scrub_on_bare": bool(bm.get("scrub_on_bare")),
                 "town_tree": town, "band": _validate_bands(bm["band"], evergreen, name)}
        resolve = [str(e) for e in bm.get("resolve_biomes", []) if str(e).strip()]
        if resolve:   # only when used, so older rules files (Colombia) keep their fingerprint
            entry["resolve_biomes"] = resolve
        if "climate" in bm:   # likewise (absent = off, or the template's value for added regions)
            entry["climate"] = bool(bm["climate"])
        biomes.append(entry)
    if "default" not in seen:
        raise ValueError('a biome named "default" is required (used for unlisted ecoregions)')
    single = data.get("single_tree") or biomes[0]["band"][0]["dense"]
    if single not in evergreen:
        raise ValueError(f"single_tree: {single} is not an allowed forest type")
    town_tree = data.get("town_tree") or single
    if town_tree not in evergreen:
        raise ValueError(f"town_tree: {town_tree} is not an allowed forest type")
    plants, pseen = [], set()
    for pr in data.get("plantation", []):
        name = str(pr["name"]).strip()
        if not NAME_RE.match(name) or name in pseen:
            raise ValueError(f"plantation name {name!r}: unique, lowercase letters, digits, - and _")
        pseen.add(name)
        asset = str(pr.get("asset") or "")
        if asset and asset not in evergreen:
            raise ValueError(f"plantation {name}: {asset} is not an allowed forest type")
        match = [str(m).strip() for m in pr.get("match", []) if str(m).strip()]
        if not match or not all("=" in part for m in match for part in m.split("&")):
            raise ValueError(f"plantation {name}: match entries must look like key=value (join with &)")
        dens = float(pr.get("density", 0.7))
        if not 0 <= dens <= 1:
            raise ValueError(f"plantation {name}: density must be 0-1")
        color = [max(0, min(255, int(x))) for x in pr.get("color", [230, 120, 40])][:3]
        plants.append({"name": name, "label": str(pr.get("label", name)), "color": color,
                       "match": match, "asset": asset, "density": dens})
    return {"sparse_below": sb, "single_tree": single, "town_tree": town_tree,
            "plantation": plants, "biome": biomes}


def _q(s: str) -> str:
    """A TOML string (a label with a newline or control character must not break the file)."""
    from forest.config import toml_str
    return toml_str(s)


def write_rules(region: str, data: dict, evergreen: set[str]) -> Path:
    if not NAME_RE.match(region):
        raise ValueError("region name: lowercase letters, digits, - and _")
    d = validate_rules(data, evergreen)
    try:
        from forest import regions
        only_evergreen = regions.get(region).evergreen_only
    except ValueError:
        only_evergreen = True
    must = " (must be evergreen)" if only_evergreen else ""
    lines = [
        "# Forest types: biome (RESOLVE Ecoregions 2017) x elevation band (written by the app).",
        ("# Only evergreen assets: XP12 season rasters can put tropical highlands in 'winter'."
         if only_evergreen else "# Seasonal assets allowed in this region."),
        "# An ecoregion uses the biome that lists it by name, else the first biome whose",
        "# resolve_biomes holds its RESOLVE biome, else the \"default\" biome.",
        "",
        f"sparse_below = {_num(d['sparse_below'])}",
        "",
        f"# Asset for OSM single trees and tree rows{must}.",
        f"single_tree = {_q(d['single_tree'])}",
        "",
        f"# Town trees where a biome sets no town_tree{must}.",
        f"town_tree = {_q(d['town_tree'])}",
        "",
        "# Per biome: climate = pick X-Plane's forest by satellite leaf type and climate map",
        "# (elevation bands then only a fallback), scrub = bush type (\"\" = none), scrub_on_bare = also on bare land,",
        "# town_tree = town tree type (\"\" = the one above).",
        "",
        "# Plantations from OpenStreetMap: first matching rule wins; match = key=value, & joins",
        "# conditions; asset = \"\" means no trees.",
    ]
    for pr in d["plantation"]:
        lines += ["", "[[plantation]]", f"name = {_q(pr['name'])}", f"label = {_q(pr['label'])}",
                  f"color = [{', '.join(str(x) for x in pr['color'])}]",
                  "match = [" + ", ".join(_q(m) for m in pr["match"]) + "]",
                  f"asset = {_q(pr['asset'])}", f"density = {_num(pr['density'])}"]
    for bm in d["biome"]:
        lines += ["", "[[biome]]", f"name = {_q(bm['name'])}", f"label = {_q(bm['label'])}",
                  f"color = [{', '.join(str(x) for x in bm['color'])}]",
                  "ecoregions = [" + ", ".join(_q(e) for e in bm["ecoregions"]) + "]",
                  *(["resolve_biomes = [" + ", ".join(_q(e) for e in bm["resolve_biomes"]) + "]"]
                    if bm.get("resolve_biomes") else []),
                  *([f"climate = {_num(bm['climate'])}"] if "climate" in bm else []),
                  f"scrub = {_q(bm['scrub'])}", f"scrub_on_bare = {_num(bm['scrub_on_bare'])}",
                  f"town_tree = {_q(bm['town_tree'])}"]
        for b in bm["band"]:
            lines += ["[[biome.band]]", f"name = {_q(b['name'])}", f"elev_max = {_num(b['elev_max'])}",
                      f"dense = {_q(b['dense'])}", f"sparse = {_q(b['sparse'])}"]
    path = rules_dir() / f"{region}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
