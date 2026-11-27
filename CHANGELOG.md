# Changelog

All notable changes to ForestStudio XP are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Each release's notes are in `docs/releases/<version>.md`.

## [Unreleased]

## [0.1.0] - unreleased

First public release.

### Added
- Installers: Windows setup (per-user, no admin rights), macOS disk image (unsigned) and a Linux
  archive, each with its own Python; no Python install needed. The version shows in the header and
  in Settings → About, and is written into the pack's `CREDITS.txt`.
- Desktop app with a tile map: build, install the pack in X-Plane, build queue with per-tile steps
  and logs, Data view, Disk space view, Settings (X-Plane folder, data folder).
- Forest overlay per 1° tile from ESA WorldCover 2021, Hansen Global Forest Change, OpenStreetMap,
  RESOLVE Ecoregions and X-Plane's own elevation; DSFTool bundled for Windows, macOS and Linux.
- Colombia with hand-tuned, evergreen-only forest types, plantations, mangroves (satellite and
  OpenStreetMap), scrub and town trees.
- Regions from Geofabrik's list with global default forest types chosen by leaf type and climate
  (Copernicus forest type, Köppen-Geiger); border tiles read every covering country file.
- Low / medium / high performance profiles with a tree budget per tile; FPS marks on every setting.
- One X-Plane pack for every region, linked as one folder link; outdated tiles shown in orange.
- Parallel builds in worker processes, sized to the computer's cores and memory.
- Command line (`forest`) for debugging, running the same tasks as the app.

[Unreleased]: https://github.com/santigarcor/foreststudio-xp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/santigarcor/foreststudio-xp/releases/tag/v0.1.0
