"""Local, per-machine configuration: where X-Plane 12 and DSFTool are.

Stored in config/local.toml (not in git). When it doesn't exist, X-Plane is detected
automatically (X-Plane's own install list, then Steam libraries) and DSFTool is looked for in
the usual places inside the project. The app's Settings view edits and validates this file.
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TOML = REPO_ROOT / "config" / "local.toml"
DSFTOOL_EXE = "DSFTool.exe" if sys.platform == "win32" else "DSFTool"


@dataclass(frozen=True)
class LocalConfig:
    xplane_root: Path
    dsftool: Path
    from_file: bool = False  # False = detected / defaults (nothing saved yet)

    @property
    def default_forests(self) -> Path:
        return self.xplane_root / "Resources" / "default scenery" / "1200 forests"

    @property
    def custom_scenery(self) -> Path:
        return self.xplane_root / "Custom Scenery"


# ---------------------------------------------------------------- detection
def xplane_candidates() -> list[Path]:
    """Likely X-Plane 12 folders on this machine, best first (may not all be valid)."""
    out: list[Path] = []
    # 1) X-Plane writes its install folders here (one per line).
    for base in (os.environ.get("LOCALAPPDATA"), os.path.expanduser("~/Library/Preferences"),
                 os.path.expanduser("~/.x-plane")):
        if base:
            f = Path(base) / "x-plane_install_12.txt"
            try:
                out += [Path(ln.strip()) for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip()]
            except OSError:
                pass
    # 2) Steam libraries (libraryfolders.vdf lists every library path).
    steam_roots = [Path(p) for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
                                     os.path.expanduser("~/.steam/steam"),
                                     os.path.expanduser("~/Library/Application Support/Steam"))]
    libs: list[Path] = []
    for root in steam_roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            libs += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', text)]
        except OSError:
            pass
        libs.append(root)
    for drive in "CDEFG":
        libs += [Path(f"{drive}:/Steam"), Path(f"{drive}:/Games/Steam"), Path(f"{drive}:/SteamLibrary")]
    out += [lib / "steamapps" / "common" / "X-Plane 12" for lib in libs]
    # 3) Common stand-alone installer locations.
    out += [Path.home() / "Desktop" / "X-Plane 12", Path("C:/X-Plane 12"), Path("D:/X-Plane 12")]
    seen, uniq = set(), []
    for p in out:
        key = str(p).lower().rstrip("\\/")
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def detect_xplane() -> Path | None:
    for p in xplane_candidates():
        if check_xplane(p)["ok"]:
            return p
    return None


PLATFORM_DIR = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
BUNDLED_DSFTOOL = REPO_ROOT / "tools" / "xptools" / PLATFORM_DIR / DSFTOOL_EXE


def dsftool_candidates() -> list[Path]:
    """The DSFTool shipped in the repo first, then XPTools unzipped into the project."""
    out = [BUNDLED_DSFTOOL]
    out += [p / "tools" / DSFTOOL_EXE for p in sorted(REPO_ROOT.glob("xptools_*"))]
    out += [REPO_ROOT / "tools" / "xptools" / DSFTOOL_EXE, REPO_ROOT / "tools" / DSFTOOL_EXE]
    return out


def detect_dsftool() -> Path | None:
    for p in dsftool_candidates():
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------- validation
def check_xplane(root: Path | str | None) -> dict:
    """Is this an X-Plane 12 folder with everything ForestStudio needs?"""
    if not root:
        return {"ok": False, "problems": ["No folder chosen"]}
    root = Path(root)
    checks = {
        "folder exists": root.is_dir(),
        "Custom Scenery folder": (root / "Custom Scenery").is_dir(),
        "default forest library (Resources/default scenery/1200 forests)":
            (root / "Resources" / "default scenery" / "1200 forests" / "library.txt").is_file(),
        "Global Scenery (base mesh, used for elevation)":
            (root / "Global Scenery" / "X-Plane 12 Global Scenery" / "Earth nav data").is_dir(),
    }
    return {"ok": all(checks.values()), "checks": checks,
            "problems": [k for k, v in checks.items() if not v]}


def check_dsftool(path: Path | str | None) -> dict:
    ok = bool(path) and Path(path).is_file()
    return {"ok": ok, "problems": [] if ok else ["DSFTool missing from tools/xptools/ "
                                                 "(reinstall ForestStudio)"]}


# ---------------------------------------------------------------- load / save
def load(path: Path | None = None) -> LocalConfig:
    path = path or LOCAL_TOML
    data: dict = {}
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    xp_s = data.get("xplane", {}).get("root")
    xp = Path(xp_s) if xp_s else (detect_xplane() or Path(""))
    # DSFTool ships with the app (tools/xptools/<platform>/). local.toml may still name another one by
    # hand (advanced; no UI) - it's used only if that file exists, otherwise the bundled one.
    ds_s = data.get("tools", {}).get("dsftool")
    ds = None
    if ds_s:
        ds = Path(ds_s)
        if not ds.is_absolute():
            ds = REPO_ROOT / ds
        if not ds.is_file():
            ds = None
    ds = ds or detect_dsftool() or BUNDLED_DSFTOOL
    return LocalConfig(xplane_root=xp, dsftool=ds, from_file=bool(xp_s))


def save(xplane_root: Path | str, dsftool: Path | str | None = None, path: Path | None = None) -> Path:
    """Write config/local.toml. DSFTool is written only when a custom one is given (the bundled one
    is found automatically); inside the project it's stored relative (portable)."""
    path = path or LOCAL_TOML
    text = ("# ForestStudio XP - this machine's settings (written by the app; not in git).\n"
            "# ForestStudio only READS from the X-Plane install, except its own Custom Scenery/forest_* link.\n\n"
            f"[xplane]\nroot = '{Path(xplane_root)}'\n")
    if dsftool and Path(dsftool).resolve() != BUNDLED_DSFTOOL.resolve():
        ds = Path(dsftool)
        try:
            ds_txt = str(ds.resolve().relative_to(REPO_ROOT))
        except ValueError:
            ds_txt = str(ds)
        text += f"\n[tools]\n# custom DSFTool (optional; the bundled one is used otherwise)\ndsftool = '{ds_txt}'\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
