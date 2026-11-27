"""forest command line: the same work the app runs in its workers (forest.tasks), for debugging
and scripting. Progress events print as plain lines here (forest.events)."""

from __future__ import annotations

import argparse
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
    from forest import tasks
    return tasks.fetch([t.name for t in _tiles(args)], region=args.region)


def cmd_data(args: argparse.Namespace) -> int:
    """Download / update a country-wide file. For OSM, tiles that already have an extract are
    re-extracted from the new file (they then show as outdated until rebuilt)."""
    from forest import tasks
    if args.what == "region":
        return tasks.data_region(args.region)
    if args.what == "ecoregions":
        return tasks.data_ecoregions(args.region, update=args.update)
    return tasks.data_osm(args.region, update=args.update, force=args.force)


def cmd_build(args: argparse.Namespace) -> int:
    from forest import tasks
    return tasks.build(args.tile, profile=args.profile, region=args.region, compile=args.compile,
                       install=args.install, keep_text=args.keep_text)


def compile_dsf(txt: Path, dsf: Path | None = None, measured: dict | None = None) -> int:
    from forest import tasks
    return tasks.compile_dsf(txt, dsf, measured)


def _run(cmd: list[str], measured: dict | None = None) -> int:
    from forest import tasks
    return tasks.run_tool(cmd, measured)


def main(argv: list[str] | None = None) -> int:
    from forest import migrate
    from forest.fetch import osm_tiles
    for note in migrate.run() + migrate.run_packs() + osm_tiles.ensure_all():   # old layouts / OSM tile lists
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
