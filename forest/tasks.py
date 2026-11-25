"""The work the app runs in its worker processes (and the CLI runs directly): download data for
tiles, download / update country-wide files, build one tile. Every function returns an exit code
(0 = ok) and reports progress through `forest.events`, never by printing lines for someone to parse.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from forest import config, events, fsutil
from forest.tiles import Tile

# ---------------------------------------------------------------- data for tiles

def prepare(tiles: list[str]) -> int:
    """Files every country shares (the ecoregions zip, the climate map), downloaded once before
    the per-country data steps run side by side (two workers must never write the same file)."""
    from forest import regions
    from forest.fetch import sources
    from forest.fetch.ecoregions import clipped_path

    owners = {regions.owner(Tile.parse(n)) or regions.default_name() for n in tiles}
    zp = sources.ecoregions_zip()
    if any(not clipped_path(r).exists() for r in owners) and not zp.exists():
        events.emit("downloading", what="ecoregions", name="the biome map (Ecoregions 2017)",
                    url=sources.ECOREGIONS_URL)
        try:
            sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
        except OSError as exc:
            events.log(f"ecoregions download FAILED: {exc}")
    if any(uses_climate(r) for r in owners):
        ensure_climate()
    return 0


def fetch(tiles: list[str], region: str | None = None) -> int:
    """Satellite maps (4 tiles at a time), elevation and OSM (one pass per country file) for these
    tiles. region None: each tile with its owner region's files."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from forest import history, regions
    from forest.fetch.elevation import fetch_elevation
    from forest.fetch.osm import missing_message, usable_without_country_file
    from forest.fetch.osm_pbf import extract_tiles
    from forest.fetch.rasters import fetch_all
    from forest.resources import DOWNLOAD_WORKERS

    tiles_ = [Tile.parse(n) for n in dict.fromkeys(tiles)]
    owner = {t.name: region or regions.owner(t) or regions.default_name() for t in tiles_}
    climate_regions = {r for r in set(owner.values()) if uses_climate(r)}
    for r in sorted(set(owner.values())):
        ensure_country_files(r)
    if climate_regions:
        ensure_climate()

    def one(tile: Tile) -> float:
        had = (tile.cache_path / "worldcover.tif").exists()
        t0 = time.time()
        paths = fetch_all(tile)
        if owner[tile.name] in climate_regions:
            from forest.fetch.climate import fetch_leaftype
            try:
                paths["leaftype"] = fetch_leaftype(tile)
            except Exception as exc:  # noqa: BLE001 - optional: the build counts its forest as mixed
                events.log(f"{tile.name}: leaf-type map FAILED ({exc})")
        if not had:
            history.record("rasters", tile=tile.name, seconds=round(time.time() - t0, 1),
                           mb=round(sum(fsutil.size_mb(p) for p in paths.values()), 1))
        fetch_elevation(tile)
        return sum(fsutil.size_mb(p) for p in paths.values())

    events.emit("sat_start", n=len(tiles_))
    failed = []
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futs = {pool.submit(one, t): t for t in tiles_}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                events.emit("sat_tile", tile=t.name, ok=True, mb=round(fut.result(), 1))
            except Exception as exc:  # noqa: BLE001 - report and continue with the others
                failed.append(t.name)
                events.emit("sat_tile", tile=t.name, ok=False, error=str(exc)[:300])
    # OpenStreetMap: one pass over each country file for every tile that needs it (border tiles
    # read every downloaded file that covers them).
    with_pbf = [t for t in tiles_ if regions.osm_sources(t, region)]
    if with_pbf:
        t0 = time.time()
        counts = extract_tiles(with_pbf, region=region)
        secs = round(time.time() - t0, 1)
        if counts:
            mbs = [fsutil.size_mb(next(t for t in tiles_ if t.name == n).cache / "osm.json") for n in counts]
            peak = memory_gb("peak")
            history.record("osm_pass", tiles=len(counts), seconds=secs,
                           mb_per_tile=round(sum(mbs) / len(mbs), 1),
                           peak_gb=round(peak, 2) if peak is not None else None)
        events.emit("osm_done", n=len(counts), seconds=secs, tiles=[t.name for t in with_pbf])
    for tile in tiles_:
        if tile not in with_pbf:   # no downloaded country file covers it: say what to download
            if usable_without_country_file(tile):
                continue           # its OSM data is already here in the current format
            msg = missing_message(tile, region)
            events.log(msg)
            events.emit("step", tile=tile.name, step="data", status="failed", note=msg[:200])
            if tile.name not in failed:
                failed.append(tile.name)
    if failed:
        events.log(f"data missing for: {', '.join(sorted(failed))}")
        return 3
    return 0


# ---------------------------------------------------------------- country-wide files

def uses_climate(region: str) -> bool:
    from forest.build import Settings
    from forest.fetch.climate import uses_climate as rules_use_climate
    try:
        return rules_use_climate(Settings.load("medium", region).rules)
    except Exception:  # noqa: BLE001 - no rules yet: decided at build time
        return False


def ensure_climate() -> None:
    """First use: the Köppen-Geiger climate map (one 131 MB zip; only the present-day map is kept)."""
    from forest.fetch import climate, sources
    if climate.climate_path().exists():
        return
    events.emit("downloading", what="climate", name="the climate map (Köppen-Geiger)", url=climate.KOPPEN_URL)
    try:
        climate.ensure_climate(sources.print_progress("climate"))
        events.emit("ready", what="climate map")
    except OSError as exc:
        events.log(f"climate map download FAILED: {exc}")


def ensure_country_files(region: str) -> None:
    """First use: download the OSM country file and the ecoregions map if they're missing."""
    from forest.fetch import sources
    from forest.fetch.ecoregions import clipped_path

    pbf, url = sources.osm_file(region), sources.osm_url(region)
    if not pbf.exists():
        events.emit("downloading", what="OSM", name=f"the OpenStreetMap file {pbf.name}", url=url)
        try:
            sources.download(url, pbf, sources.print_progress("OSM"))
        except OSError as exc:
            events.log(f"OSM country file download FAILED: {exc}")
    zp = sources.ecoregions_zip()
    if not clipped_path(region).exists() and not zp.exists():
        events.emit("downloading", what="ecoregions", name="the biome map (Ecoregions 2017)",
                    url=sources.ECOREGIONS_URL)
        try:
            sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
        except OSError as exc:
            events.log(f"ecoregions download FAILED: {exc}")


def data_region(region: str) -> int:
    """Everything a newly added region needs: its OSM file, the ecoregions map and its cut-out (and
    the climate map if its forest types use it). Tiles that already have OSM data and are covered
    by the new file are re-read (border tiles)."""
    from forest.fetch.ecoregions import build_clip
    ensure_country_files(region)
    build_clip(region)
    events.emit("ready", what="ecoregions")
    if uses_climate(region):
        ensure_climate()
    return reextract(region)


def data_ecoregions(region: str, update: bool = False) -> int:
    from forest.fetch import sources
    from forest.fetch.ecoregions import build_clip
    zp = sources.ecoregions_zip()
    if update or not zp.exists():
        events.emit("downloading", what="ecoregions", name="the biome map (Ecoregions 2017)",
                    url=sources.ECOREGIONS_URL)
        sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
        for old in zp.parent.parent.glob("*.geojson"):   # every region's cut-out is from the old zip
            old.unlink(missing_ok=True)
    build_clip(region)
    events.emit("ready", what="ecoregions")
    return 0


def data_osm(region: str, update: bool = False, force: bool = False) -> int:
    """Download / update the region's OSM country file (only if the server's is newer, unless
    `force`), then re-read every tile that has OSM data (they then show as outdated)."""
    from forest import regions
    from forest.fetch import sources

    reg = regions.get(region)
    url, pbf = reg.osm_url, reg.osm_file
    if update or not pbf.exists():
        if pbf.exists() and not force:
            if not sources.check_update(pbf, url)["newer"]:
                events.log("OSM country file: already the newest version")
                return 0
        events.emit("downloading", what="OSM", name=f"the OpenStreetMap file {pbf.name}", url=url)
        sources.download(url, pbf, sources.print_progress("OSM"))
    return reextract(region)


def reextract(region_name: str) -> int:
    from forest import regions
    from forest.fetch.osm_pbf import extract_tiles
    from forest.tiles import CACHE

    region = regions.get(region_name)
    pbf = region.osm_file
    if not pbf.exists():
        events.log(f"OSM country file missing: {pbf.name}")
        return 1
    # Tiles that already have OSM data and are covered by the new file are re-extracted (with every
    # other downloaded file covering them, so border tiles keep both countries).
    tiles = [t for t in (Tile.parse(d.name) for d in sorted(CACHE.glob("*")) if (d / "osm.json").exists())
             if pbf in regions.osm_sources(t, region.name if region.contains(t) else None)]
    if tiles:
        t0 = time.time()
        extract_tiles(tiles, force=True, region=region.name)
        events.emit("osm_done", n=len(tiles), seconds=round(time.time() - t0, 1),
                    tiles=[t.name for t in tiles])
    return 0


# ---------------------------------------------------------------- build

def build(tile: str, profile: str = "medium", region: str | None = None, compile: bool = True,
          install: bool = False, keep_text: bool = False) -> int:
    """Build one tile (its owner region's forest types unless `region`), compile it into the pack
    and record time / disk / peak memory for the estimates."""
    from forest import history
    from forest.build import build_tile
    from forest.paths import dsf_path, studio_path

    t = Tile.parse(tile)
    t0 = time.time()
    txt = build_tile(t, profile=profile, region=region)
    if compile or install:
        events.emit("step", tile=t.name, step="compile", status="running")
        dsf = dsf_path(t)
        rss_at_compile = memory_gb("rss")   # what this process still holds while DSFTool runs
        measured: dict = {}
        rc = compile_dsf(txt, dsf, measured)
        if rc:
            return rc
        from forest.credits import write_pack_credits
        from forest.paths import pack_root
        write_pack_credits(pack_root())   # data licenses require it
        if not keep_text:
            # ~130 MB per tile and regenerated by every build; the .dsf is what matters.
            txt.unlink(missing_ok=True)
        extra = sum(fsutil.size_mb(studio_path(t, s)) for s in (".overlay.png", ".preview.png", ".stats.json"))
        # Peak of the whole build = the larger of this process's own peak (classify/vectorise) and
        # what it still holds plus DSFTool's peak during the compile. Auto parallel builds use it.
        build_peak = memory_gb("peak")
        dsftool_peak = measured.get("peak_gb")
        peak_gb = build_peak
        if build_peak is not None and rss_at_compile is not None and dsftool_peak is not None:
            peak_gb = max(build_peak, rss_at_compile + dsftool_peak)
        history.record("build", tile=t.name, seconds=round(time.time() - t0, 1),
                       dsf_mb=round(fsutil.size_mb(dsf), 2), out_extra_mb=round(extra, 2),
                       peak_gb=_round(peak_gb), build_peak_gb=_round(build_peak),
                       rss_at_compile_gb=_round(rss_at_compile), dsftool_peak_gb=_round(dsftool_peak))
        events.emit("step", tile=t.name, step="compile", status="done")
    if install:
        # The whole pack is linked into X-Plane as one folder; make sure the link exists.
        from forest import pack
        events.log(pack.link_pack()["message"])
    return 0


def memory_gb(which: str) -> float | None:
    """This process's current ("rss") or peak ("peak") memory in GB."""
    try:
        import psutil

        mi = psutil.Process().memory_info()
        return (getattr(mi, "peak_wset", mi.rss) if which == "peak" else mi.rss) / 1e9
    except Exception:  # noqa: BLE001
        return None


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def compile_dsf(txt: Path, dsf: Path | None = None, measured: dict | None = None) -> int:
    """Compile DSF text to `dsf` (default: next to the text). Written to a temporary name and
    swapped in atomically, so X-Plane never reads a half-written tile. With `measured`, DSFTool's
    peak memory is stored there as "peak_gb"."""
    cfg = config.load()
    dsf = dsf or txt.with_suffix(".dsf")
    dsf.parent.mkdir(parents=True, exist_ok=True)
    tmp = dsf.with_name(dsf.stem + ".building.dsf")
    tmp.unlink(missing_ok=True)
    rc = run_tool([str(cfg.dsftool), "--text2dsf", str(txt), str(tmp)], measured)
    if rc == 0:
        os.replace(tmp, dsf)
    else:
        tmp.unlink(missing_ok=True)
    return rc


_TOOL_NOISE = ("Scanning for", "Got dimension", "Got entire", "Converting ", "Converted ")


def run_tool(cmd: list[str], measured: dict | None = None) -> int:
    """Run DSFTool hidden (rule 4); its messages go to the log, minus the progress chatter."""
    if not Path(cmd[0]).exists():
        events.log(f"DSFTool not found at {cmd[0]} (it ships in tools/xptools/; reinstall ForestStudio)")
        return 2
    from forest import proc

    if measured is None:
        r = proc.run(cmd)  # hidden: never opens a console window
    else:
        r, measured["peak_gb"] = proc.run_measured(cmd)
    for line in (r.stdout or "").splitlines() + (r.stderr or "").splitlines():
        if line.strip() and not line.startswith(_TOOL_NOISE):
            events.log(line)
    return r.returncode
