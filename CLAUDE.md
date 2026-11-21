# ForestStudio XP

A standalone Windows desktop app (Python backend + HTML/JS UI in a pywebview window) that builds
**X-Plane 12 forest overlay scenery** from satellite land cover, biome maps and OpenStreetMap,
starting with **Colombia**. It is meant to become an open-source project (repo:
https://github.com/santigarcor/foreststudio-xp, license GPL-3.0); more regions will be added later
(**do not add other regions unless asked**).

`PLAN.md` is the detailed historical log of every decision, measurement and finding (dated). This
file is the authoritative summary; keep both up to date when something important changes.

---

## Hard rules (from the project owner — always follow)

1. **Never modify installed X-Plane scenery or libraries** (simHeaven, airports, Global Scenery,
   default libraries…). ForestStudio only *reads* the X-Plane install. The one thing it creates
   there is its own folder link `Custom Scenery/forest_<region>` (a directory junction).
2. **`scenery_packs.ini`**: change it only when the user asks. Back it up first
   (`~/.foreststudio/cache/backups/scenery_packs.ini.<timestamp>`) unless the user explicitly says not to.
3. **Colombia uses evergreen forest assets only** (no `EXPORT_SEASON` variants, no `SEASONAL`
   textures). XP12's season rasters put the Andean highlands (1,500–2,500 m, e.g. Rionegro) in
   "winter" for ~6 months, so seasonal assets show bare trees. Colombia has no seasonal trees.
   **Palms are fine** (they exist in Medellín and Rionegro). The build refuses seasonal assets.
4. **No console windows.** Every helper program (DSFTool, CLI workers) is started hidden via
   `forest/proc.py` (`CREATE_NO_WINDOW` + `SW_HIDE`). Never call `subprocess` directly.
5. **The UI is a standalone desktop app like OrthoStudio-XP** (pywebview), **not a web app**.
   The HTTP dev server (`forest/app/devserver.py`) exists only to test the UI in a browser.
6. **Settings must use plain language** (label + help text a non-expert understands) and carry
   **FPS-impact marks** (⚡⚡ high / ⚡ medium / low / none). Forest types show their FPS cost.
7. Tests and experiments must never touch the user's real data or X-Plane (use the `sandbox`
   fixture / temp folders). Commit or push only when the user asks.
8. The owner is Spanish/English bilingual, flies over Colombia (SKRG Rionegro, SKMD Olaya Herrera,
   SKCL Cali, SKCG Cartagena, SKSM Santa Marta, SKSP San Andrés) and uses OrthoStudio ortho tiles
   plus simHeaven X-World America.

---

## How to run

```powershell
# first time
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
# the desktop app (no console window)
.venv\Scripts\foreststudio-xp.exe        # or double-click "ForestStudio XP.lnk" (per-machine shortcut)
# tests
.venv\Scripts\python -m pytest -q        # all (≈2 s);  -m "not integration" for the fast ones
```

- Python 3.11+ (this PC: 3.15; all deps have 3.15 wheels). Windows is the target platform.
- **DSFTool is bundled** for all three systems (XPTools 24-5, DSFTool 2.4.0-b1, Laminar Research,
  **MIT/X11** — see `tools/xptools/NOTICE.md`): `tools/xptools/windows/DSFTool.exe`,
  `macos/DSFTool` (universal x86-64 + arm64), `linux/DSFTool` (x86-64). `config.PLATFORM_DIR` picks
  one; git keeps them executable (100755) and `config._ensure_executable` re-adds +x if a zip lost it.
  No DSFTool setting in the UI (a hand-set `[tools] dsftool` in local.toml still wins if it exists).
- macOS/Linux: the pack link is a **symlink** (`pack.make_link` / `remove_link`; junction on
  Windows); X-Plane detection also looks in `~/.local/share/Steam`, `/Applications/X-Plane 12`.
  Not tested on real Macs/Linux yet, only in CI.
- User README: `README.md` (install, first run, scenery order, views, credits). Keep it in sync with
  user-visible changes; `CLAUDE.md` is the technical summary.
- X-Plane location: **Settings** view (or auto-detected: `%LOCALAPPDATA%\x-plane_install_12.txt`,
  Steam `libraryfolders.vdf`, common folders). Saved in `~/.foreststudio/config/local.toml`.
- First run without a valid X-Plane folder opens the Settings view automatically.

### Data folder (default `~/.foreststudio`; Settings → Data folder, or `FORESTSTUDIO_HOME`)

The repository holds only code, the bundled DSFTool and the **default** profiles/rules. Everything
that changes lives in `config.DATA_ROOT` (`%USERPROFILE%/.foreststudio`):

| Path | What |
|---|---|
| `config/local.toml` | X-Plane location (Settings view) |
| `config/profiles/*.toml`, `config/rules/*.toml` | the user's profiles / forest types; missing files are copied from the repo defaults on first use (`config.ensure_user_config`, never overwrites) |
| `config/catalog_xp12.json` | forest catalog generated from this X-Plane |
| `cache/` | `tiles/<tile>/`, `osm/` (country .pbf), `ecoregions/`, `jobs/`, `history.jsonl`, `backups/`, `decoded/` |
| `out/` | `forest_<region>/` (the pack, junction target) and `studio_<region>/` (overlays, stats) |

Use `config.CACHE_ROOT / OUT_ROOT / USER_CONFIG / profile_path() / rules_path()`; never write under
`REPO_ROOT`. `forest/migrate.py` moves the old in-repo `cache/`, `out/`, `config/local.toml` and
catalog into the data folder on start (app and CLI), never overwriting, and re-points our own
`Custom Scenery/forest_<region>` junction. Done on this PC 2026-11-21 (3.0 GB, 34 tiles).

Changing it: `config.resolve_data_root()` = `FORESTSTUDIO_HOME` (locks the setting) > the folder named
in `~/.foreststudio/location.toml` (written by Settings; the only file left in the default folder) >
the default. `forest/datafolder.py` checks a target (not inside the current folder, X-Plane or the
program folder; drive present and writable; free space when crossing drives; a non-empty foreign
folder gets a `ForestStudio` subfolder; a folder that already has data is *switched to*, nothing
moved), moves every item without overwriting, re-points our junctions, writes `location.toml`, and
the app restarts (`Api.restart_app`) because modules read their folders once at import.

CLI (used by the app's job runner; handy for debugging):

| Command | What it does |
|---|---|
| `forest catalog` | (Re)build `~/.foreststudio/config/catalog_xp12.json` from X-Plane's `1200 forests` library |
| `forest fetch --tiles +06-076,+06-075` | Satellite rasters per tile (4 downloads at a time) + OSM in **one** pass; downloads the OSM country file / ecoregions zip first if missing |
| `forest data osm [--update] [--force]` | Download / update the Geofabrik country file (only if the server's is newer), then re-extract every tile that has `osm.json` |
| `forest data ecoregions [--update]` | Download the RESOLVE zip and make the Colombia cut-out |
| `forest build --tile +06-076 --profile medium --region colombia --compile [--keep-text] [--install]` | Build one tile; `--install` ensures the pack link exists |
| `forest decode <file.dsf> [--label x]` | DSF → text into `<data>/cache/decoded/` (debug) |
| `forest poc …` | Phase-0 proof of concept (three test squares near SKRG) |
| `forest app` | Open the desktop app |

---

## Architecture

```
forest/
  config.py        data-folder paths, local.toml load/save, X-Plane & DSFTool auto-detect + validation
  migrate.py       one-time move of old in-repo data to ~/.foreststudio (+ junction re-point)
  datafolder.py    Settings → Data folder: check / move / switch, re-point links, location.toml
  credits.py       data + software credits (Settings → About) and the pack's CREDITS.txt
  tiles.py         Tile (1°×1°, name "+06-076", 10° folder "+00-080"), Grid (north-up raster)
                   Tile.cache creates the folder (write); Tile.cache_path never does (read/check)
  paths.py         <data>/out/forest_<region>/Earth nav data/…/<tile>.dsf (the pack) vs
                   out/studio_<region>/<tile>.{txt,stats.json,overlay.png,preview.png}
  fetch/
    sources.py     country-wide files: Geofabrik .pbf + RESOLVE zip — download (.part → rename,
                   `<file>.json` meta: server date/size/downloaded), status, check_update (HEAD)
    rasters.py     ESA WorldCover 2021 (COG windows), Hansen GFC v1.12 treecover2000 + lossyear
    osm_pbf.py     ONE pass over the Geofabrik .pbf → streams elements into every requested tile's
                   osm.json (format VERSION 3; old versions re-extracted automatically)
    osm.py         dispatch: local .pbf (preferred) else Overpass (fallback, unreliable)
    elevation.py   elevation from X-Plane's own base mesh via DSFTool (no download)
    ecoregions.py  RESOLVE Ecoregions 2017 → clipped Colombia GeoJSON → per-tile raster
  exclusions.py    OSM cut-outs (roads, buildings, water, runways, rail/power) + tree hints
                   (single trees, tree rows, parks/golf/woods, plantations)
  build.py         classify → vectorize → OSM trees → budget → DSF text (+ overlay/preview/stats)
  dsf/text.py      DSFTool text writer (OverlayTile, ForestPolygon, season rasters)
  assets/catalog.py  X-Plane forest catalog (seasons, spacing → trees/km², triangles, species)
  xplane/          library.txt and .for parsers (skip mesh data, count triangles)
  pack.py          install/uninstall the region pack as ONE directory junction
  jobs.py          saved build jobs (Build queue view): steps, timings, results, logs
  history.py       measured seconds/MB/peak RAM per step → estimates
  resources.py     parallel builds = min(physical cores − 1, (free RAM − 2 GB) / peak GB, 8)
  diskusage.py     disk usage by category + safe clean-ups
  proc.py          hidden subprocess helpers (no console windows)
  cli.py           command line
  app/             pywebview desktop app: main.py, api.py (js_api), settings.py (schemas,
                   validation, TOML writers), devserver.py (dev only), ui/{index.html,app.css,app.js,views.js}
config/                             DEFAULTS shipped with the app (user copies live in ~/.foreststudio/config)
  profiles/{low,medium,high}.toml   performance profiles
  rules/colombia.toml               biomes, elevation bands, plantations, single-tree asset
  local.example.toml                template for ~/.foreststudio/config/local.toml
tools/xptools/                      bundled DSFTool + license notes
tests/                              pytest suite (sandboxed)
```

### The build pipeline (per tile)

1. **Data**: WorldCover 10 m (classes; trees = 10 **and 95 mangrove**), Hansen tree cover % and
   loss year (30 m), OSM features, elevation from the base mesh, ecoregion polygons.
2. **Classify** on a grid of `grid_cells` per degree (medium 6000 ≈ 18.5 m):
   - tree cell = WorldCover tree share ≥ `min_tree_frac` (≥ `osm_green_min_frac` inside OSM
     parks/gardens/golf/woods), minus Hansen loss from `recent_loss_from`;
   - `smooth_passes` × 3×3 majority filter (mangrove cells exempt, so shore fringes survive);
   - cut-outs drawn at 2× detail, a cell cleared only if they cover ≥ `cutout_min_cover`; water is
     **not** cut where WorldCover says mangrove;
   - density = 0.6 × WorldCover share + 0.4 × Hansen, gaussian blur, quantised to `density_levels`;
   - **biome** per cell from ecoregions; WorldCover mangrove class overrides to the `mangrove`
     biome; asset = that biome's elevation band × dense/sparse (`sparse_below`);
   - **plantations** (OSM orchards/plantations/farmland/needleleaved forests) replace the biome
     asset inside their outline and are planted regardless of satellite tree share; rules with
     `asset = ""` clear trees (sugar cane, pineapple, rice…). First matching rule wins.
   - **scrub**: cells with WorldCover shrubland (20) share ≥ `scrub_min_frac` (+ bare/sparse
     class 60 in biomes with `scrub_on_bare`, i.e. xeric/La Guajira; OSM `natural=scrub|heath`
     counts as 0.7), majority-filtered, never on forest/plantations/cut-outs/mangroves, only in
     biomes with a `scrub` asset (páramo has none); thickness = share × `scrub_density` → nearest
     density level; vectorised and budgeted with the forests. Grassland (30) is **not** used: in
     Colombia it is mostly cattle pasture.
   - **town** cells: WorldCover built-up (50) share ≥ `town_min_built` or OSM `landuse=residential`,
     minus OSM industrial/commercial/retail, forest, plantations and water.
3. **Vectorize** per (asset, density level) inside `chunks_per_degree` windows, simplify
   (`simplify_m`), drop < `min_area_m2`; **split polygons with > 250 holes** (DSF limit: 255
   windings per polygon — "too many intervals in polygon").
4. **OSM trees**: `natural=tree` → point forests (fill 512, grouped per chunk); `natural=tree_row`
   → line forests (fill 256); skipped inside buildings, runway strips and existing forest; never
   thinned by the budget. **Town trees** (`build.town_trees`): Poisson-random points in town cells
   (`town_trees_per_km2`, seeded by tile name so rebuilds are identical), dropped if they hit a
   building (with clearance), road, runway/taxiway or rail/power corridor (one bulk STRtree query);
   point forests per (biome `town_tree` or rules `town_tree`, chunk), ≤ 1000 points per polygon;
   `ForestPolygon.kind = "town"` keeps them apart in the stats; never thinned by the budget.
5. **Budget**: estimated trees = area × density × global_density × trees/km²; scaled down to
   `tile_tree_budget`. DSF density = 0–255.
6. **Write** DSF text (tile-wide `sim/exclude_for` so our forests replace everything below),
   overlay/preview PNGs, stats JSON → **compile** with DSFTool to `<tile>.building.dsf` then atomic
   swap into the pack (X-Plane never sees a half-written tile); text deleted unless `--keep-text`.
7. **Outdated detection**: stats JSON records `profile`, `region`, `fingerprint` (sha256 of profile +
   rules + `build.BUILD_VERSION`) and `data` (mtimes of worldcover/treecover/lossyear/osm.json).
   `build.outdated_reasons()` compares with the current profile/rules/data; `tile_status(..., profile)`
   returns `outdated` reasons → orange outline on the map, **Select outdated**, Tile info "Up to date".
   Tiles built before fingerprints count as outdated. **Bump `BUILD_VERSION`** when a code change
   alters results so every older tile shows up.

### Install model

- The region pack `~/.foreststudio/out/forest_<region>` is linked into X-Plane as **one directory junction**
  `Custom Scenery/forest_<region>` (like OrthoStudio; a symlink on macOS/Linux). No admin rights needed. Every built tile is
  in X-Plane immediately; uninstall removes only the junction. File symlinks are not possible on
  this PC (need Developer Mode/admin); hard links were used briefly and replaced by the junction.
- An existing *real* pack folder is absorbed: tiles only in X-Plane move into the project,
  duplicates are removed, anything that isn't a `.dsf` (or our `CREDITS.txt`) makes it refuse.
- **Scenery order** (owner's ini): `forest_colombia` sits **below** hand-made airport/city packs
  (they have their own trees our exclusion would delete) and **above** simHeaven
  `X-World_America-4-extras` and `7-forests`, so on built tiles our tile-wide exclusion hides
  X-World's forests, random trees, hedges and `osm_tree`; unbuilt tiles keep X-World's forests
  (7-forests stays **enabled**). Expect style seams at 1° borders.
- New tiles need an X-Plane restart; rebuilt tiles show with Developer → Reload Scenery.

### Jobs / parallel builds

App job = `fetch --tiles all` (satellite downloads ×4 in parallel, OSM one pass) then a pool of
N `build` subprocesses (N = Auto from `resources.auto_workers()`, measured peak ≈ 4.6 GB RAM for
Medellín, 2.8 GB San Andrés; this PC 8 cores / 33 GB → Auto 3). Logs tagged `[tile]`; a failed
tile doesn't stop others; cancel terminates all. Every job is saved in `<data>/cache/jobs/<id>.{json,log}`
with per-tile steps **Data → Classify → Vectorise → Compile** (advanced by parsing log lines in
`JobRecord.track`), results and a report; jobs left running by a closed app become "interrupted".
Measured: 6 tiles in 89 s with 3 workers vs 218 s serial.

Download jobs use the same runner and queue (`Api.start_data_job`): kind `data` (fetch for tiles
selected on the map, or the ecoregions zip) and `osm_update` (`forest data osm --update`, then
re-extract every tile with `osm.json`). Their records have `kind` and `step_names = ["data"]`;
old records without them load as builds. Only one job (build or download) runs at a time.

---

## The app (views)

- **Build**: Leaflet map (Esri satellite / OSM) with the 1° grid; tile status colours (cached,
  built, installed, selected; **outdated** = orange; no ortho status — not relevant); click/shift-click selection
  (no "select all": the owner selects by hand); **Select outdated**; forest overlay (colour = biome
  or plantation, lilac = scrub, brightness = density, red = cut out, yellow = OSM trees, light blue =
  town trees) with legend; sidebar tabs:
  - **Tiles**: selection, **X-Plane pack** (Install/Uninstall pack, ini warnings), Build with
    "Builds at the same time: Auto (N)/1…max", time & disk **estimate** (from `history.jsonl`
    medians), confirmation for ≥10 tiles or >30 min, Delete selected tiles (built tiles and/or
    downloaded data), Tile info (status, last build stats, forest by biome, plantations, types).
  - **Profile**: every profile value as slider + help + FPS mark; Reset to defaults (shipped values via
    `Api.default_profile`; own profiles reset to medium; not saved until Save) / Revert / Save as / Save.
  - **Forest types**: sparse_below, OSM single-tree type, general town-tree type, per-biome
    **Scrub & town trees** (bush type or none, "also on bare ground", town tree type), **Plantations** editor (tags, tree type,
    thickness, reorder), **Biomes** (colour, label, ecoregion chips add/remove, per-biome
    elevation bands with dense/sparse type pickers sorted lightest first with FPS cost); **Reset to defaults** loads the shipped
    rules (`Api.default_rules`), saved only with Save).
  - (No log tab: the Tiles tab shows progress + "Open in Build queue"; the live log is in the Build queue.)
- **Build queue** (internal id `works`; the open log keeps updating without closing): saved jobs (search by tile), progress, per-tile step bars, log, final report,
  per-step table, errors; delete job / clear all. Download jobs show as "Download data" /
  "Update OSM data".
- **Data**: cards for the OSM country file (size, map-data date, downloaded date; Download /
  Check for update → Update OSM data), ecoregions (Download), satellite (fixed releases, nothing to
  update); table of tiles with data (satellite / OSM date, flagged when older than the country file /
  elevation / size / built) with Download for map selection, Select checked on map, Delete checked data.
- **Disk space**: usage by category (tile data, OSM country file, ecoregions, built pack, app
  files, job logs, leftovers), largest tiles, clean-ups (leftovers, data of unbuilt tiles, job history).
- **Settings**: X-Plane 12 folder (Browse / Detect / live validation / Save); **Data folder** (Browse /
  Use default / live check / Move data here → Restart now). **No DSFTool setting**: the bundled one is used (`config.BUNDLED_DSFTOOL`); a hand-set `[tools] dsftool` in local.toml still overrides it if that file exists. Plus
  setup checks, **About** (credits from `forest/credits.py`). Status bar: X-Plane path, checks (n/5),
  data kept, running job.
- Header logo + favicon: `forest/app/ui/icon.svg` (copy of `forest/app/icons/foreststudio-xp.svg`; keep in sync).
- UI convention: any button that calls a slow API goes through `busy(btn, "Working…", fn)` in
  `views.js` (spinner + disabled while it runs).

---

## Data sources (all free; attribution required — see `forest/credits.py`, README, pack CREDITS.txt)

The OSM country file and the ecoregions zip are downloaded automatically on the first fetch/build
(`cli._ensure_country_files`) or from the Data view; per-tile rasters on every fetch.

| Data | Source | Notes |
|---|---|---|
| Land cover | ESA WorldCover 2021 v200, 10 m (AWS COG `…/ESA_WorldCover_10m_2021_v200_N06W078_Map.tif`, 3° tiles) | only the 1° window is read |
| Tree cover / loss | Hansen GFC v1.12 (Google Storage, 10° tiles, strip-organised) | rows of the tile fetched |
| OSM | Geofabrik `colombia-latest.osm.pbf` (~335 MB) in `<data>/cache/osm/` | ODbL; Overpass fallback timed out on whole tiles |
| Biomes | RESOLVE Ecoregions 2017 (`storage.googleapis.com/teow2016/Ecoregions2017.zip`, 149 MB, CC-BY 4.0) | clipped to `<data>/cache/ecoregions/colombia.geojson` |
| Elevation | X-Plane 12 base mesh (`Global Scenery/…/+06-076.dsf`) via DSFTool | rows south→north in the raw raster |
| Rejected | Meta/WRI 1 m canopy height (≈160 GB for Colombia, no overviews); ETH 10 m canopy (server rate-limited; parked) | tree height is parked |

---

## X-Plane facts learned (verified on this install)

- Default forests: `Resources/default scenery/1200 forests/library.txt` exports 383 virtual
  forests. Public set `lib/vegetation/forests/{broadleaves,conifers,mixed}/{cold_low,…,very_hot_dry}`
  and single species `lib/vegetation/trees/…` (palms, kapok, Brazil nut, banana, pine, cypress…);
  global-scenery set `lib/g8/<type>[_sp]_<temp>_<moisture>.for` (SEMI_DEPRECATED but used by the
  base mesh; only home of rainforest and tropical orchards).
- Seasons are `EXPORT_SEASON spr|sum|fal|win`; very_hot / hot_dry / rainforest are plain EXPORT
  (evergreen). Max trees/km² at density 255 = 1e6 / (SPACING x·z); FPS cost ≈ trees/km² ×
  triangles/tree (MESH line: name near far nverts **nidx** → tris = nidx/3).
- DSF: forest param = density (0–255) + fill (0 solid, 256 line, 512 points); CCW exteriors, CW
  holes; `sim/exclude_for` is rectangle-only `w/s/e/n` and hides forests of packs *below*;
  **max 255 windings per polygon**; text header `A`/`I`, `800`, `DSF2TEXT`; `sim/west|east|north|south`
  must be the last properties.
- XP12 season rasters `spr1 … win2` (128×128, 1 byte, scale 1.994536 = day of year, south→north).
  Base mesh gives temperate-style seasons to Andean highlands (1,500–2,500 m) → rule 3.
- simHeaven X-World: `7-forests` (OSM forests, mostly `mixed.for`), `4-extras` (27k `random_tree`
  areas/tile, hedges, shrubs → the "orange city trees"), `6-scenery` (`osm_tree`), Vegetation
  Library maps `simheaven/forests/*` by 5 climate bitmaps (vhot defines all seasons with palms).
  `Tropical_Trees_Fix` remaps simheaven names (disabled by the owner).
- OrthoStudio ortho tiles replace the base mesh and contain no forests (forests then come only
  from overlays — so our tiles must cover the whole tile).
- Medellín City pack = landmark buildings only; SKMD/SKRG airport packs have their own trees.

---

## Rules file (`config/rules/colombia.toml`)

- `sparse_below`, `single_tree` (asset for OSM single trees/rows), `town_tree` (town trees where
  a biome sets none; very_hot.for = palms + kapok + broadleaves).
- Per biome (optional): `scrub` (bush asset, "" = none), `scrub_on_bare` (bool), `town_tree`.
  Keys missing in a user's older rules file are filled from the shipped default biome of the same
  name (`build.fill_rule_defaults`, also used by `Api.get_rules`); an explicit "" is kept.
- `[[plantation]]` name, label, color, `match = ["key=value", "a=b&c=d"]` (case-insensitive,
  first match wins), `asset` ("" = no trees), `density`. Current: oil_palm (date palm),
  coconut, banana, coffee (shrubs + shade), rubber, conifer (pine), eucalyptus, no_trees, orchard.
- `[[biome]]` name, label, color, `ecoregions = [...]`, `[[biome.band]]` name, elev_max, dense,
  sparse. Biomes: rainforest, montane, dry_forest, xeric, savanna (Llanos), mangrove (**no
  ecoregions — WorldCover class 95 only**; RESOLVE mangrove polygons reach far inland and were
  moved to dry_forest/rainforest), paramo, **default** (required, catch-all, last).
  **xeric** forest uses real dry-forest trees (`broadleaves/hot_dry` / `g8/broad_sp_hot_sdry`), not
  shrubs: the RESOLVE "Guajira-Barranquilla xeric scrub" ecoregion also covers the Sucre/Bolívar/
  Magdalena dry forests, and shrub types there put bushes on real tree canopy (+09-076: 1,054 km²).
  Bushes come only from xeric's `scrub` (shrubland/bare land).
- Saved by the app via `forest/app/settings.py` (validated: evergreen assets, increasing bands,
  unique names, default biome present, match syntax).

## Profile keys (`config/profiles/*.toml`)

grid_cells, min_tree_frac, recent_loss_from, smooth_passes, min_area_m2, road_clearance_scale,
building_clearance_m, cutout_min_cover, runway_clearance_m, clear_airport_grounds,
osm_green_min_frac, osm_single_trees, osm_tree_rows, tile_tree_budget (main FPS knob),
global_density, density_blur_cells, simplify_m, chunks_per_degree, density_levels,
scrub_density (0 = off), scrub_min_frac, town_trees_per_km2 (0 = off), town_min_built.
Keys added later have defaults in `build.PROFILE_DEFAULTS` (older profile files still build/load).
Schema, ranges, help texts and FPS marks: `forest/app/settings.py` (`PROFILE_SCHEMA`, `FPS_IMPACT`).
The owner currently uses medium with `tile_tree_budget` ≈ 25–27M.

---

## Testing

- `tests/conftest.py` → `sandbox` fixture uses a temp code folder (`ns.repo`) and a temp data folder (`ns.root`) and redirects config paths, cache, out, jobs, history,
  profiles/rules, OSM file, ecoregions dir and the X-Plane folder to `tmp_path`.
- Unit tests: tiles/paths, DSF writer (winding, params, bounds, rasters), polygon splitting,
  plantation matching, biomes, exclusions & coverage, settings validation/round-trips, catalog
  parsing (synthetic library + .for), **one-pass OSM extraction (real pyosmium on a tiny .osm)**,
  jobs lifecycle, config detection/save, resources/history, hidden processes, disk usage and safe
  deletion, pack link/unlink/absorb (junction or symlink), data-folder move/switch, migration,
  country-file download (local HTTP server) / update check, download jobs, outdated detection,
  pack credits.
- Integration (`-m integration`): the real pipeline on a synthetic tile (WorldCover/Hansen
  GeoTIFFs, OSM, ecoregions, elevation) → stats, biomes, plantations, overlay, ≤255 windings; refuses
  seasonal assets; compiles with the bundled DSFTool (`-m dsftool`).
- **CI**: `.github/workflows/tests.yml` runs the suite on Windows (Python 3.11, 3.13), Ubuntu and
  macOS (3.12) on every push/PR — the only place the Linux/macOS DSFTool and symlinks get exercised.
- UI check: `.claude/launch.json` → `forest-ui-dev` (dev server on :8765); the real window was
  smoke-tested with pywebview `evaluate_js`.

## Conventions

- Keep the X-Plane pack folder clean (only `.dsf` + our `CREDITS.txt`, which the data licenses
  require; written on every compile and on Install pack); app files go to `<data>/out/studio_<region>/`.
- New per-tile data → `Tile.cache` (write) / `Tile.cache_path` (read). Bump `osm_pbf.VERSION` (now 4)
  when extracted OSM features change.
- Record measurements with `history.record(...)` so estimates stay accurate.
- Large inline edits: prefer the Edit tool; Python patch scripts via heredoc break on Windows
  backslashes (use raw strings or files).

## Parked ideas / not done yet

- Resume an interrupted/cancelled job; packaged Windows release (PyInstaller zip / installer).
- Later, when the owner asks: scrubland on open ground (Caribbean), town trees where OSM has none,
  FPS benchmark vs X-World, sharper edges from ortho imagery.
- Other regions (Phase 4) — only when asked.
- **Not wanted** (owner, 2026-11-21): a "select all Colombia" button (tiles are selected by hand);
  tree height (too much work for the gain).
