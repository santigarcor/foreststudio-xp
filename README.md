# ForestStudio XP

[![Tests](https://github.com/santigarcor/foreststudio-xp/actions/workflows/tests.yml/badge.svg)](https://github.com/santigarcor/foreststudio-xp/actions/workflows/tests.yml)

Better forests for **X-Plane 12**, built from real satellite and map data.

X-Plane's default forests are placed from coarse global land-use data, so they're often in the wrong
place, the wrong type, or missing. ForestStudio XP builds a forest overlay tile by tile from
10 m satellite land cover, tree-cover maps, OpenStreetMap and biome maps, using X-Plane's own
default tree models. It's a desktop app: pick tiles on a map, press **Build**, fly.

It ships with **Colombia** (hand-tuned forest types). Other countries or states can be added in
**Data → Regions** from Geofabrik's list of OpenStreetMap downloads: a new region gets the 1° tiles
its outline touches and default forest types that pick, for each spot, X-Plane's own forest for
the leaf type seen by satellite (conifers, broadleaves or mixed) and the local climate (very cold …
very hot, dry … rainforest), including swamp forest, cloud forest, savanna trees, wetland bushes,
orchards and town trees for the climate. You can edit them in Build → Forest types. This needs a 131 MB climate map
(downloaded once) and about 1 MB more per tile. Regions between the
tropics use evergreen trees only; others use X-Plane's seasonal forests. Tiles on a border read
every downloaded country file that covers them.

## What you get

- **Forests where they really are**: trees follow ESA WorldCover (10 m) and Hansen tree cover,
  minus recent forest loss.
- **The right kind of forest**: rainforest in the Amazon and Pacific, montane forest in the Andes,
  dry forest, savanna gallery forest in the Llanos, mangroves on the coasts, páramo up high. All of
  them are evergreen, so the Andes don't turn wintery for half the year.
- **Clear of roads, buildings, water and runways**, cut out using OpenStreetMap.
- **Plantations and city trees**: oil palm, coconut, banana, coffee, rubber, pine and eucalyptus
  plantations, plus parks, golf courses, single trees and tree rows mapped in OpenStreetMap.
- **Town trees and scrub**: trees scattered through towns and cities (never on buildings or roads,
  not in industrial zones; palms and dry-climate trees on the Caribbean side), and bushes on open
  bushland such as La Guajira, the Caribbean coast and the Llanos. Both have their own sliders.
- **FPS-aware settings**: a tree budget per tile and three performance profiles (low / medium /
  high). Every setting is explained in plain language and marked with how much it affects frame rate.
- **Works next to your other scenery**: ortho tiles (e.g. OrthoStudio), simHeaven X-World and airport
  packs. ForestStudio never changes your installed scenery; it only adds one folder link.

## Requirements

- X-Plane 12
- Windows 10/11 (main platform). macOS and Linux should work but haven't been tested much yet.
- [Python 3.11 or newer](https://www.python.org/downloads/)
- Disk space: about 30–200 MB of downloaded data per tile, plus 5–15 MB per built tile.
  The OpenStreetMap country file is about 335 MB and the biome map about 150 MB, each downloaded once.
- Memory: a build needs about 3–5 GB of RAM. Several tiles can build at the same time if you have more.

## Install

```powershell
git clone https://github.com/santigarcor/foreststudio-xp.git
cd foreststudio-xp
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Start the app:

```powershell
.venv\Scripts\foreststudio-xp.exe
```

On macOS / Linux use `.venv/bin/python -m pip install -e .` and start it with `.venv/bin/foreststudio-xp`.
If macOS refuses to run the bundled DSFTool, allow it under *System Settings → Privacy & Security*.

Everything else ForestStudio needs, including DSFTool from Laminar's X-Plane Scenery Tools, is
included or downloaded automatically.

## First run

1. **Settings**: ForestStudio looks for X-Plane 12 by itself. If it doesn't find it, choose your
   X-Plane 12 folder and press **Save**. The setup checks should then show ✓.
2. **Build** view: click tiles on the map to select them (shift-click selects a block of tiles). Tiles of
   all your regions are on the same map and can be built together.
   Built tiles outlined in orange are outdated (see below).
3. Press **Build selected**. The first build also downloads the OpenStreetMap country file and the
   biome map. Each tile takes about 1–2 minutes once its data is downloaded. Follow progress in the
   **Build queue**.
4. Press **Install pack in X-Plane** (once). This adds a single folder link,
   `Custom Scenery/forest_studio_xp`, that points to your built tiles (all regions). Every tile you build
   later shows up there automatically.
5. Restart X-Plane. After rebuilding tiles that were already loaded, *Developer → Reload scenery* is enough.

### Scenery order

In `Custom Scenery/scenery_packs.ini`, put `forest_studio_xp`:

- **below** airport and city packs (they bring their own trees, and ForestStudio's tiles would hide them);
- **above** simHeaven X-World (`X-World_America-4-extras`, `-7-forests`) and your ortho tiles.

On built tiles, ForestStudio's forests replace the forests of everything below them. Unbuilt tiles keep
whatever forests you had before.

Upgrading from a version with one pack per region (`forest_colombia`): ForestStudio moves your tiles into
the new pack by itself and keeps your existing `forest_colombia` link pointing at it, so your scenery order
stays as it is.

## Using the app

| View | What it's for |
|---|---|
| **Build** | Map with tile status (selected, installed, built, outdated, data downloaded), the forest overlay preview, and the side tabs: **Tiles** (select, build, install, delete, tile info), **Profile** (performance settings, with Reset to defaults) and **Forest types** (which X-Plane forest grows in each biome and elevation band, plus plantations). |
| **Build queue** | Every build or download job, with per-tile steps, progress, log and a final report. |
| **Data** | Your regions (add countries or states from Geofabrik's list; their map data downloads right away), each region's OpenStreetMap file (check for and get newer map data), the biome map, and each tile's data. You can also download data for selected tiles without building them. |
| **Disk space** | How much space everything takes, and safe clean-ups. |
| **Settings** | X-Plane folder, where ForestStudio keeps its data, setup checks and credits. |

**Outdated tiles**: if you change the profile or forest types, or update the OpenStreetMap data,
tiles you already built no longer match. They get an orange outline on the map. **Select outdated**
selects them so you can rebuild them in one go.

**Where your data lives**: downloads, built tiles and your settings are kept in a data folder,
`.foreststudio` in your user folder by default. Change it under *Settings → Data folder* (for example
to put it on another drive). ForestStudio moves the data and updates the X-Plane link for you.

## Command line

The app runs these behind the scenes; they're also handy on their own:

```powershell
.venv\Scripts\forest fetch --tiles +06-076,+06-075        # download data for tiles
.venv\Scripts\forest build --tile +06-076 --profile medium --compile   # uses the tile's own region
.venv\Scripts\forest data osm --update                    # get a newer OpenStreetMap file
.venv\Scripts\forest app                                  # open the app
```

## Data sources and credits

ForestStudio XP is built on free data. **Every built pack includes a `CREDITS.txt`** with these
notices, as their licenses require. If you share tiles you built, keep that file with them.

| Data | Used for | License |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright), via [Geofabrik](https://download.geofabrik.de) | roads, buildings, water, airports, parks, single trees, plantations, mangroves | © OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/) |
| [ESA WorldCover 2021 v200](https://esa-worldcover.org) | land cover at 10 m (trees, mangroves) | © ESA WorldCover project 2021 / contains modified Copernicus Sentinel data (2021) processed by the ESA WorldCover consortium, CC BY 4.0 |
| [Hansen Global Forest Change v1.12](https://glad.earthengine.app/view/global-forest-change) | tree cover share, recent forest loss | Hansen et al. 2013, *Science* 342: 850–853, CC BY 4.0 |
| [RESOLVE Ecoregions 2017](https://ecoregions.appspot.com) | biomes (which forest type grows where) | Dinerstein et al. 2017, *BioScience* 67(6): 534–545, CC BY 4.0 |
| [Copernicus Global Land Cover 100 m (2019)](https://zenodo.org/records/3939050) | leaf type of each forest (conifers / broadleaves / mixed) | Buchhorn et al., Copernicus Global Land Service, free and open |
| [Köppen-Geiger 1 km maps](https://doi.org/10.6084/m9.figshare.21789074) | climate of each forest (picks the tree types) | Beck et al. 2023, *Scientific Data* 10: 724, CC BY 4.0 |
| X-Plane 12 base mesh | elevation (read from your own X-Plane, not redistributed) | Laminar Research |

Software included: **DSFTool** from Laminar Research's
[X-Plane Scenery Tools](https://github.com/X-Plane/xptools) (MIT/X11, see `tools/xptools/NOTICE.md`).
The app's map uses [Leaflet](https://leafletjs.com) with Esri World Imagery and OpenStreetMap tiles.
Tree models and textures are X-Plane 12's own default forests. They're referenced, not included.

## Development

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
```

Tests run in temporary folders and never touch your real data or X-Plane. `CLAUDE.md` has the
technical overview (architecture, build pipeline, X-Plane details) and `PLAN.md` the decision log.

## License

ForestStudio XP is free software under the [GNU General Public License v3.0](LICENSE).
