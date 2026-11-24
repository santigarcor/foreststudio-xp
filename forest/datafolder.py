"""Change where ForestStudio keeps its data (Settings → Data folder).

Moving = every item of the current data folder goes to the new one (a rename on the same
drive, a copy on another drive), ForestStudio's own X-Plane links are re-pointed, and the
choice is remembered in ~/.foreststudio/location.toml. The app restarts afterwards, because
every module reads its folders once at start.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from forest import config
from forest.fsutil import move_merge, size_bytes

MARKERS = ("config", "cache", "out")   # a folder holding any of these is a ForestStudio data folder


def _is_data_folder(p: Path) -> bool:
    return p.is_dir() and any((p / m).exists() for m in MARKERS)


def _existing_parent(p: Path) -> Path:
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _inside(a: Path, b: Path) -> bool:
    """Is a the same as, or inside, b?"""
    try:
        a, b = a.resolve(), b.resolve()
    except OSError:
        return False
    return a == b or b in a.parents


def info() -> dict:
    return {
        "current": str(config.DATA_ROOT), "default": str(config.DEFAULT_DATA_ROOT),
        "source": config.DATA_SOURCE, "is_default": config.DATA_SOURCE == "default",
        "locked": config.DATA_SOURCE == "env",   # FORESTSTUDIO_HOME wins over the setting
    }


def check(folder: str) -> dict:
    """What would happen if the data folder became `folder`."""
    raw = (folder or "").strip().strip('"')
    if not raw:
        return {"ok": False, "error": "Choose a folder."}
    target = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not target.is_absolute():
        return {"ok": False, "error": "Use a full path, like D:\\ForestStudio."}
    cur = config.DATA_ROOT
    # A non-empty folder that isn't ours (e.g. D:\ itself): use a subfolder inside it.
    note = ""
    is_default = target.resolve() == config.DEFAULT_DATA_ROOT.resolve()   # may hold location.toml
    if not is_default and target.is_dir() and any(target.iterdir()) and not _is_data_folder(target) \
            and not _inside(target, cur):
        target = target / "ForestStudio"
        note = f"That folder already has other files, so ForestStudio will use {target}."
    if _inside(target, cur) and target.resolve() == cur.resolve():
        return {"ok": True, "same": True, "target": str(target), "message": "This is already the data folder."}
    if _inside(target, cur):
        return {"ok": False, "error": "The new folder can't be inside the current data folder."}
    if _inside(cur, target):
        return {"ok": False, "error": "The new folder can't contain the current data folder."}
    xp = config.load().xplane_root
    if str(xp) not in ("", ".") and _inside(target, xp):
        return {"ok": False, "error": "Don't put ForestStudio's data inside the X-Plane folder."}
    if _inside(target, config.REPO_ROOT):
        return {"ok": False, "error": "Don't put the data inside ForestStudio's program folder."}
    parent = _existing_parent(target)
    if not parent.exists():
        return {"ok": False, "error": f"{target.anchor or target} wasn't found (is the drive connected?)."}
    if not os.access(parent, os.W_OK):
        return {"ok": False, "error": f"Can't write to {parent}."}

    has_data = _is_data_folder(target)
    size = size_bytes(cur)
    try:
        free = shutil.disk_usage(parent).free
    except OSError:
        free = None
    same_drive = os.path.splitdrive(str(parent.resolve()))[0].lower() == \
        os.path.splitdrive(str(cur.resolve()))[0].lower()
    if not has_data and not same_drive and free is not None and free < size * 1.05:
        return {"ok": False, "error": f"Not enough free space there: need {size / 1e9:.1f} GB, "
                                      f"{free / 1e9:.1f} GB free."}
    if has_data:
        msg = ("That folder already has ForestStudio data. ForestStudio will switch to it; your "
               "current data stays where it is.")
    elif same_drive:
        msg = f"Your data ({size / 1e9:.1f} GB) will be moved there. Same drive, so it's quick."
    else:
        msg = (f"Your data ({size / 1e9:.1f} GB) will be copied to the other drive and then removed "
               f"here. This can take a few minutes.")
    return {"ok": True, "same": False, "target": str(target), "has_data": has_data,
            "size_gb": round(size / 1e9, 2), "free_gb": round(free / 1e9, 1) if free is not None else None,
            "same_drive": same_drive, "note": note, "message": msg}


def _links_into(out_dir: Path) -> list[tuple[Path, str]]:
    """ForestStudio's own X-Plane links (Custom Scenery/forest_*) pointing into out_dir, each with
    the name of the folder it points at (a link may be named forest_colombia but point at
    forest_studio_xp)."""
    from forest import pack

    found = []
    try:
        cs = config.load().custom_scenery
        old = out_dir.resolve()
        for link in cs.glob("forest_*"):
            if not pack._is_junction(link):
                continue
            try:
                t = Path(os.path.realpath(link)).resolve()
            except OSError:
                continue
            if old in t.parents:
                found.append((link, t.relative_to(old).parts[0]))
    except OSError:
        pass
    return found


def repoint_links(links: list[tuple[Path, str]], new_out: Path) -> list[str]:
    """Point each forest_* junction at the same folder in new_out. Only our own forest_* links."""
    from forest import pack

    notes = []
    for link, name in links:
        if not link.name.startswith("forest_"):
            continue
        target = new_out / name
        if not target.is_dir():
            notes.append(f"{link.name}: nothing to link in the new folder; left as it was")
            continue
        try:
            pack.remove_link(link)
            pack.make_link(target, link)
            notes.append(f"X-Plane link {link.name} now points to {target}")
        except OSError as exc:
            notes.append(f"could not re-point {link.name}: {exc} (use Install pack again)")
    return notes


def change(folder: str) -> dict:
    """Move (or switch) to a new data folder. The app must restart afterwards."""
    c = check(folder)
    if not c["ok"] or c.get("same"):
        return c
    if config.DATA_SOURCE == "env":
        return {"ok": False, "error": "The data folder is set by FORESTSTUDIO_HOME; change that instead."}
    cur, target = config.DATA_ROOT, Path(c["target"])
    links = _links_into(cur / "out")
    notes: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    if not c["has_data"]:
        for item in sorted(cur.iterdir()) if cur.exists() else []:
            if item.resolve() == config.LOCATION_FILE.resolve():
                continue                      # the pointer stays in the default folder
            move_merge(item, target / item.name, notes)
        notes.insert(0, f"moved {c['size_gb']} GB to {target}")
        if cur != config.DEFAULT_DATA_ROOT:
            try:
                cur.rmdir()                   # the old custom folder, if now empty
            except OSError:
                pass
    else:
        notes.insert(0, f"switched to the existing data in {target}")
    notes += repoint_links(links, target / "out")
    config.write_location(target)
    return {"ok": True, "target": str(target), "notes": notes, "restart": True}
