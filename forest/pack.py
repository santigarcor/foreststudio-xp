"""Install / uninstall ForestStudio's pack in X-Plane as ONE linked folder (like OrthoStudio).

    X-Plane 12/Custom Scenery/forest_studio_xp  ──junction──▶  ~/.foreststudio/out/forest_studio_xp

On Windows it's a directory junction (no admin rights or Developer Mode needed); on macOS and
Linux a symbolic link. Every built tile (any region) is then in X-Plane at once; uninstalling
removes only the link, never ForestStudio's files. Only links named forest_* are ever created or
removed. A link with an older name (forest_colombia, from when every region had its own pack)
that points at the pack counts as installed: it keeps its place in scenery_packs.ini.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from forest import config
from forest.paths import PACK_NAME, pack_root


def make_link(target: Path, link: Path) -> None:
    """Folder link: a directory junction on Windows (no admin needed), a symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def remove_link(link: Path) -> None:
    """Remove only the link itself, never what it points to."""
    if os.name == "nt" and not link.is_symlink():
        os.rmdir(link)        # a junction
    else:
        os.unlink(link)       # a symlink


def _is_junction(p: Path) -> bool:
    """True for a directory junction or a symlink (never follows the link)."""
    try:
        if p.is_symlink():
            return True
        if os.name != "nt":
            return False
        # os.path.isjunction is Python 3.12+; read the reparse tag directly so 3.11 works too.
        st = os.lstat(p)
        return getattr(st, "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT
    except OSError:
        return False


def link_target(link: Path) -> Path | None:
    try:
        return Path(os.path.realpath(link)).resolve()
    except OSError:
        return None


def _points_at(link: Path, target: Path) -> bool:
    got = link_target(link)
    try:
        return got is not None and got == target.resolve()
    except OSError:
        return False


def _custom_scenery(xplane_root: Path | None = None) -> Path:
    root = xplane_root if xplane_root is not None else config.load().xplane_root
    return root / "Custom Scenery"


def our_links(xplane_root: Path | None = None) -> list[Path]:
    """forest_* links in Custom Scenery that point at the pack (normally just forest_studio_xp,
    or an older forest_<region> link re-pointed by migrate.run_packs). Sorted, ours first."""
    cs, target = _custom_scenery(xplane_root), pack_root()
    try:
        found = [p for p in cs.glob("forest_*") if _is_junction(p) and _points_at(p, target)]
    except OSError:
        return []
    return sorted(found, key=lambda p: (p.name != PACK_NAME, p.name))


def _link_path(xplane_root: Path | None = None) -> Path:
    """The pack's link: an existing one pointing at the pack, else Custom Scenery/forest_studio_xp."""
    links = our_links(xplane_root)
    return links[0] if links else _custom_scenery(xplane_root) / PACK_NAME


def ini_lines(xplane_root: Path | None = None) -> list[str]:
    root = xplane_root if xplane_root is not None else config.load().xplane_root
    try:
        return (root / "Custom Scenery" / "scenery_packs.ini").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def ini_line(name: str, xplane_root: Path | None = None, lines: list[str] | None = None) -> int | None:
    """Line number of a pack in scenery_packs.ini (negative = disabled; None = not listed)."""
    want = f"Custom Scenery/{name}/"
    for i, ln in enumerate(lines if lines is not None else ini_lines(xplane_root), 1):
        if ln.startswith("SCENERY_PACK") and ln.strip().split(" ", 1)[-1] == want:
            return i if not ln.startswith("SCENERY_PACK_DISABLED") else -i
    return None


def pack_status() -> dict:
    xp = config.load().xplane_root   # read local.toml once (it may also run X-Plane detection)
    link, target = _link_path(xp), pack_root()
    linked = _is_junction(link)
    points_ok = linked and _points_at(link, target)
    line = ini_line(link.name, xp)
    tiles = sorted(p.stem for p in target.glob("Earth nav data/*/*.dsf")) if target.exists() else []
    return {
        "pack": link.name, "link": str(link), "target": str(target),
        "installed": points_ok, "linked_elsewhere": linked and not points_ok,
        "real_folder": link.exists() and not linked,
        "in_ini": line is not None and line > 0, "ini_disabled": line is not None and line < 0,
        "tiles": tiles,
    }


def _absorb_real_folder(link: Path, target: Path) -> list[str]:
    """Turn an existing real pack folder (older per-tile installs) into nothing, safely:
    tiles missing from the project are moved into it; duplicates (copies/hard links of the
    project's files) are deleted. Refuses if it holds anything other than .dsf tiles (and our
    own CREDITS.txt)."""
    from forest.credits import CREDITS_FILE

    others = [p for p in link.rglob("*") if p.is_file() and p.suffix.lower() != ".dsf"
              and not (p.parent == link and p.name == CREDITS_FILE)]
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
    (link / CREDITS_FILE).unlink(missing_ok=True)   # the project's pack has its own
    for d in sorted((p for p in link.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        d.rmdir()
    link.rmdir()
    return moved


def link_pack() -> dict:
    link, target = _link_path(), pack_root()
    if not link.name.startswith("forest_"):
        raise RuntimeError("only forest_* packs can be installed")
    (target / "Earth nav data").mkdir(parents=True, exist_ok=True)
    from forest.credits import write_pack_credits
    write_pack_credits(target)
    st = pack_status()
    if st["installed"]:
        return {"ok": True, "message": f"{link.name} is already linked into X-Plane", **st}
    moved = []
    if st["linked_elsewhere"]:
        remove_link(link)
    elif st["real_folder"]:
        moved = _absorb_real_folder(link, target)
    make_link(target, link)
    msg = f"linked {link} → {target}"
    if moved:
        msg += f" (moved {len(moved)} tile(s) that only existed in X-Plane into ForestStudio's data folder)"
    return {"ok": True, "message": msg, **pack_status()}


def unlink_pack() -> dict:
    links = our_links()
    if links:
        for link in links:
            remove_link(link)  # the link only; ForestStudio's pack folder is untouched
        return {"ok": True, "message": f"removed link {links[0]} (built tiles kept)", **pack_status()}
    link = _link_path()
    if link.exists():
        return {"ok": False, "message": f"{link} is a real folder, not a link; leaving it alone",
                **pack_status()}
    return {"ok": True, "message": "not installed", **pack_status()}
