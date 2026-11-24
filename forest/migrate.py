"""One-time move of data from the old in-repository layout to the data folder.

Before v0.2 everything that changes lived inside the repository (cache/, out/,
config/local.toml, config/catalog_xp12.json). Now it lives in config.DATA_ROOT
(~/.foreststudio). This moves it there on the first start, and re-points ForestStudio's own
X-Plane links (Custom Scenery/forest_<region>) at the moved packs. Nothing else in X-Plane is
touched. Safe to run every start: it does nothing once the old folders are gone.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from forest import config
from forest.fsutil import move_merge


def _legacy_pairs() -> list[tuple[Path, Path]]:
    old = config.REPO_ROOT
    return [
        (old / "config" / "local.toml", config.LOCAL_TOML),   # first: X-Plane's location
        (old / "config" / "catalog_xp12.json", config.USER_CONFIG / "catalog_xp12.json"),
        (old / "cache", config.CACHE_ROOT),
        (old / "out", config.OUT_ROOT),
    ]


def pending() -> bool:
    return any(src.exists() for src, _ in _legacy_pairs())


def _region_links() -> list[str]:
    """Regions whose X-Plane link points at a pack in the old out/ folder."""
    from forest import pack

    old_out = (config.REPO_ROOT / "out").resolve()
    regions = []
    try:
        cs = config.load().custom_scenery
        for link in cs.glob("forest_*"):
            if not pack._is_junction(link):
                continue
            try:
                target = Path(os.path.realpath(link)).resolve()
            except OSError:
                continue
            if target == old_out / link.name or old_out in target.parents:
                regions.append(link.name.removeprefix("forest_"))
    except OSError:
        pass
    return regions


def run() -> list[str]:
    """Move old data into the data folder. Returns what was done (empty = nothing to do)."""
    if not pending():
        return []
    notes: list[str] = []
    pairs = _legacy_pairs()
    if pairs[0][0].exists():
        move_merge(*pairs[0], notes, report_moves=True)      # local.toml first: finding the links needs X-Plane's location
    relink = _region_links()
    for src, dst in pairs[1:]:
        if src.exists():
            move_merge(src, dst, notes, report_moves=True)
    if relink:
        from forest import pack

        for region in relink:      # same folder name in the data folder (run_packs merges it later)
            try:
                link = config.load().custom_scenery / f"forest_{region}"
                pack.remove_link(link)
                pack.make_link(config.OUT_ROOT / f"forest_{region}", link)
                notes.append(f"X-Plane link re-pointed: {link} → {config.OUT_ROOT / link.name}")
            except Exception as exc:  # noqa: BLE001 - report, the user can reinstall the pack
                notes.append(f"could not re-point the X-Plane link for {region}: {exc}")
    return notes


# ---------- one pack for every region (2026-11-23) ----------

def _move_newer(src: Path, dst: Path) -> bool:
    """Move src to dst unless dst is newer (then src is deleted). True if src won."""
    if dst.exists():
        if dst.stat().st_mtime >= src.stat().st_mtime:
            src.unlink()
            return False
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


def _remove_empty_dirs(root: Path) -> None:
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def run_packs() -> list[str]:
    """Merge the old per-region packs (out/forest_<region>/, out/studio_<region>/) into the one
    pack (out/forest_studio_xp/, out/studio/). A tile in two old packs keeps its newer build.
    ForestStudio's own X-Plane link to an old pack (e.g. Custom Scenery/forest_colombia) is
    re-pointed at the new pack under its old name, so it keeps its place in scenery_packs.ini;
    links to other old packs are removed (X-Plane would show the same tiles twice). Nothing else
    in X-Plane is touched. Does nothing once the old folders are gone."""
    from forest import pack
    from forest.credits import CREDITS_FILE, write_pack_credits
    from forest.paths import legacy_packs, pack_root, studio_dir

    out = config.OUT_ROOT
    old_packs = legacy_packs()
    old_studios = sorted(p for p in out.glob("studio_*") if p.is_dir()) if out.exists() else []
    if not old_packs and not old_studios:
        return []
    notes: list[str] = []
    target, studio = pack_root(), studio_dir()

    # Our X-Plane links that point at an old pack (found before the folders move).
    links: list[Path] = []
    xp = None
    try:
        xp = config.load().xplane_root
        olds = {p.resolve() for p in old_packs}
        for link in (xp / "Custom Scenery").glob("forest_*"):
            if pack._is_junction(link) and pack.link_target(link) in olds:
                links.append(link)
    except OSError:
        pass

    moved = 0
    for old in old_packs:
        old_studio = out / f"studio_{old.name.removeprefix('forest_')}"
        for f in sorted(old.glob("Earth nav data/*/*.dsf")):
            if f.name.endswith(".building.dsf"):
                f.unlink()                            # a half-written leftover
                continue
            if _move_newer(f, target / f.relative_to(old)):
                moved += 1
                for s in studio.glob(f"{f.stem}.*"):  # the older build's app files
                    s.unlink()
            else:
                for s in old_studio.glob(f"{f.stem}.*"):
                    s.unlink()
                notes.append(f"{f.stem}: kept the newer build already in {target.name}")
        (old / CREDITS_FILE).unlink(missing_ok=True)
        _remove_empty_dirs(old)
        if old.exists():
            notes.append(f"{old} still has other files; left it there")
    for old_studio in old_studios:
        for s in sorted(old_studio.iterdir()):
            if s.is_file():
                _move_newer(s, studio / s.name)
        _remove_empty_dirs(old_studio)
    if moved:
        write_pack_credits(target)
        notes.append(f"moved {moved} built tile(s) into {target}")

    if links and xp is not None:
        lines = pack.ini_lines(xp)

        def rank(link: Path) -> tuple:
            n = pack.ini_line(link.name, lines=lines)
            return (n is None or n < 0, abs(n) if n else 0, link.name)

        keep, *others = sorted(links, key=rank)
        (target / "Earth nav data").mkdir(parents=True, exist_ok=True)
        if not pack.our_links(xp):
            try:
                pack.remove_link(keep)
                pack.make_link(target, keep)
                notes.append(f"X-Plane link {keep.name} now points to {target} (same place in the scenery order)")
            except OSError as exc:
                notes.append(f"could not re-point {keep.name}: {exc} (use Install pack)")
        else:
            others.insert(0, keep)
        for link in others:
            try:
                pack.remove_link(link)
                notes.append(f"removed the X-Plane link {link.name} (its tiles are in {target.name} now)")
            except OSError as exc:
                notes.append(f"could not remove {link.name}: {exc}")
    return notes
