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

**Phase 0: proof of concept — done 2026-11-20** (findings in §10).

**Phase 1: one tile, end to end — done 2026-11-20.**
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
  - Installed as `Custom Scenery/forest_poc/.../+06-076.dsf`, replacing the PoC squares.

**Phase 2: quality — mostly done.** OSM plantations and mangroves, the Colombian biome table
(rainforest, montane, dry forest, xeric, Llanos savanna, mangroves, páramo), airport clearances,
scrub and town trees are in (§10 and the dated entries below). Left: spot checks against imagery at
SKBO, SKCL and SKLT (SKRG and SKCG done). Not planned: IDEAM, Dynamic World; canopy height is not
wanted (owner, 2026-11-21).

**Phase 3: all of Colombia — done.** Parallel builds with caching, profiles tuned by the owner's
FPS checks (2026-11-23), one-click install as a linked pack. Resuming interrupted jobs is not wanted.

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
- Side effect: towns get almost no trees, because our build leaves built-up areas empty. Solved by the town-trees layer (2026-11-21).
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

### 2026-11-21 — Open-source readiness, Data view, outdated tiles

- `README.md` for users (install, first run, scenery order, views, credits); pyproject readme →
  README.md, `license-files = ["LICENSE"]`.
- Credits: `forest/credits.py` → Settings → About and `CREDITS.txt` in every built pack (ODbL needs a
  notice on produced works; ESA/Hansen/RESOLVE are CC BY 4.0).
- CI: GitHub Actions on Windows (3.11, 3.13), Ubuntu, macOS (3.12).
- macOS/Linux: bundled DSFTool for both (from XPTools 24-5 mac universal / linux x86-64), symlink
  install, extra X-Plane detection paths. Untested on real machines.
- Select outdated: build stats now carry a settings fingerprint + data dates; dashed outline on map.
  All 34 existing tiles show as outdated (built before fingerprints).
- Data view: OSM country file / ecoregions status, download, check for update (HEAD to Geofabrik)
  and update (+ re-extract tiles), per-tile data table, download for map selection. Found that the
  app never downloaded the .pbf / ecoregions itself (they had been fetched by hand): fetch now
  downloads them when missing. First update check: Geofabrik's file was ~6 h newer than ours.
- Dropped from the plan by the owner: "select all Colombia" (manual selection) and tree height.

### 2026-11-21 — Scrub and town trees

- No new downloads: WorldCover classes 20 (shrubland), 50 (built-up) and 60 (bare/sparse) were
  already in every tile's raster. OSM extraction now also keeps `landuse=residential`,
  `industrial/commercial/retail` and `natural=scrub/heath` (`osm_pbf.VERSION` 4 → every tile is
  re-extracted once from the local .pbf, ~140 s for the pass). `BUILD_VERSION` 2.
- Scrub: shrubland share ≥ `scrub_min_frac` (+ bare land in xeric via `scrub_on_bare`, OSM scrub
  = 0.7), per-biome bush type (páramo none). Grassland deliberately not used (cattle pasture).
- Town trees: random points in built-up/residential cells, dropped on buildings/roads/runways/
  corridors, trimmed to `town_trees_per_km2` (oversampled 3×: 36 % of spots hit buildings/roads in
  Medellín, 54 % in Barranquilla). Per-biome type: dry_forest/xeric use broadleaves/hot_dry
  (palms, kapok, agave), elsewhere mixed/very_hot.
- Measured on real tiles (medium, temp data folder): +06-076 Medellín 32.4k town trees in 216 km²
  of town, 21 km² scrub, 27.0M trees total (budget scale 0.837, unchanged), 72 s; +11-075
  Barranquilla/Santa Marta 26 km² scrub, 63 km² town, 6.7M trees, 30 s. Both compiled.
- Older user rules/profiles get the new keys from the shipped defaults (`fill_rule_defaults`,
  `PROFILE_DEFAULTS`), so nothing has to be re-saved.

### 2026-11-21 — Xeric biome gets dry-forest trees

- Owner's screenshots of +09-076 (Sucre coast): photo shows tree canopy, sim showed tiny bushes.
  Stats: 1,264 km² of forest in the xeric biome, 1,054 km² of it as `g8/shrb_vhot_dry` (agave,
  yucca, dead bush, stones) because xeric used shrub types for dense/sparse *forest*. The RESOLVE
  xeric-scrub ecoregion is coarse and covers the coastal dry forests; WorldCover (≥ 50 % trees)
  was right. +11-075 had ~500 km² the same way (Tayrona dry forest).
- Fix: xeric dense = `broadleaves/hot_dry.for`, sparse = `g8/broad_sp_hot_sdry.for` (defaults and
  the owner's rules, backup in cache/backups); `shrb_vhot_dry` stays as xeric's scrub type.

### 2026-11-21 — First FPS report

- Owner flew the FlightFactor 777 over built tiles with the **medium** profile
  (`tile_tree_budget` ≈ 27M): a steady 59+ fps. That is probably the 60 fps cap (vsync/limiter),
  so the real headroom is unknown; GPU/CPU frame times (X-Plane data output) would show it.

### 2026-11-21 — No town trees or scrub on airport grounds

- Owner's SKRG screenshot: lone trees right beside the parallel taxiway. Read-only check on the
  cached +06-076 data: 46 town trees inside the SKRG boundary, all 20–35 m from a runway/taxiway
  centre line (WorldCover marks airport pavement as built-up → the grass in between became
  "town"; taxiways only clear ±20 m); 296 scrub cells too. The 39 OSM-mapped trees inside SKRG
  are ≥ 54 m away (real trees near the fence) and stay.
- Fix: OSM `aeroway=aerodrome` outlines (`hints["airports"]`) are excluded from town cells and
  scrub and block town-tree points. After: 0 town trees and 0 scrub cells in SKRG. 92 forest
  cells remain (satellite tree cover inside the fence; cleared only with `clear_airport_grounds`).
  `BUILD_VERSION` 3.

### 2026-11-21 — Code review: bug fixes, faster builds, cleanup

- Bugs fixed (commit 37ac0c7): parallel builds raced saving the job record (PermissionError on
  Windows, reproduced 49× in 30 trials); build-thread errors vanished; an apostrophe in the X-Plane
  path broke local.toml; two quick clicks could start two jobs; cancel left DSFTool running; a
  failed status call froze the progress bar; the Overpass fallback lacked the tree-hint features.
- Faster builds, same tiles. Medellín (+06-076) 72 s / 5.31 GB peak → 56 s / 3.84 GB; San Andrés
  (+12-082) 19 s / 4.31 GB → 12.5 s / 2.24 GB. Verified by rebuilding both tiles before/after in a
  scratch data folder: same polygons (type, density, vertices), identical overlay/preview PNGs and
  stats; the text compiles with DSFTool (13.3 MB, same as installed). Where the time went
  (profile): shapely `__geo_interface__` inside rasterize (~14 s; now numpy → GeoJSON dicts,
  burnt in batches), clipping every polygon to the tile (~7 s; now only those crossing the edge —
  the clip kept the same vertices but restarted the rings, so the text is not byte-identical),
  per-geometry buffers (~10 s; now one shapely call per category — note shapely.buffer's default
  quad_segs is 8, Polygon.buffer's 16), 5 WorldCover reprojections (now a k×k block count when the
  grid is exactly 1 or 2 source pixels per cell). Memory: osm.json parsed once and freed before
  rasterising, full-tile arrays freed when done, uint8 level indices, int32 cell codes.
- Measured and **not** changed: repairing invalid OSM polygons (43 of 2.55 M buildings, 0.03 km²
  in 35 tiles — not worth it). **Parked** (changes tiles, owner decides later): OSM multipolygon
  holes are dropped — 511 km² of river/lake islands are cut as water in the 35 cached tiles, ~41 km²
  of it forest on +07-074 (Bajo Cauca) and ~42 km² on +06-075 (Magdalena Medio); needs
  `osm_pbf.VERSION` + `BUILD_VERSION` bumps.
- Cleanup: ruff in dev deps + CI, dead code removed, shared helpers, clear error for a forest type
  missing from the X-Plane library, job log kept open while running, cached job summaries and
  ecoregion names, devserver honours `PORT`.

### 2026-11-23 — FPS result, OSM mangroves, DSFTool memory

- **FPS (owner, frame limiter off):** about 100 fps with medium at 27M, and 90–100 fps at
  `tile_tree_budget` 35M with `global_density` 0.75 over different cities. The shipped medium
  profile is now **30M / 0.75** (was 27M / 0.7); the owner's own profile is unchanged (35M / 0.75).
- **Missing mangroves at SKCG** (owner screenshots: the Juan Angola canal through Crespo, and the
  wetland between the runway and the Ciénaga de la Virgen). Read-only check of +10-076 in a scratch
  data folder:
  - WorldCover calls most of these mangroves herbaceous wetland (90), grass (30) or water, so
    they got no trees; OSM maps them as `natural=wetland` + `wetland=mangrove` (42 areas in the
    tile, 2,097 / ~1,600 km² in Colombia), which the build did not read.
  - Where WorldCover did see mangrove, Hansen's 2000 tree cover was ~5 %, so density
    (0.6 WorldCover + 0.4 Hansen) stayed low: 79 % of the tree cells east of the runway were at the
    thinnest level and half the mangrove cells got the sparse shrub type.
  - Fix: OSM mangroves are extracted (`osm_pbf.VERSION` 5, Overpass query too) and count as
    mangrove cells (mangrove biome, never cut by water); inside them grass, wetland and shrubland
    count as half trees (`OSM_MANGROVE_LOW_VEG`) with the parks threshold, so channels, sand and
    built-up stay open. Mangrove density comes from WorldCover alone. `BUILD_VERSION` 4.
  - Result on +10-076: continuous mangrove fringe along the canal; east of the runway the dense
    mangrove type went from 835 to 1,686 cells and tree cells from 3,105 to 3,505; whole tile
    9.18 M → 9.32 M trees. Medellín (+06-076, no mangroves) rebuilt identical apart from metadata.
- **DSFTool memory**, now measured on every build (`proc.run_measured`, history fields
  `build_peak_gb`, `rss_at_compile_gb`, `dsftool_peak_gb`; `peak_gb` = the larger of the build's
  own peak and what it still holds plus DSFTool's): Medellín 0.27 GB DSFTool + 0.40 GB held vs
  3.73 GB build peak; Cartagena 0.10 GB. DSFTool never sets the limit for parallel builds.
- Duplicated helpers merged into `forest/fsutil.py` (`size_bytes`, `size_mb`, `move_merge`);
  `OverlayTile.name/folder` use `Tile`.

### 2026-11-23 — Regions are data files (step 1 of multi-region)

- Plan for other regions (owner asked before a public release): (1) a region becomes a data file,
  (2) global default forest rules by RESOLVE biome so any region works without hand-tuning,
  (3) evergreen-only as a region setting, (4) a first test region (a tropical neighbour, later a
  temperate one for seasons), plus big OSM extracts (sub-region files) and border tiles.
- Done now (1 and 3): `config/regions/colombia.toml` + `forest/regions.py`. The map tiles, the OSM
  extract URL/file, the ecoregion cut-out box and file (`cache/ecoregions/<region>.geojson`), the
  setup checks, the Data view and the OSM update (only the region's tiles are re-extracted) read
  the region; `fetch_osm`, `ecoregion_raster`, `build_clip` take it. `evergreen_only` decides
  whether saving rules and building accept seasonal types; the forest-type pickers offer seasonal
  types only in regions that allow them. The region picker shows labels and the map fits the
  region.
- Verified: +10-076 rebuilt in a scratch folder before/after — DSF text, overlay and preview PNGs
  byte-identical, stats equal except build time; the Colombia ecoregion cut-out remade from the zip
  is byte-identical to the one made by the old code. The app (dev server) shows 288 tiles, 59
  ecoregions, 5/5 setup checks.

### 2026-11-23 — Other regions: global rules, Geofabrik regions, border tiles

- Owner wants to try other regions. Done: (1) global default forest types by RESOLVE biome,
  (2) Data view → Regions built from Geofabrik's index (continent → country → state, sizes by HEAD,
  Add / Download OSM / Remove), (3) border tiles read every downloaded extract covering them,
  (4) end-to-end test of Panama (tropical) and Luxembourg (temperate) in a scratch data folder.
- `config/rules/_global.toml`: 15 biomes (14 RESOLVE + mangrove from WorldCover/OSM + default).
  Tropical biomes reuse the Colombia evergreen types; temperate/boreal/Mediterranean use XP12's
  seasonal types (`broadleaves|mixed|conifers/{temperate,cold,cold_low,warm_dry}`, `lib/g8/*_tmp_*`).
  `[evergreen_swap]` maps each seasonal type to an evergreen one for evergreen-only regions.
  Every asset exists in this install's catalog; the swapped template has no seasonal types left
  and both versions pass `validate_rules`.
- Evergreen-only for added regions = outline centroid within ±23.5° (Panama yes, Luxembourg no).
- First Panama build put 74 km² in `default`: RESOLVE "Mangroves" ecoregions have no biome in the
  template (in Colombia they were reassigned by hand because they reach inland). Fix: tropical_moist
  also takes RESOLVE "Mangroves"; the real mangroves still come from WorldCover/OSM. Left: 34 km² of
  coast/islands outside every ecoregion polygon (default = evergreen swap there).
- Results: Panama +08-080 986 km² forest, 4.3 M trees; Luxembourg +49+006 3,418 km², 15.9 M trees,
  381k conifer-plantation cells from OSM `leaf_type=needleleaved`; +07-078 (Colombia/Panama) from
  both files: 7,899 + 1,221 elements, 138 in both, 8,982 written. OSM pass with two files: 147 s.
- Colombia unchanged: +10-076 DSF text + PNGs and +06-076 compiled DSF + PNGs byte-identical.

### 2026-11-23 — One map and one pack for every region

- Owner chose (asked: "less tedious for the final user"): one world map with every region's tiles,
  built together, and one X-Plane folder instead of one per region; adding a region downloads its data
  at once (no extra Download click).
- Tile owner = region whose Geofabrik outline covers most of the tile (`geofabrik.tile_shares`);
  +07-078 stays Colombia's, +08-080 is Panama's, Quito (-01-079) is Colombia's until Ecuador is added.
  Each tile builds with its owner's rules (`forest build --region <owner>`); a job can mix regions.
- Pack `out/forest_studio_xp` (+ `out/studio`). Considered editing scenery_packs.ini to put the new
  pack where `forest_colombia` was: rejected (rule 2, ini only on request). Instead the migration
  re-points the existing `forest_colombia` junction at the new pack, keeping its ini position; new
  installs get `forest_studio_xp`. Other old region links are removed (duplicate tiles otherwise).
- Data view: the per-region OSM card became a column of the Regions table; `forest data region`
  (OSM file + ecoregions + cut-out + re-extract covered tiles) runs right after Add region
  (Liechtenstein: 4 MB, 16 s).
- Verified on the scratch copy with a fake X-Plane folder (real Resources/Global Scenery linked
  read-only): migration merged Colombia (+06-076, +07-078), Panama (+08-080), Luxembourg (+49+006)
  into one pack, the fake `forest_colombia` link now points at it, ini unchanged; one job built
  +06-076 (colombia) and +08-080 (panama) at the same time; Add region → download job started.

### 2026-11-24 — Download progress in the Regions table

- Owner: after Add region the row said "not downloaded" + Download while the download was already
  running. Now the row shows "Downloading the OpenStreetMap file… 45 % (28 MB)" with a bar (then
  "getting it ready…", then the file's size/date). Progress comes from the job runner
  (`Job.progress`, parsed from `download OSM: N MB P%`, printed per 1 %, one log line per 10 %).
  Checked on the scratch copy adding Costa Rica (39 MB, 24 s).

- Same day: building a Florida tile only said "running · satellite data ready" while the job
  downloaded Florida's OSM file and read it. The data step now reports its phase in plain words
  (download with %, satellite maps n of N, reading OpenStreetMap) in the Tiles tab, the Data view and
  each tile's Data step in the Build queue. Checked on the scratch copy (Liechtenstein file removed,
  then its tile built: download 29/59/89 % → satellite maps → reading OSM → building).

### 2026-11-24 — Medium thickness 0.8

- Owner compared +30-082 (Florida) with simHeaven: ours thinner (avg density 171 vs 255). Medium
  `global_density` 0.75 → 0.8 (shipped default and the owner's copy). Built medium tiles show as outdated.

### 2026-11-24 — Trees by leaf type and climate (new regions)

- Owner compared +30-082 (Jacksonville) with simHeaven: our tile was 82 % `broadleaves/warm_dry`
  because RESOLVE files "Southeast US conifer savannas" under temperate *grasslands*, which the global
  template mapped to broadleaves. simHeaven: mixed 866 km², conifers 560, wetland 526 at density 255
  (climate zone wrm → `1200 forests/<season>/{mixed,coni,wetl_wdy}_wrm.for`), plus 688k random trees in
  4-extras that our tile-wide `exclude_for` hides (X-Plane can't exclude only some forests).
- General fix: `climate = true` biomes pick `lib/g8/<family>[_sp]_<temp>_<moist>.for` (the same files
  as simHeaven's) from Copernicus LC100 forest type (leaf) × Köppen-Geiger 1 km (climate). Downloads:
  leaf type = window of a 1 GB Zenodo GeoTIFF (tiled, 6 s, ~0.1–1 MB per tile); climate = 131 MB zip
  once, 12 MB kept. All 168 names exist in X-Plane's library. On for every template biome except
  mangrove; Colombia's hand-made rules unchanged (same fingerprint, no rebuild needed).
- +30-082 rebuilt in a scratch folder: conifers (coni_wrm_wet) 2,535 km², mixed_wrm_wet 502, pine
  plantations 546, bushes shrb_wrm_wet; Copernicus calls 64 % "unknown/mixed", 36 % conifer, 0 % broadleaf.
  Not done: wetland (cypress swamp) forest from OSM `wetland=swamp` (needs an osm_pbf VERSION bump,
  which re-extracts every tile).

### 2026-11-24 — Every X-Plane vegetation type (climate biomes)

- Owner: "map all of the possible vegetation types … as realistic as possible". Added to climate
  biomes: OSM `leaf_type` override, cloud forest (rainforestm) in cool wet tropics, swamp forest
  (wetl_wdy) in OSM swamps, savanna trees (sparse_tr) for thin forest in hot dry climates, wetland
  bushes (wetl_shrb), climate orchards (orch_*), olive groves (template rule), climate broadleaf
  town / single / row trees. Template: OSM `leaf_type=needleleaved` woods are no longer a pine
  plantation. `osm_pbf.VERSION` 6 (all wetlands), `BUILD_VERSION` 5 → every tile re-extracts / shows
  outdated once; Colombia's trees don't change (its biomes have no `climate`).
- First try put wetland bushes on every marsh: +30-082 got ~490 km² of bushes on Jacksonville's salt
  marshes (grass in reality; X-Plane's wetl_shrb has small trees). Now only where WorldCover also sees
  shrubland, and salt marsh / reed beds are excluded → 0 km² on this tile.
- +30-082 (scratch, fresh default rules): coni_wrm_wet 2,377 km², swamp forest 565 (simHeaven: 526
  wetland), mixed 497, bushes 34, OSM-broadleaf 4; 23.9 M trees. OSM leaf_type covers ~770 km².

### 2026-11-24 — In-program workers instead of CLI subprocesses (#8)

- The app no longer starts `python -m forest.cli …` and parses its log lines. Each task (fetch, one
  tile build, OSM update, region download, ecoregions) runs in its own fresh `multiprocessing` (spawn)
  process from `forest/worker.py`; the work moved from `cli.py` into `forest/tasks.py` (CLI = wrapper).
- Progress is structured events (`forest/events.py`) over a pipe: job steps and the data phase no longer
  depend on the wording of log lines (`JobRecord.track` / `Api._data_phase` and their regexes removed).
- Errors come back with their traceback; a worker that dies without a word fails only its tile
  (`crashed`); cancel stops the worker's process tree (DSFTool included).
- No console windows: Windows workers run on `pythonw.exe`; `freeze_support()` ready for PyInstaller.
  Checked a zip-launched main (like the `foreststudio-xp.exe` launcher) doesn't re-run in the worker.
- Measured (scratch data folder): +30-082 69.1 s through the app = the CLI build; data 1.7 s,
  classify 46.7, vectorise 9.9, compile 2.7. Cancel at 20 s → job cancelled, no leftover workers/DSFTool.
- Tests: `tests/test_worker.py` (order of events, traceback, crash isolation, stop, pythonw, Api._task).

### 2026-11-24 — Countries' data side by side; build as soon as a tile's data is ready

- Owner question: why does a Florida tile wait for Colombia's OSM file? Before, a job had ONE data
  step (all country files read one after another) and builds started only after it. Same-country tiles
  must share one pass over their file (reading 335 MB per tile would be far slower), different
  countries don't.
- Now `regions.osm_groups` splits a job's tiles into groups sharing no OSM file (union over the files
  each tile needs, downloaded or not; border tiles join their countries). `tasks.prepare` downloads the
  shared ecoregions zip / climate map first. Each group's fetch runs in its own worker at the same
  time; its tiles' builds are queued the moment it ends. Data steps and builds share the N slots.
- Also fixed: the "Reading OpenStreetMap (colombia…)" note landed on every waiting tile (Florida's
  too); `osm_reading` and `osm_done` now carry their `tiles`.
- Measured (scratch, 2 slots): +06-076 + +30-082 with both OSM files to read → 375 s. OSM passes
  137 s (Colombia, alone 135) and 309 s (Florida, alone 305): no slowdown side by side. Peaks 1.39 /
  3.05 GB. +06-076 was built (63 s) while Florida's file was still being read. Old order ≈ 510 s.

### 2026-11-24 — OSM country files read ~2.3× faster (same output)

- Measured where a pass over colombia-latest.osm.pbf went: decoding + storing every node location
  in C++ 3.7 s; a Python call per node/way just to reject it ~47 s; our tag tests 18 s; building
  each wanted way's coordinates node by node in Python ~63 s (for every road/building of the whole
  country, before knowing whether it touches a requested tile); json.dumps ~7 s. The second read
  osmium does for multipolygons is cheap (relations only: 0.2 s).
- Fix 1: osmium C++ filters before Python (`osm_pbf.PREFILTER_KEYS`): only `natural=tree` points,
  and ways / relations / areas with one of highway, building, natural, landuse, waterway, aeroway,
  railway, power, leisure. The Python tests still decide. Guard test: every key the tests read is
  listed; with and without filters the osm.json is byte-identical (tiny file with a multipolygon).
- Fix 2: a wanted way's coordinates come from osmium's WKBFactory (C++, `use_nodes.ALL` keeps
  repeated nodes) read with numpy; a way with a missing location or < 2 points raises and takes the
  old per-node path (`_way_slow`). Test: both paths write the same bytes, odd cases included.
  (Tried and dropped: shapely bounds of a WKB to skip far ways — slower, 106 s; taking each node's
  Location once — no gain.)
- Check (scratch, real country files): 9 tiles (Colombia ×5, Florida ×2, Colombia+Ecuador border
  +00-078, Jamaica) — same elements, same headers; only a few multipolygons sit at another place in
  the list (osmium hands assembled areas over in batches). Tiles built from old vs new osm.json:
  +06-076 and +30-082 DSF text and PNGs byte-identical. No VERSION bump: nothing is re-read.
- Times: Colombia 5 tiles 146 → 62 s, Florida 2 tiles 324 → 144 s, border 178 → 64 s, Jamaica 23 → 12 s.
- Incident: a first version of the new test called `monkeypatch.undo()`, which also undid the
  sandbox, so it wrote test data over the real +06-076 / +06-077 osm.json. Restored the same day
  with the committed code from the same country file, original mtimes put back from the tiles'
  stats (+06-076 up to date again); nothing else in the data folder changed. The test now patches
  inside `monkeypatch.context()` and asserts it writes under the sandbox. Never use undo() with the
  sandbox fixture.

### 2026-11-24 — No trees on building sites, car parks and ponds (+30-082, owner's sim check)

- Owner saw trees on car parks (town trees) and on a cleared sandy site with a pond near
  Jacksonville. Measured on the built +30-082 against Florida's OSM: 951 town trees inside
  `amenity=parking` areas (never extracted; WorldCover calls car parks built-up → town cells);
  2.0 of 6.7 km² of `landuse=construction` sites still had forest (satellite maps 2021–2024 predate
  the clearing); smaller amounts on sand, brownfields, quarries; 218 town trees in OSM water
  (water was only cut per cell, ≥ 50 % cover).
- Fix: extract car parks, landfills and bare land (`landuse=construction|quarry|brownfield`,
  `natural=sand|bare_rock`; `amenity` added to `PREFILTER_KEYS`; `osm_pbf.VERSION` 7). Bare land is
  a cut-out (`bare`); car parks and landfills are no-town-tree land; town trees are now dropped at
  the exact spot on water, bare land, car parks and industrial/commercial land (not only by cell).
  `BUILD_VERSION` 6 → every tile shows as outdated.
- Result (+30-082, scratch rebuild): forest on construction sites 2.0 → 0.02 km², town trees on
  car parks 951 → 0, in water 218 → 5. The trees left on car parks / sand (304 / 251) are all
  OSM-mapped `natural=tree` points (real, kept). Landfills keep their forest (0.34 of 4.7 km²: the
  wooded buffers inside the outline). Sites cleared but not mapped in OSM still get trees.

### 2026-11-24 — Pre-release review fixes, faster builds, leaner osm.json

An outside review of the work so far (B1–B3, N1–N8, P1–P8) was agreed with the owner in steps.

- **Step 1, fixes:**
  - B1: Leaflet 1.9.4 ships in `forest/app/ui/vendor/leaflet` (BSD-2, SRI-checked), so the map works offline and without a CDN.
  - B3: the Overpass fallback is gone. A tile no downloaded country file covers fails its Data step with "OpenStreetMap country file missing … Download it in Data → Regions". A tile that already has a current `osm.json` can still be built.
  - N3: GDAL HTTP retries (4 × 5 s) in `fetch_window`. Sea-only tiles (no base-mesh DSF) are refused when the build starts.
  - N5: one app window per data folder (`forest/app/single.py`, an OS lock on `<data>/app.lock`; a restart waits for it).
  - N1: the RAM peak is the 90th percentile of the last 30 builds (`resources.RECENT_BUILDS`).
  - N4: job views are read under the record's lock (deep copies).
  - N6: the dev server only answers JSON POSTs (415 otherwise).
  - N7: TOML strings are written with `config.toml_str`.
  - README: keep the `-e` install.
- **Step 2, same tiles, faster** (scratch rebuilds of +06-076, +10-076, +30-082 medium and +06-076 low, byte-identical DSF text and PNGs). Times 64.5/31.7/62.4/46.5 s → 49.8/20.6/48.3/33.7 s (−23…−35 %).
  - P1: k×k block counts from strided slices (5× faster).
  - P5: the low profile (4000 cells = WorldCover / 3) now takes the block path too, with a rounding table. GDAL's average equals round-half-up of 100·c/k² whenever no value ties at .5. Checked for k = 2, 3, 6; k = 4 ties and rounds either way, so k ∈ `_TIE_FREE` only. The high profile still reprojects, now from a cheaper 0/100 array. A 7-band reproject saved only ~1 s for ~1 GB more RAM, so it was not done.
  - P6: `majority3` = 9 shifted views ≥ 5 with symmetric padding (13× faster than `median_filter`).
  - P2: way geometries are built in bulk. Tree hints decide from tags before building a polygon (buildings no longer become polygons there) and check validity in bulk. The order and the filter places are unchanged: relation green areas still have no validity filter.
  - P3: bulk `shape()` in `vectorize`.
  - P4: one STRtree `within` query for OSM single trees.
- **Step 3, changes tiles** (`BUILD_VERSION` 7, every tile shows as outdated):
  - P7: `osm.json` VERSION 8 stores ways and rings as `[lon, lat]` pairs, 22–26 % smaller (+30-082: 282 → 211 MB), with the same numbers.
    - A version-7 file is converted in place on its next check (1–8 s per tile) and keeps its date. The converted bytes equal a fresh read from the country file (checked on +10-076).
    - Tiles built from it are byte-identical. gzip was left out for now (owner).
  - P8: building clearance buffers use 4 segments per quarter circle instead of 16. With 2 m the arcs differ by < 5 cm.
  - N2: point and line forests are written as they are, without `oriented()`. Closed tree rows keep their closing segment (87 rows on +06-076, 2 on +30-082). A lone tree's 3 padded points stay 3.
  - Measured against the step-2 output:
    - forest area unchanged (+30-082 +0.1 km² of 3,564);
    - estimated trees +0.001 %; polygons ±5;
    - building cut-out cells −0.1 to −0.3 % (−527 of 543,729 on +30-082);
    - OSM single trees ±2.
  - Town-tree counts are unchanged, but 35–45 % of the town trees sit at other random spots in the same town cells: a few candidate spots changed, so the seeded draw picks a different subset.
  - Times vs the baseline: 43.7/18.4/40.5/27.8 s (−32…−42 %).
- Not done: gzip of `osm.json` (later); B2 waits for the packaging work.

### 2026-11-25 — First run on a Mac; builds need an X-Plane folder

- First real run on macOS (Apple Silicon, macOS 26, Homebrew Python 3.15.2): every dependency has a 3.15 arm64 wheel, the app opens, all tests pass, and the bundled macOS DSFTool compiles (Gatekeeper did not block it).
- Found there: Build and "Download data" for tiles started without an X-Plane folder, downloaded the OSM file and satellite maps, then failed at the elevation step. Both now refuse at once with "Set your X-Plane 12 folder in Settings first" (`Api._xplane_missing`, same check as Settings). The other downloads (OSM file, ecoregions, region) still work without X-Plane.
- Not done yet: a real tile build on the Mac (needs `1200 forests` + a base-mesh DSF copied from the Windows PC).
