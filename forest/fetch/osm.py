"""OpenStreetMap features per tile come only from the downloaded Geofabrik country files
(osm_pbf.py). There is no online fallback any more: the Overpass API stalled whole jobs (one
degree is too heavy for it) and its query drifted from what osm_pbf extracts, so a tile built
from it silently got different cut-outs. A tile no downloaded country file covers now fails
with a message that says what to download.
"""

from __future__ import annotations

from pathlib import Path

from forest.tiles import Tile


class OsmFileMissing(RuntimeError):
    """No downloaded OSM country file covers the tile."""


def missing_message(tile: Tile, region: str | None = None) -> str:
    from forest import regions

    files = [p.name for p in regions.osm_files(tile, region)]
    if files:
        return (f"{tile.name}: OpenStreetMap country file missing ({', '.join(files)}). "
                "Download it in Data → Regions, then build again.")
    return f"{tile.name}: no region's OpenStreetMap file covers this tile. Add its country in Data → Regions."


def usable_without_country_file(tile: Tile) -> bool:
    """The tile's osm.json is in the current format, so it can be built even though no country
    file is downloaded (e.g. deleted to save space). Old files without a version (the former
    Overpass fallback) don't count, nor does a tile no region's extract covers (its osm.json was
    written empty when the region's own file was read for it)."""
    from forest import regions
    from forest.fetch.osm_pbf import is_current
    return regions.osm_covered(tile) and is_current(tile)


def fetch_osm(tile: Tile, region: str | None = None) -> Path:
    """The tile's osm.json, read from every downloaded country file covering it (re-read when
    outdated), or the tile's existing current-format osm.json when no country file is downloaded.
    Raises OsmFileMissing otherwise."""
    from forest import regions
    from forest.fetch.osm_pbf import extract_tile

    pbfs = regions.osm_sources(tile, region)   # every downloaded extract covering the tile
    if pbfs:
        return extract_tile(tile, pbfs)
    if usable_without_country_file(tile):
        return tile.cache_path / "osm.json"
    raise OsmFileMissing(missing_message(tile, region))
