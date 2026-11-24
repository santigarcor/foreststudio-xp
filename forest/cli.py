"""forest command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from forest import config, fsutil


def cmd_catalog(args: argparse.Namespace) -> int:
    from forest.assets.catalog import build_catalog, write_catalog

    cfg = config.load()
    cat = build_catalog(cfg.default_forests)
    out = Path(args.out)
    write_catalog(cat, out)
    print(f"{cat['count']} forest assets -> {out}")
    return 0


def cmd_poc(args: argparse.Namespace) -> int:
    from forest import poc

    out_root = Path(args.out)
    txt = poc.build(out_root, density=args.density, season=args.season)
    print(f"wrote {txt}")
    if args.compile:
        return compile_dsf(txt)
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    """Decode a DSF to text into cache/ (never writes next to the source)."""
    cfg = config.load()
    src = Path(args.dsf)
    name = f"{args.label}_{src.stem}.txt" if args.label else src.stem + ".txt"
    dst = config.CACHE_ROOT / "decoded" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    return _run([str(cfg.dsftool), "--dsf2text", str(src), str(dst)])


def _tiles(args: argparse.Namespace) -> list:
    from forest.tiles import Tile

    names = ([args.tile] if getattr(args, "tile", None) else []) + \
            [x for x in (getattr(args, "tiles", None) or "").split(",") if x.strip()]
    if not names:
        raise SystemExit("give --tile or --tiles")
    return [Tile.parse(n) for n in dict.fromkeys(names)]


def cmd_fetch(args: argparse.Namespace) -> int:
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from forest import history, regions
    from forest.fetch.elevation import fetch_elevation
    from forest.fetch.osm import fetch_osm
    from forest.fetch.osm_pbf import extract_tiles
    from forest.fetch.rasters import fetch_all
    from forest.resources import DOWNLOAD_WORKERS

    tiles = _tiles(args)
    region = args.region   # None: every tile with its owner region's files
    owner = {t.name: region or regions.owner(t) or regions.default_name() for t in tiles}
    climate_regions = {r for r in set(owner.values()) if _uses_climate(r)}
    for r in sorted(set(owner.values())):
        _ensure_country_files(r)
    if climate_regions:
        _ensure_climate()

    def one(tile):
        had = (tile.cache_path / "worldcover.tif").exists()
        t0 = time.time()
        paths = fetch_all(tile)
        if owner[tile.name] in climate_regions:
            from forest.fetch.climate import fetch_leaftype
            try:
                paths["leaftype"] = fetch_leaftype(tile)
            except Exception as exc:  # noqa: BLE001 - optional: the build counts its forest as mixed
                print(f"{tile.name}: leaf-type map FAILED ({exc})", flush=True)
        if not had:
            history.record("rasters", tile=tile.name, seconds=round(time.time() - t0, 1),
                           mb=round(sum(fsutil.size_mb(p) for p in paths.values()), 1))
        fetch_elevation(tile)
        return tile, sum(fsutil.size_mb(p) for p in paths.values())

    # Satellite windows download in parallel (network-bound).
    print(f"satellite maps: downloading for {len(tiles)} tile(s)", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futs = {pool.submit(one, t): t for t in tiles}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                _, mb = fut.result()
                print(f"{t.name}: rasters ready ({mb:.0f} MB)", flush=True)
            except Exception as exc:  # noqa: BLE001 - report and continue with the others
                failed.append(t.name)
                print(f"{t.name}: data download FAILED: {exc}", flush=True)
    # OpenStreetMap: one pass over each country file for every tile that needs it (border tiles
    # read every downloaded file that covers them).
    with_pbf = [t for t in tiles if regions.osm_sources(t, region)]
    if with_pbf:
        t0 = time.time()
        counts = extract_tiles(with_pbf, region=region)
        if counts:
            secs = round(time.time() - t0, 1)
            mbs = [fsutil.size_mb(next(t for t in tiles if t.name == n).cache / "osm.json") for n in counts]
            history.record("osm_pass", tiles=len(counts), seconds=secs,
                           mb_per_tile=round(sum(mbs) / len(mbs), 1))
            print(f"OSM: one pass for {len(counts)} tile(s) in {secs:.0f} s", flush=True)
        else:
            print("OSM: already up to date", flush=True)
    for tile in tiles:
        if tile not in with_pbf:
            fetch_osm(tile, region)   # no local file covers it: Overpass
    if failed:
        print(f"data missing for: {', '.join(sorted(failed))}", flush=True)
        return 3
    return 0


def _uses_climate(region: str) -> bool:
    from forest.build import Settings
    from forest.fetch.climate import uses_climate
    try:
        return uses_climate(Settings.load("medium", region).rules)
    except Exception:  # noqa: BLE001 - no rules yet: decided at build time
        return False


def _ensure_climate() -> None:
    """First use: the Köppen-Geiger climate map (one 131 MB zip; only the present-day map is kept)."""
    from forest.fetch import climate, sources
    if climate.climate_path().exists():
        return
    print("climate map missing: downloading", flush=True)
    try:
        climate.ensure_climate(sources.print_progress("climate"))
        print("climate map: ready", flush=True)
    except OSError as exc:
        print(f"climate map download FAILED: {exc}", flush=True)


def _ensure_country_files(region: str) -> None:
    """First use: download the OSM country file and the ecoregions map if they're missing."""
    from forest.fetch import sources

    pbf, url = sources.osm_file(region), sources.osm_url(region)
    if not pbf.exists():
        print(f"OSM country file missing: downloading {url}", flush=True)
        try:
            sources.download(url, pbf, sources.print_progress("OSM"))
        except OSError as exc:
            print(f"OSM country file download FAILED: {exc}", flush=True)
    zp = sources.ecoregions_zip()
    from forest.fetch.ecoregions import clipped_path
    if not clipped_path(region).exists() and not zp.exists():
        print("ecoregions map missing: downloading", flush=True)
        try:
            sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
        except OSError as exc:
            print(f"ecoregions download FAILED: {exc}", flush=True)


def cmd_data(args: argparse.Namespace) -> int:
    """Download / update a country-wide file. For OSM, tiles that already have an extract are
    re-extracted from the new file (they then show as outdated until rebuilt)."""

    from forest.fetch import sources

    if args.what == "region":
        # A region was just added: its OSM file, the ecoregions map and its cut-out. Tiles that
        # already have OSM data and are covered by the new file get it too (border tiles).
        from forest.fetch.ecoregions import build_clip
        _ensure_country_files(args.region)
        build_clip(args.region)
        print("ecoregions: ready", flush=True)
        if _uses_climate(args.region):
            _ensure_climate()
        return _reextract(args.region)

    if args.what == "ecoregions":
        from forest.fetch.ecoregions import build_clip
        zp = sources.ecoregions_zip()
        if args.update or not zp.exists():
            sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
            for old in zp.parent.parent.glob("*.geojson"):   # every region's cut-out is from the old zip
                old.unlink(missing_ok=True)
        build_clip(args.region)
        print("ecoregions: ready", flush=True)
        return 0

    from forest import regions

    region = regions.get(args.region)
    url, pbf = region.osm_url, region.osm_file
    if args.update or not pbf.exists():
        if pbf.exists() and not args.force:
            chk = sources.check_update(pbf, url)
            if not chk["newer"]:
                print("OSM country file: already the newest version", flush=True)
                return 0
        print(f"OSM country file: downloading {url}", flush=True)
        sources.download(url, pbf, sources.print_progress("OSM"))
    return _reextract(args.region)


def _reextract(region_name: str) -> int:
    import time

    from forest import regions
    from forest.fetch.osm_pbf import extract_tiles
    from forest.tiles import CACHE, Tile

    region = regions.get(region_name)
    pbf = region.osm_file
    if not pbf.exists():
        print(f"OSM country file missing: {pbf.name}", flush=True)
        return 1
    # Tiles that already have OSM data and are covered by the new file are re-extracted (with every
    # other downloaded file covering them, so border tiles keep both countries).
    tiles = [t for t in (Tile.parse(d.name) for d in sorted(CACHE.glob("*")) if (d / "osm.json").exists())
             if pbf in regions.osm_sources(t, region.name if region.contains(t) else None)]
    if tiles:
        t0 = time.time()
        extract_tiles(tiles, force=True, region=region.name)
        print(f"OSM: one pass for {len(tiles)} tile(s) in {time.time() - t0:.0f} s", flush=True)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    import time

    from forest import history
    from forest.build import build_tile
    from forest.paths import dsf_path, studio_path
    from forest.tiles import Tile

    tile = Tile.parse(args.tile)
    t0 = time.time()
    txt = build_tile(tile, profile=args.profile, region=args.region)
    if args.compile or args.install:
        dsf = dsf_path(tile)
        rss_at_compile = _memory_gb("rss")   # what this process still holds while DSFTool runs
        measured: dict = {}
        rc = compile_dsf(txt, dsf, measured)
        if rc:
            return rc
        from forest.credits import write_pack_credits
        from forest.paths import pack_root
        write_pack_credits(pack_root())   # data licenses require it
        if not args.keep_text:
            # ~130 MB per tile and regenerated by every build; the .dsf is what matters.
            txt.unlink(missing_ok=True)
        extra = sum(fsutil.size_mb(studio_path(tile, s))
                    for s in (".overlay.png", ".preview.png", ".stats.json"))
        # Peak of the whole build = the larger of this process's own peak (classify/vectorise) and
        # what it still holds plus DSFTool's peak during the compile. Auto parallel builds use it.
        build_peak = _memory_gb("peak")
        dsftool_peak = measured.get("peak_gb")
        peak_gb = build_peak
        if build_peak is not None and rss_at_compile is not None and dsftool_peak is not None:
            peak_gb = max(build_peak, rss_at_compile + dsftool_peak)
        history.record("build", tile=tile.name, seconds=round(time.time() - t0, 1),
                       dsf_mb=round(fsutil.size_mb(dsf), 2), out_extra_mb=round(extra, 2),
                       peak_gb=_round(peak_gb), build_peak_gb=_round(build_peak),
                       rss_at_compile_gb=_round(rss_at_compile), dsftool_peak_gb=_round(dsftool_peak))
    if args.install:
        # The whole pack is linked into X-Plane as one folder; make sure the link exists.
        from forest import pack
        print(pack.link_pack()["message"])
    return 0


def _memory_gb(which: str) -> float | None:
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
    import os

    cfg = config.load()
    dsf = dsf or txt.with_suffix(".dsf")
    dsf.parent.mkdir(parents=True, exist_ok=True)
    tmp = dsf.with_name(dsf.stem + ".building.dsf")
    tmp.unlink(missing_ok=True)
    rc = _run([str(cfg.dsftool), "--text2dsf", str(txt), str(tmp)], measured)
    if rc == 0:
        os.replace(tmp, dsf)
    else:
        tmp.unlink(missing_ok=True)
    return rc


def _run(cmd: list[str], measured: dict | None = None) -> int:
    if not Path(cmd[0]).exists():
        print(f"DSFTool not found at {cmd[0]} (it ships in tools/xptools/; reinstall ForestStudio)", file=sys.stderr)
        return 2
    from forest import proc

    if measured is None:
        r = proc.run(cmd)  # hidden: never opens a console window
    else:
        r, measured["peak_gb"] = proc.run_measured(cmd)
    for line in (r.stdout or "").splitlines() + (r.stderr or "").splitlines():
        if line.strip() and not line.startswith(("Scanning for", "Got dimension", "Got entire")):
            print(line, flush=True)
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    from forest import migrate

    for note in migrate.run() + migrate.run_packs():   # one-time moves (old layouts)
        print(f"[migrate] {note}", flush=True)
    p = argparse.ArgumentParser(prog="forest")
    sub = p.add_subparsers(required=True)

    c = sub.add_parser("catalog", help="catalog X-Plane 12 default forest assets")
    c.add_argument("--out", default=str(config.USER_CONFIG / "catalog_xp12.json"))
    c.set_defaults(func=cmd_catalog)

    c = sub.add_parser("poc", help="write the Phase 0 test overlay for tile +06-076")
    c.add_argument("--out", default=str(config.OUT_ROOT / "forest_poc"))
    c.add_argument("--density", type=int, default=200)
    c.add_argument("--compile", action="store_true", help="run DSFTool --text2dsf")
    c.add_argument("--season", choices=["spr", "sum", "fal", "win"],
                   help="experiment: add overlay season rasters forcing this season all year")
    c.set_defaults(func=cmd_poc)

    c = sub.add_parser("fetch", help="download/cache source data for one or more tiles")
    c.add_argument("--tile", help="e.g. +06-076")
    c.add_argument("--tiles", help="comma-separated list; OSM is read once for all of them")
    c.add_argument("--region", default=None, help="default: each tile's own region (the one covering most of it)")
    c.set_defaults(func=cmd_fetch)

    c = sub.add_parser("data", help="download/update a country-wide file (osm, ecoregions)")
    c.add_argument("what", choices=["osm", "ecoregions", "region"],
                   help="region = everything a newly added region needs (OSM file, ecoregions, cut-out)")
    c.add_argument("--region", default="colombia")
    c.add_argument("--update", action="store_true", help="download again if the server has a newer file")
    c.add_argument("--force", action="store_true", help="with --update: download even if not newer")
    c.set_defaults(func=cmd_data)

    c = sub.add_parser("build", help="build the forest overlay for a tile")
    c.add_argument("--tile", required=True, help="e.g. +06-076")
    c.add_argument("--profile", default="medium", help="config/profiles/<name>.toml")
    c.add_argument("--region", default=None,
                   help="whose forest types (config/rules/<name>.toml); default: the tile's own region")
    c.add_argument("--compile", action="store_true", help="run DSFTool --text2dsf")
    c.add_argument("--install", action="store_true",
                   help="also make sure the pack is linked into Custom Scenery")
    c.add_argument("--keep-text", action="store_true", help="keep the DSF text file after compiling")
    c.set_defaults(func=cmd_build)

    c = sub.add_parser("app", help="open the Forest Studio desktop app")
    c.set_defaults(func=lambda a: (__import__("forest.app.main", fromlist=["main"]).main(), 0)[1])

    c = sub.add_parser("decode", help="decode a DSF to text into cache/decoded")
    c.add_argument("dsf")
    c.add_argument("--label", help="prefix for the output file name")
    c.set_defaults(func=cmd_decode)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
