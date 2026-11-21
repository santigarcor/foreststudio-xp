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


def _move(src: Path, dst: Path, notes: list[str]) -> None:
    """Move src to dst. Folders are merged item by item; existing items in dst win and the
    old copy is left where it was (reported), so nothing is ever overwritten."""
    if src.resolve() == dst.resolve():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        notes.append(f"moved {src} -> {dst}")
        return
    if src.is_dir() and dst.is_dir():
        for child in sorted(src.iterdir()):
            _move(child, dst / child.name, notes)
        try:
            src.rmdir()
        except OSError:
            pass  # something was left behind (conflict) - reported below
        return
    notes.append(f"kept {src} (already exists in {dst.parent})")


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
        _move(*pairs[0], notes)      # local.toml first: finding the links needs X-Plane's location
    relink = _region_links()
    for src, dst in pairs[1:]:
        if src.exists():
            _move(src, dst, notes)
    if relink and os.name == "nt":
        from forest import pack

        for region in relink:
            try:
                r = pack.link_pack(region)
                notes.append(f"X-Plane link re-pointed: {r['message']}")
            except Exception as exc:  # noqa: BLE001 - report, the user can reinstall the pack
                notes.append(f"could not re-point the X-Plane link for {region}: {exc}")
    return notes
