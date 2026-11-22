"""forest command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from forest import config


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

    from forest import history
    from forest.fetch.elevation import fetch_elevation
    from forest.fetch.osm import fetch_osm
    from forest.fetch.osm_pbf import DEFAULT_PBF, extract_tiles
    from forest.fetch.rasters import fetch_all
    from forest.resources import DOWNLOAD_WORKERS

    tiles = _tiles(args)
    _ensure_country_files(getattr(args, "region", "colombia"))

    def one(tile):
        had = (tile.cache_path / "worldcover.tif").exists()
        t0 = time.time()
        paths = fetch_all(tile)
        if not had:
            history.record("rasters", tile=tile.name, seconds=round(time.time() - t0, 1),
                           mb=round(sum(history.dir_mb(p) for p in paths.values()), 1))
        fetch_elevation(tile)
        return tile, sum(history.dir_mb(p) for p in paths.values())

    # Satellite windows download in parallel (network-bound).
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
    # OpenStreetMap: one pass over the country file for every tile that needs it.
    if DEFAULT_PBF.exists():
        t0 = time.time()
        counts = extract_tiles(tiles)
        if counts:
            secs = round(time.time() - t0, 1)
            mbs = [history.dir_mb(next(t for t in tiles if t.name == n).cache / "osm.json") for n in counts]
            history.record("osm_pass", tiles=len(counts), seconds=secs,
                           mb_per_tile=round(sum(mbs) / len(mbs), 1))
            print(f"OSM: one pass for {len(counts)} tile(s) in {secs:.0f} s", flush=True)
        else:
            print("OSM: already up to date", flush=True)
    else:
        for tile in tiles:
            fetch_osm(tile)
    if failed:
        print(f"data missing for: {', '.join(sorted(failed))}", flush=True)
        return 3
    return 0


def _ensure_country_files(region: str) -> None:
    """First use: download the OSM country file and the ecoregions map if they're missing."""
    from forest.fetch import sources

    pbf = sources.osm_file(region)
    if not pbf.exists():
        print(f"OSM country file missing: downloading {sources.OSM_URLS[region]}", flush=True)
        try:
            sources.download(sources.OSM_URLS[region], pbf, sources.print_progress("OSM"))
        except OSError as exc:
            print(f"OSM country file download FAILED: {exc}", flush=True)
    zp = sources.ecoregions_zip()
    from forest.fetch.ecoregions import clipped_path
    if not clipped_path().exists() and not zp.exists():
        print("ecoregions map missing: downloading", flush=True)
        try:
            sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
        except OSError as exc:
            print(f"ecoregions download FAILED: {exc}", flush=True)


def cmd_data(args: argparse.Namespace) -> int:
    """Download / update a country-wide file. For OSM, tiles that already have an extract are
    re-extracted from the new file (they then show as outdated until rebuilt)."""
    import time

    from forest.fetch import sources
    from forest.tiles import CACHE, Tile

    if args.what == "ecoregions":
        from forest.fetch.ecoregions import build_clip, clipped_path
        zp = sources.ecoregions_zip()
        if args.update or not zp.exists():
            sources.download(sources.ECOREGIONS_URL, zp, sources.print_progress("ecoregions"))
            clipped_path().unlink(missing_ok=True)
        build_clip()
        print("ecoregions: ready", flush=True)
        return 0

    from forest.fetch.osm_pbf import extract_tiles
    url, pbf = sources.OSM_URLS[args.region], sources.osm_file(args.region)
    if args.update or not pbf.exists():
        if pbf.exists() and not args.force:
            chk = sources.check_update(pbf, url)
            if not chk["newer"]:
                print("OSM country file: already the newest version", flush=True)
                return 0
        print(f"OSM country file: downloading {url}", flush=True)
        sources.download(url, pbf, sources.print_progress("OSM"))
    tiles = [Tile.parse(d.name) for d in sorted(CACHE.glob("*")) if (d / "osm.json").exists()]
    if tiles:
        t0 = time.time()
        extract_tiles(tiles, pbf, force=True)
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
        dsf = dsf_path(args.region, tile)
        rc = compile_dsf(txt, dsf)
        if rc:
            return rc
        from forest.credits import write_pack_credits
        from forest.paths import pack_root
        write_pack_credits(pack_root(args.region), args.region)   # data licenses require it
        if not args.keep_text:
            # ~130 MB per tile and regenerated by every build; the .dsf is what matters.
            txt.unlink(missing_ok=True)
        extra = sum(history.dir_mb(studio_path(args.region, tile, s))
                    for s in (".overlay.png", ".preview.png", ".stats.json"))
        try:  # this process's peak memory (the heavy part; DSFTool runs after it is freed)
            import psutil
            mi = psutil.Process().memory_info()
            peak_gb = round(getattr(mi, "peak_wset", mi.rss) / 1e9, 2)
        except Exception:  # noqa: BLE001
            peak_gb = None
        history.record("build", tile=tile.name, seconds=round(time.time() - t0, 1),
                       dsf_mb=round(history.dir_mb(dsf), 2), out_extra_mb=round(extra, 2), peak_gb=peak_gb)
    if args.install:
        # The whole pack is linked into X-Plane as one folder; make sure the link exists.
        from forest import pack
        print(pack.link_pack(args.region)["message"])
    return 0


def compile_dsf(txt: Path, dsf: Path | None = None) -> int:
    """Compile DSF text to `dsf` (default: next to the text). Written to a temporary name and
    swapped in atomically, so X-Plane never reads a half-written tile."""
    import os

    cfg = config.load()
    dsf = dsf or txt.with_suffix(".dsf")
    dsf.parent.mkdir(parents=True, exist_ok=True)
    tmp = dsf.with_name(dsf.stem + ".building.dsf")
    tmp.unlink(missing_ok=True)
    rc = _run([str(cfg.dsftool), "--text2dsf", str(txt), str(tmp)])
    if rc == 0:
        os.replace(tmp, dsf)
    else:
        tmp.unlink(missing_ok=True)
    return rc


def _run(cmd: list[str]) -> int:
    if not Path(cmd[0]).exists():
        print(f"DSFTool not found at {cmd[0]} (it ships in tools/xptools/; reinstall ForestStudio)", file=sys.stderr)
        return 2
    from forest import proc

    r = proc.run(cmd)  # hidden: never opens a console window
    for line in (r.stdout or "").splitlines() + (r.stderr or "").splitlines():
        if line.strip() and not line.startswith(("Scanning for", "Got dimension", "Got entire")):
            print(line, flush=True)
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    from forest import migrate

    for note in migrate.run():   # one-time: old in-repo data -> ~/.foreststudio
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
    c.add_argument("--region", default="colombia")
    c.set_defaults(func=cmd_fetch)

    c = sub.add_parser("data", help="download/update a country-wide file (osm, ecoregions)")
    c.add_argument("what", choices=["osm", "ecoregions"])
    c.add_argument("--region", default="colombia")
    c.add_argument("--update", action="store_true", help="download again if the server has a newer file")
    c.add_argument("--force", action="store_true", help="with --update: download even if not newer")
    c.set_defaults(func=cmd_data)

    c = sub.add_parser("build", help="build the forest overlay for a tile")
    c.add_argument("--tile", required=True, help="e.g. +06-076")
    c.add_argument("--profile", default="medium", help="config/profiles/<name>.toml")
    c.add_argument("--region", default="colombia", help="config/rules/<name>.toml")
    c.add_argument("--compile", action="store_true", help="run DSFTool --text2dsf")
    c.add_argument("--install", action="store_true",
                   help="also make sure the region's pack is linked into Custom Scenery")
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
