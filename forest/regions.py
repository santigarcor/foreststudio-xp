"""Regions: where ForestStudio builds, where the data comes from, and which rules apply.

One TOML file per region: the shipped ones in <code>/config/regions/, and the user's own in
<data>/config/regions/ (a file with the same name replaces the shipped one). The file name is
the region's name, which also names its forest types (config/rules/<name>.toml), its OSM file
and its ecoregion cut-out.

The map offers every tile of every region; each tile is built with the forest types of ONE region,
its owner (`owner`): the region whose Geofabrik outline covers most of the tile. All built tiles go
into one X-Plane pack (paths.PACK_NAME).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from forest import config
from forest.tiles import Tile


@dataclass(frozen=True)
class Region:
    name: str
    label: str
    bounds: tuple[int, int, int, int]        # west, south, east, north (tile edges, whole degrees)
    osm_url: str                              # Geofabrik extract covering the bounds
    evergreen_only: bool = True               # refuse forest types with seasonal variants
    ecoregion_bounds: tuple[float, float, float, float] | None = None   # default: bounds + 1°
    tile_names: frozenset[str] | None = None  # optional explicit tiles (regions added from Geofabrik)
    geofabrik_id: str = ""                     # the extract it was added from (Data view → Regions)

    def tiles(self) -> list[Tile]:
        w, s, e, n = self.bounds
        return [t for lat in range(s, n) for lon in range(w, e) if self.contains(t := Tile(lat, lon))]

    def contains(self, tile: Tile) -> bool:
        w, s, e, n = self.bounds
        inside = w <= tile.lon < e and s <= tile.lat < n
        return inside and (self.tile_names is None or tile.name in self.tile_names)

    @property
    def clip_bounds(self) -> tuple[float, float, float, float]:
        if self.ecoregion_bounds:
            return self.ecoregion_bounds
        w, s, e, n = self.bounds
        return (w - 1, s - 1, e + 1, n + 1)

    @property
    def osm_file(self) -> Path:
        from forest.fetch import osm_pbf

        return osm_pbf.PBF_DIR / Path(self.osm_url).name

    def info(self) -> dict:
        """What the app needs to know about the region."""
        return {"name": self.name, "label": self.label, "bounds": list(self.bounds),
                "evergreen_only": self.evergreen_only, "user": is_user_region(self.name),
                "tiles": sorted(self.tile_names) if self.tile_names is not None else None}


def _dirs() -> list[Path]:
    return [config.DEFAULTS_DIR / "regions", config.USER_CONFIG / "regions"]   # later wins


def _files() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for d in _dirs():
        for f in sorted(d.glob("*.toml")) if d.is_dir() else []:
            out[f.stem] = f
    return out


def names() -> list[str]:
    return sorted(_files())


def default_name() -> str:
    """The region the app opens with: Colombia if present, else the first one."""
    n = names()
    if not n:
        raise ValueError("no regions found (config/regions/*.toml)")
    return "colombia" if "colombia" in n else n[0]


def _box(v, where: str, whole: bool) -> tuple:
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v)):
        raise ValueError(f"{where}: must be [west, south, east, north]")
    if whole and not all(float(x).is_integer() for x in v):
        raise ValueError(f"{where}: must be whole degrees (tile edges)")
    w, s, e, n = (int(x) for x in v) if whole else (float(x) for x in v)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError(f"{where}: west < east within ±180 and south < north within ±90")
    return (w, s, e, n)


def parse(name: str, data: dict) -> Region:
    where = f"region {name}"
    url = str(data.get("osm_url", ""))
    if not url.startswith("https://") or not url.endswith(".osm.pbf"):
        raise ValueError(f"{where}: osm_url must be an https:// link to a .osm.pbf file")
    eb = data.get("ecoregion_bounds")
    tl = data.get("tiles")
    if tl is not None:
        if not isinstance(tl, list):
            raise ValueError(f"{where}: tiles must be a list of tile names like \"+06-076\"")
        try:
            tl = frozenset(Tile.parse(str(x)).name for x in tl)
        except ValueError as e:
            raise ValueError(f"{where}: tiles: {e}") from None
    return Region(
        name=name,
        label=str(data.get("label") or name.replace("_", " ").title()),
        bounds=_box(data.get("bounds"), f"{where}: bounds", whole=True),
        osm_url=url,
        evergreen_only=bool(data.get("evergreen_only", True)),
        ecoregion_bounds=_box(eb, f"{where}: ecoregion_bounds", whole=False) if eb is not None else None,
        tile_names=tl,
        geofabrik_id=str(data.get("geofabrik_id", "")),
    )


def get(name: str) -> Region:
    f = _files().get(name)
    if f is None:
        raise ValueError(f"unknown region {name!r} (known: {', '.join(names()) or 'none'})")
    return parse(name, tomllib.loads(f.read_text(encoding="utf-8")))


def is_user_region(name: str) -> bool:
    """True for a region the user added (no shipped file of that name)."""
    return not (config.DEFAULTS_DIR / "regions" / f"{name}.toml").exists()


SWAP_MARKER = "# --- evergreen_swap"


def template_rules_text(region: Region) -> str:
    """Forest types for a region without hand-made rules: the global defaults by RESOLVE biome
    (config/rules/_global.toml). Evergreen-only regions get evergreen types instead of seasonal
    ones (the template's [evergreen_swap] table); the table itself is not copied."""
    text = (config.DEFAULTS_DIR / "rules" / "_global.toml").read_text(encoding="utf-8")
    swap = tomllib.loads(text).get("evergreen_swap", {})
    head = text.split(SWAP_MARKER, 1)[0].rstrip() + "\n"
    head = head.replace("{label}", region.label)
    if region.evergreen_only:
        for seasonal, evergreen in swap.items():
            head = head.replace(f'"{seasonal}"', f'"{evergreen}"')
    return head


def add_from_geofabrik(eid: str) -> Region:
    """Write <data>/config/regions/<id>.toml for a Geofabrik extract (an existing file is kept)."""
    from forest.fetch import geofabrik

    name = geofabrik.region_name(eid)
    if name in names():
        return get(name)
    d = config.USER_CONFIG / "regions"
    d.mkdir(parents=True, exist_ok=True)
    text = geofabrik.region_toml(eid)
    parse(name, tomllib.loads(text))           # refuse before writing anything bad
    (d / f"{name}.toml").write_text(text, encoding="utf-8")
    return get(name)


def remove(name: str) -> list[str]:
    """Delete a region the user added: its file, its OSM download (unless another region uses the
    same file) and its ecoregion cut-out. Forest types and built tiles stay. Returns what went."""
    if not is_user_region(name):
        raise ValueError(f"{name} is shipped with ForestStudio and can't be removed")
    r = get(name)
    gone = []
    others = [get(n) for n in names() if n != name]
    if not any(o.osm_file == r.osm_file for o in others):
        from forest.fetch import osm_tiles, sources
        for p in (r.osm_file, sources._meta_path(r.osm_file), osm_tiles.db_path(r.osm_file)):
            if p.exists():
                p.unlink()
                gone.append(p.name)
    from forest.fetch.ecoregions import clipped_path
    cp = clipped_path(name)
    if cp.exists():
        cp.unlink()
        gone.append(cp.name)
    f = config.USER_CONFIG / "regions" / f"{name}.toml"
    f.unlink()
    gone.append(f.name)
    return gone


def _covers(r: Region, tile: Tile) -> bool:
    """Does the region's OSM extract cover the tile? The file's tile list (osm_tiles, written on
    download) when there is one, else Geofabrik's outline from its index, else the region's tiles."""
    return tile.name in _extract_tiles(r)


def _extract_tiles(r: Region) -> dict[str, float]:
    """Tiles the region's extract covers → share: its tile list, else the index, else every
    region tile at 1 (no outline known)."""
    from forest.fetch import geofabrik, osm_tiles

    db = osm_tiles.read(r.osm_file)
    if db is not None:
        return db
    e = geofabrik.by_file_name().get(r.osm_file.name)
    return geofabrik.tile_shares(e["id"]) if e else {t.name: 1.0 for t in r.tiles()}


def osm_files(tile: Tile, region: str | None = None, downloaded_only: bool = False) -> list[Path]:
    """Every region's OSM extract that covers the tile (a tile on a border needs both countries'
    files). Sorted by file name. A region's own file is NOT added for a tile its extract doesn't
    cover: Colombia's box reaches into Panama, and those tiles got an empty osm.json (no runways,
    roads or buildings: trees all over MPTO). `region` is kept for callers; it no longer matters."""
    out: dict[str, Path] = {}
    for n in names():
        try:
            r = get(n)
        except ValueError:
            continue
        f = r.osm_file
        if downloaded_only and not f.exists():
            continue
        if f.name not in out and _covers(r, tile):
            out[f.name] = f
    return [out[k] for k in sorted(out)]


def osm_covered(tile: Tile) -> bool:
    """Is the tile in a downloaded OSM file's tile list (so it can be built)?"""
    return tile.name in covered_tiles()


@lru_cache(maxsize=4)
def _covered(key: tuple) -> frozenset[str]:
    from forest.fetch import osm_tiles

    out: set[str] = set()
    for n in names():
        try:
            r = get(n)
        except ValueError:
            continue
        if osm_tiles.read(r.osm_file) is not None or r.osm_file.exists():
            out |= set(_extract_tiles(r))
    return frozenset(out)


def covered_tiles() -> frozenset[str]:
    """Every tile the map enables: those in a downloaded OSM file's tile list (osm_tiles; the list
    stays when the .pbf is deleted to save space). A file downloaded but not listed yet (offline on
    start, no index) falls back to the index outline / region tiles. A region whose file isn't
    downloaded enables nothing yet: download it in Data → Regions."""
    return _covered(_owners_key())


def waiting_for_download(tile: Tile) -> list[str]:
    """Names of the regions that offer the tile but whose OSM file isn't downloaded yet."""
    from forest.fetch import osm_tiles

    out = []
    for n in names():
        try:
            r = get(n)
        except ValueError:
            continue
        if r.contains(tile) and not r.osm_file.exists() and osm_tiles.read(r.osm_file) is None:
            out.append(r.name)
    return out


def osm_sources(tile: Tile, region: str | None = None) -> list[Path]:
    """The downloaded ones of osm_files: what the tile's osm.json is read from."""
    return osm_files(tile, region, downloaded_only=True)


def osm_groups(tiles: list[str], owners: dict[str, str]) -> list[list[str]]:
    """Split tiles into groups that share no OSM country file, so each group's data can be read
    at the same time as the others' (a border tile joins its countries into one group: one pass
    per file stays one pass). Tiles no file covers form one group. The order of `tiles` is kept."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    files_of = {}
    for n in tiles:
        files = [f.name for f in osm_files(Tile.parse(n), owners.get(n))] or ["<none>"]
        files_of[n] = files
        for f in files[1:]:
            parent[find(f)] = find(files[0])
    groups: dict[str, list[str]] = {}
    for n in tiles:
        groups.setdefault(find(files_of[n][0]), []).append(n)
    return list(groups.values())


def _outline_known(r: Region) -> bool:
    from forest.fetch import geofabrik, osm_tiles
    return osm_tiles.read(r.osm_file) is not None or r.osm_file.name in geofabrik.by_file_name()


def _share(r: Region, tile: Tile) -> float:
    """How much of the tile the region's extract covers (see _extract_tiles)."""
    return _extract_tiles(r).get(tile.name, 0.0)


@lru_cache(maxsize=4)
def _owners(key: tuple) -> dict[str, str]:
    rs = []
    for n in names():
        try:
            rs.append(get(n))
        except ValueError:
            continue
    claims: dict[str, list[Region]] = {}
    for r in rs:
        for t in r.tiles():
            claims.setdefault(t.name, []).append(r)
    out = {}
    for name, cands in claims.items():
        if len(cands) == 1:
            out[name] = cands[0].name
        else:
            t = Tile.parse(name)
            # A known outline (tile list / index) beats a guessed one (the region's box at 1).
            out[name] = max(cands, key=lambda r: (_outline_known(r), _share(r, t), r.name == default_name(),
                                                  -names().index(r.name))).name
    return out


def _owners_key() -> tuple:
    from forest.fetch import geofabrik

    def mt(p: Path) -> int:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return 0
    from forest.fetch import osm_pbf
    pbfs = sorted(osm_pbf.PBF_DIR.glob("*.osm.pbf*"))   # the files and their tile lists
    return (tuple((n, str(f), mt(f)) for n, f in sorted(_files().items())), mt(geofabrik.index_path()),
            tuple((p.name, mt(p), p.stat().st_size) for p in pbfs if p.exists()))


def owners() -> dict[str, str]:
    """Every tile any region offers → the region whose forest types build it (the one whose
    outline covers most of the tile; ties: the default region, then by name)."""
    return _owners(_owners_key())


def owner(tile: Tile) -> str | None:
    return owners().get(tile.name)
