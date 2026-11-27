# ForestStudio XP

A standalone Windows desktop app (Python backend + HTML/JS UI in a pywebview window) that builds
**X-Plane 12 forest overlay scenery** from satellite land cover, biome maps and OpenStreetMap,
starting with **Colombia**; other countries can be added from Geofabrik's list (Data view →
Regions). It is meant to become an open-source project (repo:
https://github.com/santigarcor/foreststudio-xp, license GPL-3.0); do not ship more regions in the repo unless asked
(users add their own in the app).

`PLAN.md` is the detailed historical log of every decision, measurement and finding (dated). This
file is the authoritative summary; keep both up to date when something important changes.

---

## Hard rules (from the project owner — always follow)

1. **Never modify installed X-Plane scenery or libraries** (simHeaven, airports, Global Scenery,
   default libraries…). ForestStudio only *reads* the X-Plane install. The one thing it creates
   there is its own folder link `Custom Scenery/forest_studio_xp` (a directory junction; an older
   `forest_<region>` link re-pointed at the pack keeps its name).
2. **`scenery_packs.ini`**: change it only when the user asks. Back it up first
   (`~/.foreststudio/cache/backups/scenery_packs.ini.<timestamp>`) unless the user explicitly says not to.
3. **Colombia uses evergreen forest assets only** (no `EXPORT_SEASON` variants, no `SEASONAL`
   textures). XP12's season rasters put the Andean highlands (1,500–2,500 m, e.g. Rionegro) in
   "winter" for ~6 months, so seasonal assets show bare trees. Colombia has no seasonal trees.
   **Palms are fine** (they exist in Medellín and Rionegro). The build refuses seasonal assets
   in any region with `evergreen_only = true` (Colombia's region file).
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
.venv\Scripts\python -m pytest -q        # all (≈8 s);  -m "not integration" for the fast ones
.venv\Scripts\ruff check forest tests tools/package   # lint (also in CI)
# an installer for this system (needs uv; Windows also Inno Setup 6)
.venv\Scripts\python tools/package/build.py --check      # → dist/
```

### Releasing

The version lives only in `forest/__init__.py` `__version__` (pyproject's version is dynamic from it;
shown in the header, Settings → About and the pack's `CREDITS.txt`). To release: bump `__version__`, add
`docs/releases/<v>.md` (the release notes, required) and a `CHANGELOG.md` entry, commit, and tag
`v<v>` (owner only; a `-` in the tag, e.g. `v0.2.0-beta1`, makes a pre-release).
`.github/workflows/release.yml` then runs the test suite (`tests.yml` is `workflow_call`-able), creates
a **draft** release with those notes, builds the installers on windows-latest, macos-15 (arm64),
macos-15-intel and ubuntu-22.04 (`tools/package/build.py --check`, each uploads
`<artifact>` + `<artifact>.sha256`), merges the checksums into `SHA256SUMS.txt` and publishes the
release; if any build fails it stays a draft. Manual runs (workflow_dispatch) only make workflow
artifacts (7 days), and so do pull requests touching `tools/package`, the workflows, `uv.lock` or `pyproject.toml`. Dependencies change → `uv lock` (CI checks `uv lock --check`).

**Packaging** (`tools/package/`, 2026-11-25; no PyInstaller): a python-build-standalone CPython
(`build.PYTHON`, fetched by uv) + the `uv.lock` runtime deps (wheels only, except the tiny
`proxy-tools` sdist) + the app as git-tracked source in the repo layout:
`<root>/python/…` and `<root>/app/{forest,config,tools/xptools/<this system>,LICENSE,README.md,CHANGELOG.md}`,
so `config.REPO_ROOT` = `<root>/app` and nothing else changes. A `foreststudio_xp.pth` in site-packages
puts `<root>/app` on the path; launchers run `python -I -m forest.app.main` (`-I`: no PYTHONPATH/user
site; workers and `Api.restart_app` inherit the flags). Unused stdlib (Tk, tests, IDLE) is dropped and
everything precompiled. `smoke.py` (run by `--check` with the bundled Python, `FORESTSTUDIO_HOME` = temp)
checks imports + the webview backend, defaults, DSFTool, the CLI and a worker process (pythonw on
Windows). Per OS: Windows Inno Setup (`windows/foreststudio-xp.iss`: per-user, no admin,
`{userpf}\ForestStudio XP`, fixed AppId GUID — never change it, shortcuts = `pythonw.exe -I -m
forest.app.main` with AppUserModelID `ForestStudio.XP`, upgrade wipes `python\`/`app\` first, uninstall
removes only those two + `forest.cmd`, never the data folder or the X-Plane link); macOS `.app` (shell
launcher, Info.plist, icns via sips/iconutil, ad-hoc `codesign -s -` on unsigned Mach-O files, dmg via
hdiutil; unsigned → README's "Open Anyway" step); Linux tar.gz (`foreststudio-xp` launcher, `cli`
subcommand, `install.sh` for a .desktop entry). **Linux uses pywebview's Qt backend** (PyQt6 +
PyQt6-WebEngine, `sys_platform == 'linux'` deps): GTK needs PyGObject, which has no wheels and can't
come from the distro for a bundled Python. `config.program_folders()` = the code folder + the install
folder, refused as a data folder.

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
| `config/regions/*.toml` | the user's own regions (optional; a file with a shipped region's name replaces it) |
| `config/profiles/*.toml`, `config/rules/*.toml` | the user's profiles / forest types; missing files are copied from the repo defaults on first use (`config.ensure_user_config`, never overwrites) |
| `config/catalog_xp12.json` | forest catalog generated from this X-Plane |
| `cache/` | `tiles/<tile>/`, `osm/` (country .pbf), `ecoregions/`, `jobs/`, `history.jsonl`, `backups/`, `decoded/` |
| `out/` | `forest_studio_xp/` (THE pack, every region's tiles, junction target) and `studio/` (overlays, stats) |

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

CLI (the same `forest/tasks.py` functions the app's workers run; handy for debugging):

| Command | What it does |
|---|---|
| `forest catalog` | (Re)build `~/.foreststudio/config/catalog_xp12.json` from X-Plane's `1200 forests` library |
| `forest fetch --tiles +06-076,+06-075` | Satellite rasters per tile (4 downloads at a time) + OSM in **one** pass; downloads the OSM country file / ecoregions zip first if missing |
| `forest data osm [--update] [--force]` | Download / update the Geofabrik country file (only if the server's is newer), then re-extract every tile that has `osm.json` |
| `forest data ecoregions [--region x] [--update]` | Download the RESOLVE zip and make the region's cut-out |
| `forest data region --region x` | Everything a newly added region needs: OSM file, ecoregions zip, cut-out (+ re-extract covered tiles) |
| `forest build --tile +06-076 --profile medium --compile [--region x] [--keep-text] [--install]` | Build one tile with its owner region's forest types (`--region` overrides); `--install` ensures the pack link exists |
| `forest decode <file.dsf> [--label x]` | DSF → text into `<data>/cache/decoded/` (debug) |
| `forest poc …` | Phase-0 proof of concept (three test squares near SKRG) |
| `forest app` | Open the desktop app |

---

## Architecture

```
forest/
  config.py        data-folder paths, local.toml load/save, X-Plane & DSFTool auto-detect + validation;
                   toml_str() = the only way to write a TOML string (json-escaped + DEL; also settings.py)
  migrate.py       one-time moves: old in-repo data → ~/.foreststudio; run_packs(): per-region
                   packs → the one pack (+ junction re-point)
  datafolder.py    Settings → Data folder: check / move / switch, re-point links, location.toml
  credits.py       data + software credits (Settings → About) and the pack's CREDITS.txt
  regions.py       Region files (config/regions/<name>.toml): label, bounds (map tiles), ecoregion
                   cut-out box, Geofabrik osm_url, evergreen_only, optional tiles list;
                   names()/get()/default_name(); template_rules_text (global rules);
                   add_from_geofabrik / remove; osm_sources(tile) = every downloaded extract covering it
  tiles.py         Tile (1°×1°, name "+06-076", 10° folder "+00-080"), Grid (north-up raster)
                   Tile.cache creates the folder (write); Tile.cache_path never does (read/check)
  paths.py         <data>/out/forest_studio_xp/Earth nav data/…/<tile>.dsf (the one pack) vs
                   out/studio/<tile>.{txt,stats.json,overlay.png,preview.png}; legacy_packs()
  fetch/
    sources.py     country-wide files: Geofabrik .pbf + RESOLVE zip — download (.part → rename,
                   `<file>.json` meta: server date/size/downloaded), status, check_update (HEAD)
    rasters.py     ESA WorldCover 2021 (COG windows), Hansen GFC v1.12 treecover2000 + lossyear
                   (GDAL HTTP retries: 4 × 5 s, so one network blip doesn't fail a tile)
    geofabrik.py   Geofabrik index-v1.json (cache/osm/geofabrik-index.json): tree of extracts,
                   tiles an extract's outline touches, region file text, sizes (HEAD)
    osm_pbf.py     ONE pass over the Geofabrik .pbf → streams elements into every requested tile's
                   osm.json (format VERSION 8: ways/rings as [lon, lat] pairs, ~25 % smaller than
                   7's {"lat","lon"} objects; a 7 file is converted in place keeping its date
                   (`_upgrade_lean`), older versions re-extracted automatically). Speed
                   (2026-11-24, same output): osmium C++ filters (`PREFILTER_KEYS`: only tree
                   points and ways/relations/areas with a key we use reach Python — every key
                   wanted_way / wanted_area_relation read must be listed, a test checks) and way
                   coordinates via osmium's WKB factory + numpy, not one Python object per node
                   (`_way_slow` = the old per-node path, fallback). Colombia 5 tiles 146 → 62 s,
                   Florida 324 → 144 s. Tiles built from it are byte-identical (DSF text, PNGs)
    osm.py         fetch_osm: the downloaded country file(s) only; no online fallback (Overpass was
                   removed 2026-11-24). No file → the tile's current-VERSION osm.json if any, else
                   OsmFileMissing ("Download it in Data → Regions"); that tile's Data step fails
    elevation.py   elevation from X-Plane's own base mesh via DSFTool (no download)
    ecoregions.py  RESOLVE Ecoregions 2017 → cache/ecoregions/<region>.geojson → per-tile raster
    climate.py     leaf type (Copernicus LC100 forest-type window → tile leaftype.tif) × Köppen climate
                   (cache/climate/koppen_geiger_1991_2020.tif) → lib/g8/<family>[_sp]_<temp>_<moist>.for
  exclusions.py    OSM cut-outs (roads, buildings, water, runways, rail/power, bare land) + tree hints
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
  proc.py          hidden subprocess helpers (no console windows); run_measured = + peak memory;
                   terminate_pid = stop a process tree
  fsutil.py        size_bytes / size_mb / move_merge (no-overwrite move) shared by data folder,
                   migration, disk usage and history
  events.py        progress events (worker → app over a pipe; printed as text in the CLI)
  tasks.py         the work itself: fetch, build, data_osm/region/ecoregions, compile_dsf
  worker.py        one task in one fresh process (spawn, pythonw on Windows), messages, stop
  cli.py           command line (thin wrapper over tasks)
  app/             pywebview desktop app: main.py, api.py (js_api), settings.py (schemas,
                   validation, TOML writers), devserver.py (dev only; POST needs a JSON Content-Type,
                   so other web pages can't call it), single.py (one app window per data folder:
                   OS lock on <data>/app.lock; a restart waits up to 20 s for it),
                   ui/{index.html,app.css,app.js,views.js}, ui/vendor/leaflet (Leaflet 1.9.4,
                   BSD-2, shipped so the map works without a CDN)
                   api.start_build refuses sea-only tiles (no base-mesh DSF in X-Plane's Global Scenery);
                   it and the tile download (start_data_job 'tiles') refuse without a valid
                   X-Plane folder (_xplane_missing: elevation + forest types come from it)
config/                             DEFAULTS shipped with the app (user copies live in ~/.foreststudio/config)
  regions/colombia.toml             the region: map tiles, OSM extract, ecoregion box, evergreen_only
  profiles/{low,medium,high}.toml   performance profiles
  rules/colombia.toml               biomes, elevation bands, plantations, single-tree asset
  local.example.toml                template for ~/.foreststudio/config/local.toml
tools/xptools/                      bundled DSFTool + license notes
tools/package/                      release installers: build.py, smoke.py, windows/ (Inno), macos/, linux/
docs/releases/<version>.md          release notes (the GitHub Release text); CHANGELOG.md = all versions
uv.lock                             locked dependencies the installers bundle
tests/                              pytest suite (sandboxed)
```

### The build pipeline (per tile)

1. **Data**: WorldCover 10 m (classes; trees = 10 **and 95 mangrove**), Hansen tree cover % and
   loss year (30 m), OSM features, elevation from the base mesh, ecoregion polygons.
2. **Classify** on a grid of `grid_cells` per degree (medium 6000 ≈ 18.5 m):
   - tree cell = WorldCover tree share ≥ `min_tree_frac` (≥ `osm_green_min_frac` inside OSM
     parks/gardens/golf/woods), minus Hansen loss from `recent_loss_from`;
   - `smooth_passes` × 3×3 majority filter (`build.majority3`, = scipy median_filter; mangrove
     cells exempt, so shore fringes survive);
   - runway clear strip = centre line ± `runway_clearance_m`, stopping at the runway ends;
     **town trees only** also stay off `runway_ends` = that strip continued `exclusions.RUNWAY_END_M`
     (300 m: ICAO strip end + RESA, approach lights; owner 2026-11-25) past each end (BUILD_VERSION 8; SKRG had town
     trees 220 m past the 01 threshold, outside the OSM aerodrome outline). Not a cut-out: real
     forest past a runway end is kept (owner, 2026-11-25);
   - cut-outs drawn at 2× detail, a cell cleared only if they cover ≥ `cutout_min_cover`; water is
     **not** cut in mangrove cells; **bare land** (OSM `landuse=construction|quarry|brownfield`,
     `natural=sand|bare_rock`; category `bare`) is a cut-out because the satellite maps (2021–2024)
     still show forest on newer building sites (+30-082: 2.0 of 6.7 km² of construction sites);
   - **mangrove cells** = WorldCover class 95 (share ≥ `MANGROVE_MIN_FRAC`) **or OSM
     `natural=wetland` + `wetland=mangrove`**; inside OSM mangroves grass/wetland/shrubland count as
     half trees (`OSM_MANGROVE_LOW_VEG`, threshold `osm_green_min_frac`): WorldCover calls low
     mangrove "herbaceous wetland" (SKCG canal banks and runway shore);
   - density = 0.6 × WorldCover share + 0.4 × Hansen (mangrove cells: WorldCover only, Hansen 2000
     misses Caribbean mangroves), gaussian blur, quantised to `density_levels`;
   - **biome** per cell from ecoregions; mangrove cells override to the `mangrove`
     biome; asset = that biome's elevation band × dense/sparse (`sparse_below`);
   - **climate biomes** (`climate = true`, every biome of the global template except mangrove; not
     Colombia): asset = X-Plane's `lib/g8/{coni,broad,mixed}[_sp]_<temp>_<moist>.for` from the leaf
     type (Copernicus forest type, 500 m majority: ≥ 60 % conifer → coni, ≥ 60 % broadleaf → broad,
     else/unknown → mixed; broadleaf in Af → `rainforest_vhot_rain`) × the Köppen class
     (`climate.KOPPEN`: e.g. Cfa → wrm/wet, Cfb → tmp/wet, Af → vhot/rain); sea cells take the nearest
     climate; evergreen-only regions always use `vhot` (rule 3); their scrub becomes
     `shrb_<temp>_<moist>` (the biome's scrub type only says whether there is any); elevation bands
     are the fallback when the climate map is missing. On top (since 2026-11-24, BUILD_VERSION 5):
     OSM `leaf_type` on woods beats the satellite; broadleaf/mixed in cool (vcld/cld/tmp), wet
     (wet/rain) tropics (|lat| < 23.5) → cloud forest `rainforestm[_sp]_vhot_rain`; OSM
     `wetland=swamp` → swamp forest `wetl_wdy_<temp>_<moist>`; thin broadleaf/mixed in hot/vhot ×
     dry/sdry → savanna `sparse_tr_*`; wetland bushes `wetl_shrb_*` in OSM marsh/bog/fen/wet meadow
     or WorldCover wetland **only where WorldCover also sees shrubland** (salt marsh / reed beds get
     none); orchard/fruit plantations → `orch_<temp>_<moist>` (cold → tmp; wrm_sdry → wrm_dry);
     town trees, OSM single trees and rows → `broad_<temp>_<moist>`. All 246 names exist in XP12. These are the same files simHeaven X-World uses
     (`1200 forests/<season>/coni_wrm.for` …);
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
     minus OSM industrial/commercial/retail/landfill, car parks (`amenity=parking`), forest,
     plantations, water and **airport grounds**
     (OSM aerodrome boundary; WorldCover calls apron/runway pavement built-up). Scrub also skips
     airport grounds; real forest there is kept unless `clear_airport_grounds = 1`.
3. **Vectorize** per (asset, density level) inside `chunks_per_degree` windows, simplify
   (`simplify_m`), drop < `min_area_m2`; **split polygons with > 250 holes** (DSF limit: 255
   windings per polygon — "too many intervals in polygon").
4. **OSM trees**: `natural=tree` → point forests (fill 512, grouped per chunk); `natural=tree_row`
   → line forests (fill 256); skipped inside buildings, runway strips and existing forest; never
   thinned by the budget. **Town trees** (`build.town_trees`): Poisson-random points in town cells
   (`town_trees_per_km2`, seeded by tile name so rebuilds are identical), dropped if they hit a
   building (with clearance), road, runway/taxiway, rail/power corridor, water, bare land, car park,
   industrial/commercial/landfill land or airport grounds — at the exact spot, not per cell (one
   bulk STRtree query; +30-082 had 951 town trees on car parks before);
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

- **One pack for every region** (since 2026-11-23): `~/.foreststudio/out/forest_studio_xp` is linked into
  X-Plane as **one directory junction** `Custom Scenery/forest_studio_xp` (like OrthoStudio; a symlink on
  macOS/Linux). No admin rights needed. Every built tile (any region) is in X-Plane immediately; uninstall
  removes only the junction. `pack.our_links()` = every `forest_*` junction pointing at the pack, so an
  older link name counts as installed.
- **Migration** (`migrate.run_packs`, app and CLI start): old `out/forest_<region>` / `studio_<region>`
  are merged into the pack (a tile in two packs keeps the newer build); our X-Plane link to an old pack is
  **re-pointed at the new pack under its old name** (the one listed first in `scenery_packs.ini`), so it
  keeps its place in the scenery order without editing the ini; other old links are removed (X-Plane
  would show the tiles twice). The owner's `forest_colombia` link becomes this. Verified on a scratch copy
  with a fake X-Plane folder (Colombia + Panama + Luxembourg packs → one, ini untouched). File symlinks are not possible on
  this PC (need Developer Mode/admin); hard links were used briefly and replaced by the junction.
- An existing *real* pack folder is absorbed: tiles only in X-Plane move into the project,
  duplicates are removed, anything that isn't a `.dsf` (or our `CREDITS.txt`) makes it refuse.
- **Scenery order** (owner's ini): the pack (still named `forest_colombia` there) sits **below** hand-made airport/city packs
  (they have their own trees our exclusion would delete) and **above** simHeaven
  `X-World_America-4-extras` and `7-forests`, so on built tiles our tile-wide exclusion hides
  X-World's forests, random trees, hedges and `osm_tree`; unbuilt tiles keep X-World's forests
  (7-forests stays **enabled**). Expect style seams at 1° borders.
- New tiles need an X-Plane restart; rebuilt tiles show with Developer → Reload Scenery.

### Jobs / parallel builds

**Workers, not CLI subprocesses** (since 2026-11-24): every task runs in its **own fresh process**
via `forest/worker.py` (`Worker(task, **kwargs)`; `multiprocessing` spawn; on Windows the child runs on
`pythonw.exe` → no console, rule 4; `freeze_support()` in `app/main.py` for a packaged build). Tasks are
the functions of `forest/tasks.py` (`fetch`, `build`, `data_osm`, `data_region`, `data_ecoregions`);
`cli.py` is now a thin wrapper over the same functions (debugging). Progress comes back as
**structured events** over a pipe (`forest/events.py`: `emit(event, **data)`; names `log`, `download`,
`downloading`, `ready`, `sat_start`, `sat_tile`, `osm_reading`, `osm_done`, `step`), stray prints as
`log`, then exactly one `exit {rc}`; an exception sends `error {type, message, traceback}` (traceback
lands in the job log); a process that dies silently yields `exit {crashed: True}` and only fails its own
tile. Without a sink (CLI) `events.emit` prints `events.text()`, the same lines as before. Cancel =
`Worker.stop()` → `proc.terminate_pid` (process tree, so DSFTool goes too). A new process per tile is
deliberate: GDAL/numpy/shapely don't give the 2–4 GB peak back; ending the process does.

App job (`Api._run`, since 2026-11-24): `prepare` (files every country shares: ecoregions zip,
climate map — once, so two workers never write the same file), then the tiles are split by
`regions.osm_groups` into **groups that share no OSM country file** (a border tile joins its
countries; tiles of one country = one group, one pass per file). Each group's `fetch` (satellite ×4,
OSM one pass) runs **side by side** with the others, and its tiles start building as soon as their own
data is ready (Colombia's tiles build while Florida's 662 MB file is still being read: 2 tiles 375 s vs
~510 s before). Data steps and builds share the same N slots (a semaphore): an OSM pass peaks at
1.4 GB (Colombia) / 3.05 GB (Florida), like a build (`osm_pass` history now records `peak_gb`).
Per-group phases are joined with " · " in `job_status.phase`; `osm_reading` / `osm_done` carry
`tiles`, so a note or "data done" only lands on that group's tiles. The pool of
N `build` workers (N = Auto from `resources.auto_workers()`, measured peak ≈ 3.8 GB RAM for
Medellín, 2.2 GB San Andrés since 2026-11-21 (was 5.3 / 4.3 GB); this PC 8 cores / 33 GB → Auto 3,
the peak = 90th percentile of the last `resources.RECENT_BUILDS` (30) build peaks in `history.jsonl`,
so old heavy builds age out; the peak includes DSFTool, measured
since 2026-11-23: 0.27 GB on Medellín while the build holds 0.4 GB, so it never sets the limit). Logs tagged `[tile]`; a failed
tile doesn't stop others; cancel terminates all. Every job is saved in `<data>/cache/jobs/<id>.{json,log}`
with per-tile steps **Data → Classify → Vectorise → Compile** (advanced by `step` / `sat_tile` /
`osm_done` events in `JobRecord.on_event`), results and a report; jobs left running by a closed app become "interrupted".
Measured: 6 tiles in 89 s with 3 workers vs 218 s serial; +30-082 69 s through a worker = the CLI time.

While the data step runs, `Api._on_event` turns the fetch events into a plain-words phase
(`job_status.phase`): "Downloading the OpenStreetMap file florida-latest.osm.pbf… 45 % (120 MB)",
"Satellite maps: 1 of 2 tile(s) done", "Reading OpenStreetMap (file, MB) for N tile(s)"; the Tiles tab
progress line and the Data view show it, and `JobRecord.data_note` writes it on every waiting tile's
Data step (Build queue). Download progress (`download` events, one per 1 %) is logged per 10 %.

A build job can mix regions: `Api.start_build` looks up each tile's owner (`regions.owners()`) and runs
a `build` worker with `region=<owner>` per tile; the job record's `region` holds the regions' labels.

Download jobs use the same runner and queue (`Api.start_data_job`): kind `data` (fetch for tiles
selected on the map, the ecoregions zip, or `region` = `forest data region`, started by
`Api.add_region` right after a region is added) and `osm_update` (`forest data osm --update`, then
re-extract every tile with `osm.json`). Their records have `kind` and `step_names = ["data"]`;
old records without them load as builds. Only one job (build or download) runs at a time.

---

## The app (views)

- **Build**: Leaflet map (Esri satellite / OSM) with the 1° grid of **every region's tiles** (one
  map; select tiles of several regions and build them together; "Go to region…" moves the map; no
  region picker in the header); tile status colours (cached,
  built, installed, selected; **outdated** = orange; no ortho status — not relevant); click/shift-click selection
  (no "select all": the owner selects by hand); **Select outdated**; forest overlay (colour = biome
  or plantation, lilac = scrub, brightness = density, red = cut out, yellow = OSM trees, light blue =
  town trees) with legend. **The map updates as each tile finishes** during a job (`poll()` →
  `refreshFinished`: overlay + status colours + Tile info, one `refreshStatus()` per batch of newly
  finished tiles; selection kept), not only when the whole job ends; sidebar tabs:
  - **Tiles**: selection, **X-Plane pack** (Install/Uninstall pack, ini warnings), Build with
    "Builds at the same time: Auto (N)/1…max", time & disk **estimate** (from `history.jsonl`
    medians), confirmation for ≥10 tiles or >30 min, Delete selected tiles (built tiles and/or
    downloaded data), Tile info (whose forest types + "Edit …'s forest types", status, last build
    stats, forest by biome, plantations, types).
  - **Profile**: every profile value as slider + help + FPS mark; Reset to defaults (shipped values via
    `Api.default_profile`; own profiles reset to medium; not saved until Save) / Revert / Save as / Save.
  - **Forest types**: "Forest types of [region]" picker (one region at a time); sparse_below, OSM single-tree type, general town-tree type, per-biome
    **Scrub & town trees** (bush type or none, "also on bare ground", town tree type), **Plantations** editor (tags, tree type,
    thickness, reorder), **Biomes** (colour, label, ecoregion chips add/remove, per-biome
    elevation bands with dense/sparse type pickers sorted lightest first with FPS cost); **Reset to defaults** loads the shipped
    rules (`Api.default_rules`), saved only with Save).
  - (No log tab: the Tiles tab shows progress + "Open in Build queue"; the live log is in the Build queue.)
- **Build queue** (internal id `works`; the open log keeps updating without closing): saved jobs (search by tile), progress, per-tile step bars, log, final report,
  per-step table, errors; delete job / clear all. Download jobs show as "Download data" /
  "Update OSM data".
- **Data**: **Regions** (your regions: tiles, evergreen/seasonal, OSM file per region with size / date /
  Download / Check for update → Update, Show on map, Remove for added ones; Add a region from Geofabrik's
  list: continent picker, filter, Show download sizes, Refresh list; **adding starts its download at
  once**, and while a region's download runs its row shows what is downloading with a live % bar:
  `job_status` gives `region` + `progress` parsed from the CLI's `download <what>: N MB P%` lines,
  printed per 1 %, logged per 10 %); cards for ecoregions (Download; which regions have a cut-out), satellite (fixed releases, nothing to
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
| OSM | Geofabrik `colombia-latest.osm.pbf` (~335 MB) in `<data>/cache/osm/` | ODbL; required (the Overpass fallback timed out on whole tiles and was removed) |
| Biomes | RESOLVE Ecoregions 2017 (`storage.googleapis.com/teow2016/Ecoregions2017.zip`, 149 MB, CC-BY 4.0) | clipped to `<data>/cache/ecoregions/colombia.geojson` |
| Leaf type | Copernicus Global Land Cover 100 m 2019, forest-type layer (Zenodo 3939050, one 1 GB COG-like GeoTIFF, 256² blocks) | 1° window per tile ≈ 0.1–1 MB, ~6 s; only for climate biomes |
| Climate | Köppen-Geiger 1 km 1991–2020 (Beck et al. 2023, figshare 21789074, CC-BY 4.0) | 131 MB zip once → only the 12 MB present-day map kept |
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

## Regions (`config/regions/<name>.toml`)

A region's name names its forest types (`config/rules/<name>.toml`), its OSM file and its ecoregion
cut-out. **Tile owner**: the map offers every tile of every region; a tile several regions offer is
built with the forest types of the region whose Geofabrik outline covers most of it
(`regions.owners()` / `owner(tile)`, from `geofabrik.tile_shares`; without the index every region
tile counts as fully covered; ties: default region, then name). When the owner changes (e.g. adding
Ecuador takes Quito from Colombia's box), the tile shows as outdated ("built with X's forest types"). Keys: `label`, `bounds = [w, s, e, n]` (whole degrees: the
tiles offered on the map), optional `ecoregion_bounds` (cut-out box, default bounds + 1°; Colombia
keeps its old box so tiles stay identical), `osm_url` (Geofabrik `.osm.pbf`), `evergreen_only`
(Colombia true: rule 3; false lets rules and builds use seasonal types), optional `tiles = [...]`
(only these tiles of the bounds) and `geofabrik_id`. Shipped files in `<code>/config/regions/`
(only Colombia), the user's in `<data>/config/regions/` (same name wins). The tile cache
(`cache/tiles/`) and the pack (`out/forest_studio_xp`, `out/studio`) are shared by all regions.

- **Adding a region** (Data → Regions): downloads Geofabrik's `index-v1.json` (3.4 MB, on request)
  and writes `<data>/config/regions/<id>.toml` with the tiles the extract's outline touches,
  `evergreen_only = true` when the outline's centroid is within ±23.5° (rule 3 spirit: XP12 seasons
  in tropical highlands). Remove deletes the region file, its OSM file (unless another region uses
  it) and its ecoregion cut-out; forest types and built tiles stay. Shipped regions can't be removed.
- **Global default rules** `config/rules/_global.toml` (a template: files starting with `_` are not
  copied to the user's config): one biome per RESOLVE biome via `resolve_biomes`, plantations incl.
  temperate crops, mangrove + default. A region without rules gets a copy on first use
  (`config.rules_path` → `regions.template_rules_text`): `{label}` filled in, and in evergreen-only
  regions every seasonal type replaced by its `[evergreen_swap]` partner (the table isn't copied).
  Biome per cell: ecoregion listed by name → first biome whose `resolve_biomes` holds the
  ecoregion's RESOLVE biome (RESOLVE "Mangroves" polygons → tropical_moist, they reach inland) →
  `default`. `resolve_biomes` is written only when non-empty (Colombia's rules/fingerprint unchanged).
  Template biomes carry `climate = true`; regions without shipped rules whose file predates it get it
  from the template by biome name (`build.fill_rule_defaults`); `climate` is written only when present.
- **Border tiles**: `regions.osm_sources(tile, region)` = every downloaded extract whose Geofabrik
  outline (or, without the index, region tiles) covers the tile, plus the building region's own.
  `extract_tiles` makes one pass per file into shared writers, dedupes by (type, id) when there are
  several files, and records `"sources"` in the osm.json header (single-file tiles keep the old
  header); `is_current(tile, sources)` re-extracts when the set of files changes (adding Panama
  re-extracts Colombia's border tiles on their next fetch, which makes them outdated).
- Verified 2026-11-23 (scratch folder): Panama +08-080 (all evergreen, 986 km² forest), Luxembourg
  +49+006 (seasonal temperate types, conifer plantations from OSM `leaf_type=needleleaved`),
  border +07-078 from both files (Panama adds 1,083 features, 138 shared written once); Colombia
  +10-076 / +06-076 byte-identical.
- Not done yet: per-region scenery-order advice (X-World packs differ per continent), regions
  crossing ±180°, very large extracts (a continent: one osm.json pass reads the whole file).

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
  ecoregions — WorldCover class 95 and OSM mangrove areas only**; RESOLVE mangrove polygons reach far inland and were
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
Shipped medium: `tile_tree_budget` 30M, `global_density` 0.8 (2026-11-24, was 0.75; owner's FPS check 2026-11-23,
frame limiter off: ~100 fps at 27M, 90–100 fps at 35M / 0.75, which the owner uses).

---

## Testing

- `tests/conftest.py` → `sandbox` fixture uses a temp code folder (`ns.repo`) and a temp data folder (`ns.root`) and redirects config paths, cache, out, jobs, history,
  profiles/rules, OSM file, ecoregions dir and the X-Plane folder to `tmp_path`. **Never call
  `monkeypatch.undo()` in a sandbox test**: it also undoes the sandbox and the test then writes into
  the real data folder (happened once, 2026-11-24); patch inside `with monkeypatch.context() as m:`.
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
  macOS (3.12) on every push/PR — the only place the Linux/macOS DSFTool and symlinks get exercised —
  plus `ruff check forest tests` once (config in `pyproject.toml`).
- Performance changes must not change tiles: rebuild a tile before/after in a scratch data folder
  (`FORESTSTUDIO_HOME`) and compare polygons (forest type, density, vertices), PNGs and stats.
  `tests/test_speedups.py` pins each fast path to the code it replaced (2026-11-24, byte-identical
  tiles, builds −23…−35 %): block counts by strided slices (`exclusions.block_count`; also the low
  profile's k = 3 WorldCover shares, exact because 100·c/9 never ties — `build._TIE_FREE`),
  `build.majority3`, way geometries built and validity-checked in bulk (`exclusions._bulk`,
  `_Ring` placeholders keep the order), bulk `shape()` (`build._polygons_from_geojson`), one
  STRtree query for OSM single trees.
- UI check: `.claude/launch.json` → `forest-ui-dev` (dev server on :8765, or `PORT`); the real window was
  smoke-tested with pywebview `evaluate_js`.

## Conventions

- Keep the X-Plane pack folder clean (only `.dsf` + our `CREDITS.txt`, which the data licenses
  require; written on every compile and on Install pack); app files go to `<data>/out/studio/`.
- New per-tile data → `Tile.cache` (write) / `Tile.cache_path` (read). Bump `osm_pbf.VERSION` (now 8: [lon, lat] layout; 7: bare land, car parks, landfills)
  when extracted OSM features change.
- Record measurements with `history.record(...)` so estimates stay accurate.
- Large inline edits: prefer the Edit tool; Python patch scripts via heredoc break on Windows
  backslashes (use raw strings or files).

## Parked ideas / not done yet

- First public release: README screenshots, issue templates/CONTRIBUTING; code signing (Windows
  Authenticode, Apple Developer ID + notarization) for the installers.
- Owner checks in the sim: town-tree density, scrub, xeric trees, the new OSM mangroves (SKCG).
- OSM multipolygon holes are dropped: ~80 km² of forest on river islands (+07-074, +06-075) is cut
  as water. Fix parked (changes tiles; see PLAN.md 2026-11-21).
- Test on a real Mac / Linux machine (only CI so far).
- Other regions: owners check the global default forest types in the sim (temperate seasons,
  boreal, Mediterranean, desert scrub) and tune `config/rules/_global.toml`.
- **Not wanted** (owner, 2026-11-21): a "select all Colombia" button (tiles are selected by hand);
  tree height (too much work for the gain); sharper edges from ortho imagery; resuming an
  interrupted/cancelled job.
