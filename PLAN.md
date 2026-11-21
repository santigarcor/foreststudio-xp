# Forest Studio — Plan

Build better X-Plane forest overlays from land-cover, canopy and biome data, starting with Colombia.
Personal use only (not redistributed).

## 1. Goal

A command-line pipeline that, for any 1°×1° tile:

1. Collects land-cover, tree-cover, canopy-height, biome, elevation and OSM data.
2. Classifies every area as *no trees* or as a **forest class** (biome + density + height).
3. Turns that into forest polygons that point at **X-Plane's own `.for` library assets**, the same approach simHeaven X-World uses.
4. Writes an overlay DSF that also excludes the default forests underneath.
5. Keeps the tree count inside a **performance budget** that you set.

Output: a `Custom Scenery/forest_<region>` overlay folder.

## 2. Key decisions

| Decision | Choice | Why |
|---|---|---|
| Language | **Python 3.12+** | It has the best geospatial ecosystem (GDAL, rasterio, geopandas, shapely, the Earth Engine API, OSM readers). Performance is fine because the heavy raster work runs in GDAL/numpy or on Earth Engine. |
| Heavy raster processing | **Google Earth Engine** (free non-commercial tier) | Dynamic World, WorldCover, Hansen and the canopy-height maps are all hosted there already, so we skip terabytes of downloads. Local GDAL is the fallback. |
| DSF compile | Laminar's **DSFTool** (called as an external program) | We write DSF text files and DSFTool compiles them. No binary format work on our side. |
| Tree assets | **X-Plane's default `.for` library paths** | Nothing to draw or license, and they automatically match the sim's lighting and seasons. Custom `.for` files are optional later. |
| Configuration | TOML files (Python reads them natively): one for the region, one for class-to-`.for` mapping rules, and one per performance profile | All tuning without code changes. |
| Target sim | X-Plane 12 (XP11 as a stretch goal) | XP12 has seasons and better trees. |

**About Google imagery:** Earth Engine is the sanctioned route. Scraping Google Maps tiles goes against Google's terms even for private use, so we don't rely on it. If we want high-res imagery, it's for visual checks only.

## 3. Data sources (all reachable from Earth Engine unless noted)

| Purpose | Dataset |
|---|---|
| Is it forest? | ESA WorldCover 10 m; Google Dynamic World 10 m (class probabilities, any date range) |
| Colombia detail | IDEAM *Mapa de Coberturas de la Tierra* (CORINE Land Cover Colombia, 1:100k), downloaded separately |
| Density | Hansen Global Forest Change: tree cover 2000 % and loss year |
| Height | ETH Global Canopy Height 10 m; Meta/WRI 1 m canopy height |
| Biome | RESOLVE Ecoregions 2017 |
| Climate | Köppen-Geiger (Beck et al.) |
| Elevation | Copernicus DEM 30 m |
| Mangroves | Global Mangrove Watch |
| Plantations, parks | OSM (`landuse=forest/orchard/plantation`, `natural=wood/scrub`, `leaf_type`, `leaf_cycle`) |
| Exclusions | OSM roads, rail, buildings, water, landuse=residential/industrial; airports and runways from X-Plane's own airport data (`apt.dat`) |
| Seasons | MODIS phenology (MCD12Q2); NDVI time series from Sentinel-2 |

## 4. Pipeline stages

```
fetch  ->  classify  ->  vectorize  ->  map-to-assets  ->  budget  ->  write-dsf  ->  compile/install
```

1. **fetch:** for a tile, export the aligned rasters from Earth Engine (one 10 m grid) and pull OSM features. Cache everything per tile.
2. **classify:** combine the layers per pixel into
   - `forest_mask`: Dynamic World or WorldCover trees, IDEAM overriding where available, minus recent Hansen loss;
   - `density` (0–1): Hansen tree cover, adjusted by the Dynamic World tree probability;
   - `height`: canopy height;
   - `class`: rule table of ecoregion × elevation band × climate × special overrides (mangrove, plantation, páramo, urban).
   - Finally subtract the exclusion masks (road buffers, buildings, water, runways and approach areas).
   - **Why this matters (PoC 2026-11-20):** X-Plane fills a forest polygon completely. It only skips water (`SKIP_SURFACE water`), not roads, buildings or fields. Every gap visible in the ortho photo has to be a hole or gap in our polygons.
   - **Alignment with your ortho:** the tree mask has to line up with the OrthoStudio imagery (Bing ZL16–18, under 1 m/px). The 10 m products (Dynamic World / WorldCover) get the general shape right, but edges can be off by 10–20 m. Planned options:
     - (a) 10 m mask + OSM road/building carve-outs: Phase 1 baseline;
     - (b) refine the mask from the ortho textures themselves (vegetation segmentation on the tile's DDS images, local data, personal use) so trees match exactly what you see;
     - (c) Google Open Buildings and OSM for houses.
3. **vectorize:** group density into 3–5 levels, trace polygons per (class, density level), smooth and simplify, drop fragments smaller than `min_area`.
4. **map-to-assets:** the YAML rules map each class (plus its height band) to an X-Plane library `.for` path. Example: `andean_cloud_forest` → a tropical wet broadleaf `.for`; `pine_plantation` → a needleleaf `.for`.
5. **budget:** estimate trees per polygon (area × density × the `.for` file's trees per m²), add them up per tile, and scale density down if the tile goes over the profile budget (see §5).
6. **write-dsf:** write the DSF text file: forest definitions, forest polygons with density parameters, and exclusion zones that switch off default forests across the tile (or only where we have data).
7. **compile/install:** run DSFTool, then write the overlay folder and its `library.txt` if needed, and remind the user about `scenery_packs.ini` order.

## 5. Performance and density controls

Tree count is the main frame-rate cost. Polygon vertex count mostly affects load time and file size. Every knob lives in a profile YAML (`low` / `medium` / `high` / `custom`):

| Knob | What it does |
|---|---|
| `global_density` | Multiplier on every polygon's density (0–1) |
| `class_density_cap` | Maximum density per class. Rainforest is the big cost, so cap it. |
| `tile_tree_budget` | Hard cap on estimated trees per tile. The budget stage scales density down to fit. |
| `airport_zone` | Inside radius R of airports and along approach corridors: full detail and accurate edges (where you're low and slow). Outside it, `remote_density` applies. |
| `remote_density` | Lower multiplier for far-from-airport areas (what you mostly see from cruise) |
| `density_levels` | How many density steps (fewer = simpler polygons) |
| `min_area_m2` | Drop small patches |
| `simplify_tolerance_m` | Polygon simplification; affects load time and DSF size |
| `edge_falloff` | Lower density strip along forest edges. Looks natural and cuts tree count. |
| `exclude_defaults` | Remove the default forests under our coverage, so trees aren't drawn twice |

X-Plane's own graphics setting for world-object/tree density still applies on top of ours.

**Verification:** a repeatable benchmark. The same flight path, or X-Plane's built-in frame-rate test mode, over the test tile, run with default scenery, X-World, and each Forest Studio profile. Record average and 1% low FPS. Report each tile's estimated tree count next to it.

## 6. Coexistence with simHeaven X-World

- X-World also places forests. Inside regions we cover, turn off X-World's forest layer (it ships as a separate package/layer), or put our overlay above it and use exclusion zones.
- Keep X-World's buildings, roads and VFR objects.
- Ortho4XP: our overlay sits above the ortho tiles. Set Ortho4XP to skip its own forest overlays where ours exist.

## 7. Seasons

**Decision (2026-11-20): no seasons for Colombia → use only evergreen assets.** Colombian forests are effectively evergreen. But the XP12 season rasters put the high Andes (e.g. Rionegro) in "winter" for about 9 months, so any asset with `EXPORT_SEASON` variants shows bare trees.
- **Hard rule:** in Colombia, only assets with no seasonal variants (catalog `seasons == []`). Enforced in code via `forest.assets.catalog.seasonal_assets()`.
- Evergreen options:
  - broad/mixed: `very_hot`, `very_hot_dry`, `hot_dry`
  - conifers: `conifers/hot_dry` (pine + cypress → Andean plantations)
  - `lib/g8/*_vhot_*`, `rainforest*`, `orch_vhot_*` (banana / palm)
  - single-species trees: kapok, Brazil nut, banana, cypress, palms
- **PoC round 2 (2026-11-20): all three patches green, no winter look.** `conifers/hot_dry` reads well as Rionegro pine/cypress. `broadleaves/very_hot` and `rainforest` are full of palms and bananas, which is right for the lowlands and wrong for Andean native forest at 2,000 m+.
- **Gap:** every evergreen *broadleaf* default asset contains palms, bananas or agave. The only palm-free evergreen ones are the conifers and `sparse_tr_*` (244 trees/km²).
- **Plan: derived `.for` files.** A default `.for` embeds its tree meshes inline (MESH + VERTEX/IDX blocks). So an "Andean broadleaf" forest = a copy of e.g. `rainforest_vhot_rain.for` with the palm/banana MESH blocks dropped and group percentages rebalanced. It's generated by script; no art needed.
  - Shipped in our own pack's `library.txt` under `forest/...` names, plain `EXPORT` only, so it's evergreen.
  - Textures: the `.for` points at `../textures/...`, so either use a junction to the default `1200 forests` (like simHeaven) or rewrite the paths.
  - The same trick gives cloud forest (taller, denser), páramo shrubs (from `shrb_*` without yucca/agave) and lowland variants.
- Not allowed in Colombia: `warm`, `temperate`, `cold` forests and every `lib/g8/*_{wrm,hot,tmp}_*` asset (all seasonal).
- Drier zones (Caribbean dry forest, Llanos, La Guajira) are handled by picking dry assets (`*_dry`, `*_sdry`, `sparse_*`, shrubs), not by seasons.
- Season rasters / phenology are **deferred** until we expand to regions with real seasons (Phase 4). The `--season` PoC flag and `season_rasters()` stay in the code for that.

## 8. Repository layout (proposed)

```
forest-studio/
  pyproject.toml
  config/
    regions/colombia.yaml        # tile list, data sources, overrides
    rules/classes.yaml           # ecoregion × elevation × climate -> class
    rules/assets_xp12.yaml       # class -> X-Plane .for library path
    profiles/{low,medium,high}.yaml
  forest/
    cli.py                       # `forest build --tile +06-076 --profile medium`
    fetch/   (earthengine.py, osm.py, ideam.py, airports.py)
    classify/
    vectorize/
    assets/                      # catalog of X-Plane .for paths + trees/m² estimates
    budget/
    dsf/     (writer.py, dsftool.py)
    install/
  cache/                         # per-tile downloaded data (gitignored)
  out/                           # built overlays (gitignored)
  bench/                         # benchmark notes and results
```

## 9. Phases and milestones

**Phase 0: groundwork (proof of concept).**
- Set up Earth Engine access and the Python project.
- Catalog XP12's default `.for` library paths: read the library files in X-Plane's default scenery, and record each forest's tree types, sizes and spacing → `assets_xp12.yaml`.
- Check how DSFTool's text format writes forest polygons and exclusion zones (convert an existing X-World tile to text and inspect it).
- Seasons research (§7).
- Choose the test tile: **+06-076**, which contains Medellín/Rionegro SKRG and covers Andes, cloud forest and plantations.
- ✅ Done when one hand-picked polygon written by our code shows trees in the sim.

**Phase 1: one tile, end to end.**
- Fetch → classify (WorldCover + Hansen + a simple biome rule table) → vectorize → DSF for +06-076.
- Medium profile and default-forest exclusion.
- ✅ Done when it flies, looks better than default/X-World, and the benchmark is recorded.
- **Implementation (2026-11-20):** `forest fetch --tile +06-076`, then `forest build --tile +06-076 --profile medium --install forest_poc`. Uses the `.venv` with numpy, shapely, rasterio, pyproj, scipy; all have Python 3.15 wheels.
  - Data, no login needed:
    - WorldCover 2021 10 m (only the 1° window, read as HTTP ranges)
    - Hansen v1.12 treecover2000 + lossyear (the 1° rows)
    - OSM via Overpass (roads, buildings, water, aeroways, rail, power lines)
    - elevation taken from the XP12 base mesh with DSFTool, so no download
  - Grid: `grid_cells` per degree (medium 6000 ≈ 18.5 m).
    - Tree cell = WorldCover tree share ≥ `min_tree_frac`, minus the OSM cut-outs, minus Hansen loss from 2021 on.
    - Density = 0.6 × WorldCover share + 0.4 × Hansen cover.
  - Class = elevation band × dense/sparse (`config/rules/colombia.toml`, evergreen assets only, checked against the catalog).
  - Polygons per (asset, density level), traced in `chunks_per_degree` windows, simplified, small patches dropped. Then the budget step scales density to `tile_tree_budget`.
  - One tile-wide `sim/exclude_for`.
  - Outputs: `out/forest_colombia/.../+06-076.{txt,dsf,preview.png,stats.json}`.
- **OSM source:** the public Overpass server timed out (502/504) even on 7 km boxes. Switched to the **Geofabrik Colombia extract** (`cache/osm/colombia-latest.osm.pbf`, 335 MB, read with pyosmium). A tile extract takes about 80 s. Overpass stays as a fallback when there's no `.pbf`.
- **First full build of +06-076 (medium), 2026-11-20:**
  - 95,191 polygons, about 3M points, about 6,400 km² of forest.
  - Estimated 37.3M trees, scaled by 0.805 to the 30M budget.
  - About 47 s to build, 5 s to compile, DSF about 13 MB.
  - Cut-out cells: roads 2.1M, buildings 0.46M, water 0.56M, airports 18k, rail/power 0.24M.
  - Speckle control: two 3×3 majority passes, density blur σ = 2 cells, minimum patch 3,000 m², simplification 12 m. Polygons went from 190k to 95k.
  - Installed as `Custom Scenery/forest_poc/.../+06-076.dsf`, replacing the PoC squares. Next: in-sim check, then the FPS benchmark against X-World.

**Phase 2: quality.**
- Add IDEAM, canopy height, Dynamic World, OSM plantations and mangroves.
- Full Colombia class table: Amazon, Chocó, Andean, cloud forest, páramo, Caribbean dry forest, Llanos gallery forest, mangroves, plantations, urban.
- Airport zones and edge falloff.
- ✅ Done when the classes and heights match imagery on spot checks at SKBO, SKRG, SKCL, SKCG and SKLT.

**Phase 3: all of Colombia.**
- Batch build of about 100 tiles, run in parallel, with caching and resume.
- Performance profiles tuned using benchmarks.
- One-command install.

**Phase 4: generalize.**
- Region YAMLs for other countries, a global fallback rule table (WorldCover + ecoregions only), and seasonal experiments.
- Optional: custom `.for` files for signature Colombian species (wax palm, guadua bamboo, frailejón).

## 9b. Desktop app (2026-11-20)

A standalone window like OrthoStudio-XP: **pywebview** (Edge WebView2) shows an HTML/JS UI, and Python is the backend through `js_api`. No browser, no web server.
- **Start it:** double-click `Forest Studio.cmd`, or run `.venv\Scripts\forest-studio.exe` or `forest app`.
- **Tiles tab:** Leaflet map (Esri satellite / OSM basemap) with Colombia's 1° grid.
  - Tile status: has ortho (dashed orange), data cached (purple), built (blue), installed (green), selected (yellow).
  - Click to select; shift-click selects a rectangle; buttons "select ortho / built".
  - Build N tiles runs fetch → build → compile → install (optional, `forest_*` packs only) as a job in a subprocess, one tile at a time. Live progress and cancel.
  - Tile info shows the last build's stats and forest types.
  - The **forest overlay** (`<tile>.overlay.png`, transparent RGBA) is drawn over the satellite map with an opacity slider. Green = forest by band, brighter = denser; red = trees cut out by roads, buildings, water and airports.
- **Settings tab:** every profile value as slider + number with help text, grouped. Save / Save as / Revert. Unsaved changes are offered for saving before a build. Profiles are written back as commented TOML.
- **Forest types tab:** `sparse_below` + elevation bands (name, upper elevation, dense/sparse asset). The asset lists offer only evergreen forests, with trees/km² and species shown. Validation rejects seasonal assets.
- **Build log tab:** live output.
- **Dev only:** `forest.app.devserver` serves the same UI + API over HTTP on localhost (`.claude/launch.json` → `forest-ui-dev`) for checking the UI in a browser.
- Code: `forest/app/{main,api,settings,devserver}.py`, `forest/app/ui/{index.html,app.css,app.js}`. Leaflet 1.9.4 and map tiles load from the internet.

## 10. Phase 0 findings (2026-11-20)

**Ground rule:** Forest Studio only *reads* the X-Plane install. It never changes default scenery, simHeaven or other add-ons. Its output is a standalone folder in `out/`.

### X-Plane 12 default forests (`Resources/default scenery/1200 forests`)
- `library.txt` exports **383 virtual forest paths**. Catalog: `config/catalog_xp12.json` (`forest catalog`).
- **Public "custom use" set:** `lib/vegetation/forests/{broadleaves,conifers,mixed}/{cold_low,cold,temperate,warm,warm_dry,hot,hot_dry,very_hot,very_hot_dry}.for`, plus single-species forests (`lib/vegetation/trees/...`: kapok, Brazil nut, banana, coconut, date and Mexican palms, cypress, pine...) and planters.
- **Global-scenery set:** `lib/g8/<type>[_sp]_<temp>_<moisture>.for`
  - type: broad, coni, mixed, orch, fruit, shrb, wetl_shrb, wetl_wdy, sparse, rainforest[m]
  - temp: vcld, cld, tmp, wrm, hot, vhot
  - moisture: dry, sdry, wet, rain
  - `_sp` = sparse
  - Marked `SEMI_DEPRECATED` (hidden in WED) but still what the base mesh uses. It's the only place the **rainforest** and tropical **orchard** (banana/palm) mixes live. OK to reference.
- **Seasons** are done in the library with `EXPORT_SEASON spr|sum|fal|win`. The very_hot, hot_dry and rainforest assets are plain `EXPORT`, i.e. **evergreen, no seasonal change**.
- **Tree density:** max trees/km² at DSF density 255 = 1e6 / (SPACING x × z). Range: rainforest_sp ≈ 5,100, rainforest ≈ 6,900, conifers ≈ 10,000, orchards ≈ 15,600. That feeds the budget stage directly.
- `.for` LOD is 15,000 m. XP12 uses the smaller of that and the user's graphics distance.

### simHeaven X-World (read-only reference)
- `X-World_America-7-forests` holds overlay DSFs for all of Colombia, including +06-076. Forests come from OSM. The active version uses "smart" exclusions only where OSM has forest, so default forests show elsewhere.
- Its DSFs reference generic names (`simheaven/forests/{broad,coni,mixed,orchard,shrubs,wetland,hedge,vineyard,osm_tree,random_tree}.for`). `X-World_Vegetation_Library/library.txt` maps those, per season, onto the default `1200 forests` files using 5 climate-zone bitmaps (vcld/cld/tmp/wrm/vhot). Its `1200 forests` folder is a directory junction to the default library.
- In the vhot zone it only defines the `sum` set.

### Our approach (decided)
- Our DSFs **reference the default virtual paths directly** (`lib/vegetation/...`, `lib/g8/...`). No junction, no dependency on simHeaven, and per-polygon climate selection finer than 5 bitmap zones.
- **Living alongside X-World without touching it:** put `forest_<region>` *above* `simHeaven_X-World_America-7-forests` in `scenery_packs.ini`, and use `sim/exclude_for`. Exclusions in a higher overlay hide forests from packs below it. Optional: tile-wide exclusion (our forests only) or per-polygon exclusion (fill gaps with X-World/default).

### Phase 2: biome-specific forest types (2026-11-20)
- **Why:** SKCL showed giant rainforest trees on the Cauca valley floor (median 994 m, right on the old 1,000 m line). Elevation alone can't separate dry valleys from rainforest.
- **Data:** RESOLVE Ecoregions 2017 (CC-BY 4.0, `cache/ecoregions/raw/Ecoregions2017.zip`, 149 MB), clipped once to Colombia + margin → `cache/ecoregions/colombia.geojson` (5 MB), rasterised per tile (`forest/fetch/ecoregions.py`).
- **Rules v2** (`config/rules/colombia.toml`): `[[biome]]` = name, label, colour, ecoregion list, and its own `[[biome.band]]` elevation bands (dense/sparse, evergreen only). Biomes: rainforest, montane, dry_forest, xeric, savanna (Llanos), mangrove, paramo, plus `default` for unlisted ecoregions (required). Old band-only files still load as a single default biome.
- **Build:** biome per cell → its bands → asset. Overlay/preview colour = biome colour. Stats include `biome_km2`.
- **App:** Forest types tab has a biome picker, colour, label, removable ecoregion chips, an "add ecoregion" list (unassigned only) and per-biome bands. The legend lists biomes; Tile info shows forest by biome.
- **Result, +03-077 (Cali):** rainforest giants on the Cauca valley floor **0%** (was ~47% of trees). Valley trees are now dry forest (52%) and montane (48%); the Pacific slopes keep rainforest (46%). +06-076 is now montane 91%, dry forest 8% (Cauca canyon), páramo 1%. Both rebuilt and installed.

### DSF polygon limit fix (2026-11-20, tile +09-076)
- DSFTool failed with "too many intervals in polygon": a DSF polygon holds at most 255 windings (outline + holes). One coastal forest area in +09-076 had 336 (lagoon/road/building holes).
- `_split_for_dsf()` in the build now cuts any polygon with more than 250 holes into halves, recursively, until every piece fits. No holes are dropped. +09-076 now compiles (max 251 windings, 6.2 MB) and is live in the linked pack.

### Whole-pack link + no console windows (2026-11-20) — replaces "Linked installs" below
- **The pack is installed as ONE folder junction**, like OrthoStudio: `Custom Scenery/forest_colombia → <project>/out/forest_colombia`. No admin rights needed.
  - Every built tile is in X-Plane immediately; there's no per-tile install any more.
  - "Install pack / Uninstall pack" sit in the Tiles tab. Uninstall removes only the junction.
  - `forest/pack.py`: `link_pack` / `unlink_pack` / `pack_status` (also checks the scenery_packs.ini entry).
  - An existing real pack folder is absorbed safely: tiles only in X-Plane are moved into the project, duplicates are removed, and anything that isn't a tile is refused.
- **Output layout** (`forest/paths.py`): `out/forest_<region>/Earth nav data/…/<tile>.dsf` holds only tiles (this is the pack). The app's overlay, preview, stats and DSF text live in `out/studio_<region>/`. DSFs are compiled to a temporary name and swapped in atomically.
- Delete dialog: "Delete built tiles" (removes them from X-Plane too, since it's a link) and "Delete downloaded data".
- Converted on this PC: 76 app files moved to `out/studio_colombia/`; the X-Plane folder with 20 tiles became the junction, so X-Plane now sees all 24 built tiles. Tested uninstall → project kept → reinstall → a build appears live.
- **No console windows:** `forest/proc.py` starts every helper (DSFTool for compiling and for elevation, plus the CLI workers) hidden (CREATE_NO_WINDOW + SW_HIDE). The windows came from DSFTool started from the windowless app. The launcher is now `Forest Studio.lnk` → `.venv\Scripts\forest-studio.exe` (no console); `Forest Studio.cmd` was removed.

### Linked installs (2026-11-20)
- Installing a tile now creates a **hard link** in `Custom Scenery/forest_colombia/...` to the project's `out/.../<tile>.dsf`: same file on disk, no extra space, instant, swapped in atomically.
  - Falls back to copying if the project and X-Plane are on different drives.
  - File symlinks are not possible here: Windows requires Developer Mode or admin. OrthoStudio links whole *folders* with junctions, which wouldn't work for single tiles inside one pack.
  - A rebuild writes a new .dsf, so builds with Install re-link automatically.
- Uninstall removes only the X-Plane entry; the project file stays. "Delete build output" alone leaves the installed tile working (the data stays until both names are gone).
- Tile info shows "Installed: yes (linked, no extra space)" or "(separate copy)". The estimate counts no installed MB for links.
- The 6 existing installs were converted from copies to links (36 MB freed).

### Parallel builds (2026-11-20)
- Measured: one build peaks at 4.6 GB RAM (+06-076 Medellín) / 2.8 GB (+12-082). Memory, not CPU, is the limit. This PC has 8 physical cores and 33 GB RAM.
- `forest/resources.py`: workers = min(physical cores − 1, (free RAM − 2 GB) ÷ peak per build, 8), recalculated at every job start. The peak per build is learned from history (`peak_gb`, 90th percentile).
- App jobs: data first (satellite downloads 4 at a time, OSM in one pass), then a pool of N build subprocesses. Log lines are tagged `[tile]`; cancel stops all of them; failures don't stop the others. The app has "Builds at the same time: Auto (N) / 1…max" with an explanation, and the estimate accounts for it.
- Result: 6 tiles in 89 s with 3 workers, vs 218 s one at a time; free RAM never below 9.7 GB.

### Deleting tiles (2026-11-20)
- Tiles tab → **Delete N tiles…** for the selected tiles that have anything. The dialog offers: uninstall from the `forest_*` pack, delete build output (`out/`), delete downloaded data (`cache/tiles/<tile>`), each with tile count and MB. Nothing is deleted until confirmed.
- `Api.delete_preview()` / `Api.delete_tiles()` refuse non-`forest_*` packs and any path outside the pack, `out/` and `cache/tiles/`. They also remove empty folders they leave behind and are blocked while a build runs.
- Fixed: `Tile.cache` created folders on read. Checks now use `Tile.cache_path`. 238 empty folders the estimate had created were removed; real tile data untouched.

### Plantations, one-pass OSM, estimates (2026-11-20)
- **Plantations** (`[[plantation]]` in the rules; the first matching rule wins):
  - Matched on OSM tags (`key=value`, `&` = all of) of orchards, plantations, vineyards, farmland with crop/trees/produce, and forests with leaf_type/genus.
  - Rules: oil palm (date-palm trees), coconut, banana/plantain, coffee (shrubs with shade trees), rubber, pine/cypress, eucalyptus, "no trees" (sugar cane, pineapple, rice, corn, soy…), and other orchards.
  - Inside a plantation the biome forest is replaced and trees are planted regardless of the satellite tree share (at the rule's density); `asset = ""` clears trees.
  - Plantations may use evergreen single-species sets (`lib/vegetation/trees/...`).
  - Overlay colour per rule; stats `plantation_km2`; editor in the Forest types tab (reorder, tags, tree type, thickness).
- **One-pass OSM:** `forest fetch --tiles a,b,c` (and every app job) reads the Colombia .pbf once and streams each element into every requested tile it touches (cache format v3). 6 tiles: 149 s total, instead of ~140 s per tile.
- **Estimates:** `cache/history.jsonl` records the seconds and MB of every raster download, OSM pass and build. `Api.estimate()` uses the medians (defaults until measured). The app shows time, cache/output/installed MB and free space under the Build button, and asks for confirmation for ≥ 10 tiles or > 30 min. App jobs now fetch data for all tiles first, then build each; a failed tile doesn't stop the batch.
- Rough current estimate for all 244 land tiles in the map grid (Colombia + neighbours): ≈ 5.4 h, ≈ 18 GB cache, ≈ 2.2 GB installed. Colombia alone (~115 tiles) is about half.

### Mangroves fix (2026-11-20, SKCG test)
- **Problem:** mangrove strips along Cartagena's lagoons had no trees. WorldCover puts mangroves in their own class (95), and we only counted class 10. OSM water polygons (lagoons, ciénagas) also cut them. Meanwhile the coarse RESOLVE "mangrove" ecoregions reached far inland and gave inland dry forest the mangrove type.
- **Fix:**
  - Tree share = WorldCover classes 10 + 95.
  - Cells with ≥ `mangrove_min_frac` (0.3) mangrove → `mangrove` biome, exempt from the OSM water cut-out and from the tidy-up filter (so narrow fringes survive).
  - The mangrove biome now has **no ecoregions** (WorldCover only). The RESOLVE mangrove ecoregions moved to dry_forest (Caribbean) and rainforest (Pacific).
- **Result:** real mangroves at +10-076 (Cartagena) 131 km² placed along shores; inland teal gone. +11-075 (Santa Marta / Ciénaga Grande): 2.6 → 60.5 km² of mangroves. +12-082 (San Andrés): 1.1 km². All three rebuilt and installed.

### X-World forests back on (2026-11-20)
- `simHeaven_X-World_America-7-forests` is **re-enabled**. `forest_colombia` sits above it (and above 4-extras), so on built tiles our tile-wide `sim/exclude_for` hides all X-World and default forests. Unbuilt tiles keep X-World's forests as before. No need to build the whole continent; expect style seams at 1° borders between built and unbuilt tiles.

### OpenStreetMap trees (2026-11-20)
- **Problem:** the golf course south of SKMD (and parks and gardens generally) gets no trees. WorldCover sees only 26% trees there; narrow tree lines lose to grass at 10 m.
- **Added:**
  - The OSM extract (cache format v2, re-extracted automatically) now also keeps `natural=tree` nodes, `natural=tree_row`, `natural=wood`, `landuse=forest`, and `leisure=park|garden|golf_course|nature_reserve`.
  - Inside those green areas the tree threshold drops to `osm_green_min_frac` (0.25).
  - Single trees → X-Plane **point forests** (fill 512, grouped per chunk); tree rows → **line forests** (fill 256). Both use `single_tree` from the rules (default `mixed/very_hot`, evergreen) and are never thinned by the budget. Trees inside buildings, runway strips or existing forest are skipped.
- **+06-076:** 9,555 single trees placed (of 14,003; the rest fall inside forest, buildings or runway strips), 535 tree rows (≈7,000 trees). DSFTool accepts both. Installed.
- App: new "OpenStreetMap trees" settings group, a single-tree type picker in Forest types, and yellow "OSM trees" in the overlay legend.

### Pack renamed (2026-11-20)
- `Custom Scenery/forest_poc` → **`Custom Scenery/forest_colombia`**, and the same line changed in `scenery_packs.ini` (same position: above X-World 4-extras). The app and CLI now install there by default. Older notes below still say `forest_poc`.

### Pack order and the X-World "orange city trees" (2026-11-20)
- The orange trees between houses in Medellín came from **X-World `4-extras`**: 26,977 `random_tree` areas per tile, plus hedges/shrubs/mixed. Confirmed by an A/B test with 4-extras disabled. They did not come from Medellín City (landmark buildings only) or from our forest.
- **New order:** `forest_poc` sits **above `simHeaven_X-World_America-4-extras`** (below 3-details). Our tile-wide `sim/exclude_for` now also hides X-World's random trees, hedges, shrubs and `6-scenery`'s `osm_tree` on our tiles. X-World buildings, roads, grass and cemeteries are unaffected; `7-forests` stays disabled.
- Side effect: towns get almost no trees, because our build leaves built-up areas empty. **TODO:** add an optional "town trees" layer (scattered evergreen single trees in built-up areas and parks, with its own density setting).
- `Tropical_Trees_Fix` is disabled. It was turned off between 13:00 and 15:13, outside Forest Studio's edits. Ini backups are in `cache/backups/`.

### Cut-out rules v2 (2026-11-20)
- **Problem:** in Medellín the cut-outs removed **40%** of real tree cover (roads 27%, buildings 16%). Any road or building that merely *touched* an 18 m cell cleared it, and the buffers added 4–6 m per side. At SKRG, the whole OSM aerodrome polygon + 120 m was cleared, removing the gully forest inside the fence (25% of trees there).
- **Fix:**
  - Cut-outs are drawn at 2× detail (~9 m); a cell is cleared only if they cover ≥ `cutout_min_cover` (0.5) of it.
  - Road half-widths ≈ carriageway + 1 m, times `road_clearance_scale`. Building margin = `building_clearance_m` (2 m).
  - Airports: only runways (±`runway_clearance_m` = 75 m from the centre line), taxiways (±20 m) and aprons (+5 m) are cleared. The whole grounds only with `clear_airport_grounds = 1`.
- **Result:** removal **40% → 17%** in Medellín and **25% → 5%** at SKRG. +06-076 rebuilt: 88,295 polygons, budget scale 0.783, about 51 s. Installed.

### DSF facts (X-Plane "DSF Usage" spec)
- Forest polygon param = density 0–255 + fill code (0 solid, 256 line, 512 points). CCW exteriors, CW holes.
- `sim/exclude_for` is rectangle-only: `west/south/east/north`. For finer exclusions, use many rectangles.
- **XP12 season rasters** in DSF: `spr1 spr2 sum1 sum2 fal1 fal2 win1 win2` = day-of-year start/end of each season, all 8 required. Art blends between the ranges. So seasons depend on location, not just date, and could be set for Colombia's wet/dry cycle. **Open:** does XP read season rasters from an *overlay* DSF, or only from the base mesh? Test once DSFTool is available.

### Decoded tiles (DSFTool 2.4, from `xptools_win_24-5/`, gitignored)
- **simHeaven `7-forests` +06-076:** 1,606 forest polygons. 1,325 are `mixed.for` at density 255; the rest are orchard at 180, shrubs at 120, and a few broad/coni. Plus 255 `sim/exclude_for` rectangles. So in this tile X-World is essentially one forest type.
- **X-Plane 12 base mesh +06-076:** about 27,000 forest polygons, almost all density 255 (param 255; 257 = line fill). They use `lib/g8/*_{wrm,hot,vhot}_{rain,wet}` types, e.g. broad/coni/mixed `_sp`, `rainforestm_sp_vhot_rain`, `grass_rainforest`, `orch_hot_rain`, wetlands, shrubs.
- **Season rasters in the base mesh:** 128×128, 1 byte per cell, `scale=1.994536` → day of year, `flags=6`.
  - **Rows run south → north** (row 0 = south edge). Verified with the elevation raster: SKRG 2,121 m, Medellín 1,488 m.
  - About 83% of cells hold an "all-year" style pattern (`sum1=365`...). **But the cells at Rionegro hold real seasons:** spring 44–48, summer 52–124, fall 128–132, **winter 136 → 40** (mid-May to early February). Same in the base mesh and in the OrthoStudio tile. My first reading ("perpetual summer") used the wrong row order and was wrong.
  - **Where real seasons appear (+06-076, by elevation):** none below 1,000 m. 4% of cells at 1,000–1,500 m, 23% at 1,500–2,000 m, 27% at 2,000–2,500 m. In those cells winter averages about 170–200 days. So X-Plane gives Andean highlands (the Rionegro/Medellín plateau) temperate-style seasons. Likely reason (inferred, not documented): the season maps come from climate data, and a cool highland climate with steady ~16 °C, classified like Köppen Cfb/Cwb, gets treated like a temperate climate with winters.
  - **In-sim confirmation (2026-11-20, day 324):** the `broadleaves/warm.for` patch looked bare and wintery; `conifers/warm` and the evergreen rainforest looked fine. The default XP12 scenery has the same problem in the Andes, because its `lib/g8/*_wrm_*` forests are seasonal.
- **zOrthoStudio +06-076 (your ortho mesh):** base mesh with no forests at all, only terrain plus XP12 rasters (incl. seasons). Because it replaces the default base mesh, the default forests disappear on ortho tiles. Forests there come only from overlays (formerly X-World 7-forests, now ours), so **our overlay has to cover the whole tile**, not just patches.
- **Our writer is verified:** `forest poc --compile` builds a valid DSF, and it decodes back identically. DSFTool writes `I` as the header; the readme says `A`. Both work.

### Done in Phase 0
- Python package scaffold (stdlib-only so far): `forest catalog`, `forest poc [--compile] [--season spr|sum|fal|win]`, `forest decode [--label]`.
- PoC overlay +06-076 compiled: `out/forest_poc/`. Three ~1 km patches about 1.5–2 km west of SKRG: broadleaf warm (SW), rainforest (SE), conifer warm (NW), each with its own `exclude_for`.
- Season-raster writer exists (`forest poc --season win`) but is parked; see §7.

### Still open
1. **In-sim test: installed 2026-11-20.** `Custom Scenery/forest_poc/` (DSF only) sits below `simHeaven_X-World_America-8-network` and above `Tropical_Trees_Fix` and the Vegetation Library. `simHeaven_X-World_America-7-forests` is set to `SCENERY_PACK_DISABLED`. Backup of the original ini: `cache/backups/scenery_packs.ini.20261120-130043`. Check that the three patches render near SKRG. With the ortho tile, expect no other forests on +06-076.
   - **Result, round 1:** the patches render. As expected for plain squares, trees cover roads, houses and open fields → needs the Phase 1 tree mask. `broadleaves/warm` looked wintery (season rasters, §7).
   - **Round 2 (installed 13:10):** SW `broadleaves/very_hot`, SE `rainforest_vhot_rain`, NW `conifers/hot_dry`, all evergreen. To verify: no winter look.
2. ~~Season test~~: deferred (no seasons for Colombia, see §7). Revisit in Phase 4 for seasonal regions.
3. Python: the installed 3.15 is very new and may not have geospatial wheels (GDAL/rasterio) yet. Use a 3.12/3.13 venv for Phase 1.
4. Earth Engine account + Cloud project for Phase 1.

## 11. Risks

- **The `.for` mapping is only as good as the default asset variety.** It may not look truly Colombian until custom assets come in Phase 4.
- **Season control in XP12 may be limited.** Not relevant for Colombia (§7), but it matters when expanding to seasonal regions.
- **Earth Engine quotas and export times** for many tiles. Mitigation: per-tile caching and exporting at the coarsest resolution that's enough (20–30 m is often fine).
- **Conflicts with X-World or Ortho4XP forests:** if layer order or exclusions are wrong, trees get drawn twice.
- **Cloud cover over Colombia** affects any custom Sentinel-2 work. That's why we use composites and existing products first.

### 2026-11-21 — Data folder moved out of the repository

- Everything that changes now lives in `~/.foreststudio` (`config.DATA_ROOT`, override with
  `FORESTSTUDIO_HOME`): `config/local.toml`, user profiles/rules (seeded from the repo defaults,
  never overwritten), the generated catalog, `cache/` and `out/`. The repo is code + defaults only.
- `forest/migrate.py` moves the old in-repo data on start (app and CLI) and re-points our
  `Custom Scenery/forest_<region>` junction. Run on this PC: 3.0 GB moved (same drive, instant),
  junction now → `~/.foreststudio/out/forest_colombia`, 34 tiles visible, 5/5 setup checks.
- UI: DSFTool setting removed (bundled per platform), Build log tab removed (log lives in the
  Build queue, stays open while it updates), app icon in the header.
- Settings → **Data folder**: pick another folder (e.g. another drive) or go back to the default;
  data is moved (or switched to an existing data folder), X-Plane links re-pointed, the choice kept in
  `~/.foreststudio/location.toml`, then the app restarts. `FORESTSTUDIO_HOME` overrides and locks it.
