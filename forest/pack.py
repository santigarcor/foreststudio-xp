"""Install / uninstall a region's pack in X-Plane as ONE linked folder (like OrthoStudio).

    X-Plane 12/Custom Scenery/forest_<region>  ──junction──▶  ~/.foreststudio/out/forest_<region>

A directory junction needs no admin rights or Developer Mode. Every tile built into the
data folder's pack is then in X-Plane at once; uninstalling removes only the junction,
never ForestStudio's files. Only folders named forest_* are ever created or removed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from forest import config
from forest.paths import pack_name, pack_root


def _link_path(region: str) -> Path:
    return config.load().xplane_root / "Custom Scenery" / pack_name(region)


def _is_junction(p: Path) -> bool:
    try:
        return os.path.isjunction(p) or p.is_symlink()
    except OSError:
        return False


def _ini_line(region: str) -> int | None:
    """Line number of the pack in scenery_packs.ini (None if X-Plane doesn't list it yet)."""
    ini = config.load().xplane_root / "Custom Scenery" / "scenery_packs.ini"
    try:
        lines = ini.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    want = f"Custom Scenery/{pack_name(region)}/"
    for i, ln in enumerate(lines, 1):
        if ln.strip().split(" ", 1)[-1] == want and ln.startswith("SCENERY_PACK"):
            return i if not ln.startswith("SCENERY_PACK_DISABLED") else -i
    return None


def pack_status(region: str) -> dict:
    link, target = _link_path(region), pack_root(region)
    linked = _is_junction(link)
    points_ok = False
    if linked:
        try:
            points_ok = Path(os.path.realpath(link)).resolve() == target.resolve()
        except OSError:
            points_ok = False
    line = _ini_line(region)
    tiles = sorted(p.stem for p in target.glob("Earth nav data/*/*.dsf")) if target.exists() else []
    return {
        "pack": pack_name(region), "link": str(link), "target": str(target),
        "installed": linked and points_ok, "linked_elsewhere": linked and not points_ok,
        "real_folder": link.exists() and not linked,
        "in_ini": line is not None and line > 0, "ini_disabled": line is not None and line < 0,
        "tiles": tiles,
    }


def _absorb_real_folder(link: Path, target: Path) -> list[str]:
    """Turn an existing real pack folder (older per-tile installs) into nothing, safely:
    tiles missing from the project are moved into it; duplicates (copies/hard links of the
    project's files) are deleted. Refuses if it holds anything other than .dsf tiles."""
    others = [p for p in link.rglob("*") if p.is_file() and p.suffix.lower() != ".dsf"]
    if others:
        raise RuntimeError(f"{link} contains non-tile files (e.g. {others[0].name}); not touching it")
    moved = []
    for f in link.rglob("*.dsf"):
        rel = f.relative_to(link)
        dst = target / rel
        if dst.exists():
            f.unlink()  # same tile already in the project (hard link or copy)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            moved.append(f.stem)
    for d in sorted((p for p in link.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        d.rmdir()
    link.rmdir()
    return moved


def link_pack(region: str) -> dict:
    import _winapi  # Windows: CreateJunction (no admin needed)

    name = pack_name(region)
    if not name.startswith("forest_"):
        raise RuntimeError("only forest_* packs can be installed")
    link, target = _link_path(region), pack_root(region)
    (target / "Earth nav data").mkdir(parents=True, exist_ok=True)
    st = pack_status(region)
    if st["installed"]:
        return {"ok": True, "message": f"{name} is already linked into X-Plane", **pack_status(region)}
    moved = []
    if st["linked_elsewhere"]:
        os.rmdir(link)  # removes only the junction
    elif st["real_folder"]:
        moved = _absorb_real_folder(link, target)
    _winapi.CreateJunction(str(target), str(link))
    msg = f"linked {link} → {target}"
    if moved:
        msg += f" (moved {len(moved)} tile(s) that only existed in X-Plane into ForestStudio's data folder)"
    return {"ok": True, "message": msg, **pack_status(region)}


def unlink_pack(region: str) -> dict:
    link = _link_path(region)
    if not pack_name(region).startswith("forest_"):
        raise RuntimeError("only forest_* packs can be uninstalled")
    if _is_junction(link):
        os.rmdir(link)  # removes the junction only; ForestStudio's pack folder is untouched
        return {"ok": True, "message": f"removed link {link} (built tiles kept)", **pack_status(region)}
    if link.exists():
        return {"ok": False, "message": f"{link} is a real folder, not a link; leaving it alone",
                **pack_status(region)}
    return {"ok": True, "message": "not installed", **pack_status(region)}
