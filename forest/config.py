"""Where things live, and the per-machine configuration (where X-Plane 12 and DSFTool are).

The repository (REPO_ROOT) holds only code, the bundled DSFTool and the *default* profiles and
rules (config/). Everything that changes lives in the user's data folder (DATA_ROOT):

    ~/.foreststudio/            (override with the FORESTSTUDIO_HOME environment variable)
        config/local.toml       X-Plane location (written by the Settings view)
        config/profiles/*.toml  the user's profiles (copied from the defaults on first use)
        config/rules/*.toml     the user's forest types per region (same)
        config/catalog_xp12.json  X-Plane forest catalog, generated from this X-Plane
        cache/                  downloads, per-tile data, job records, build history
        out/                    built packs (linked into X-Plane) + the app's overlay/stats files

When local.toml doesn't exist, X-Plane is detected automatically (X-Plane's own install list,
then Steam libraries). DSFTool is the bundled one for this platform.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_DIR = REPO_ROOT / "config"          # shipped default profiles/ and rules/
DEFAULT_DATA_ROOT = Path.home() / ".foreststudio"
# When the user picks another data folder (Settings), this small file in the default folder
# remembers where it is. It's the only thing left in the default folder then.
LOCATION_FILE = DEFAULT_DATA_ROOT / "location.toml"


def resolve_data_root() -> tuple[Path, str]:
    """(folder, source): source is 'env' (FORESTSTUDIO_HOME), 'custom' (chosen in Settings) or
    'default' (~/.foreststudio)."""
    env = os.environ.get("FORESTSTUDIO_HOME")
    if env:
        return Path(env), "env"
    try:
        chosen = tomllib.loads(LOCATION_FILE.read_text(encoding="utf-8")).get("data_folder")
        if chosen:
            return Path(chosen), "custom"
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return DEFAULT_DATA_ROOT, "default"


def write_location(folder: Path | None) -> None:
    """Remember the chosen data folder (None or the default folder = forget the choice)."""
    if folder is None or Path(folder).resolve() == DEFAULT_DATA_ROOT.resolve():
        LOCATION_FILE.unlink(missing_ok=True)
        return
    LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text("# ForestStudio XP keeps its data here (chosen in Settings).\n"
                             f"data_folder = {json.dumps(str(Path(folder)))}\n",  # TOML basic string
                             encoding="utf-8")


DATA_ROOT, DATA_SOURCE = resolve_data_root()
CACHE_ROOT = DATA_ROOT / "cache"
OUT_ROOT = DATA_ROOT / "out"
USER_CONFIG = DATA_ROOT / "config"
LOCAL_TOML = USER_CONFIG / "local.toml"


def ensure_user_config() -> Path:
    """Copy any default profile/rules file the user doesn't have yet (never overwrites)."""
    for sub in ("profiles", "rules"):
        dst = USER_CONFIG / sub
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted((DEFAULTS_DIR / sub).glob("*.toml")):
            if not (dst / f.name).exists():
                shutil.copy2(f, dst / f.name)
    return USER_CONFIG


def profile_path(name: str) -> Path:
    return ensure_user_config() / "profiles" / f"{name}.toml"


def rules_path(region: str) -> Path:
    return ensure_user_config() / "rules" / f"{region}.toml"
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
                                     os.path.expanduser("~/.local/share/Steam"),
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
    if sys.platform == "win32":
        for drive in "CDEFG":
            libs += [Path(f"{drive}:/Steam"), Path(f"{drive}:/Games/Steam"), Path(f"{drive}:/SteamLibrary")]
    out += [lib / "steamapps" / "common" / "X-Plane 12" for lib in libs]
    # 3) Common stand-alone installer locations.
    out += [Path.home() / "Desktop" / "X-Plane 12", Path.home() / "X-Plane 12"]
    if sys.platform == "win32":
        out += [Path("C:/X-Plane 12"), Path("D:/X-Plane 12")]
    elif sys.platform == "darwin":
        out += [Path("/Applications/X-Plane 12")]
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


def _ensure_executable(p: Path) -> None:
    """macOS/Linux: a DSFTool from a zip download may have lost its executable bit."""
    if sys.platform == "win32" or not p.is_file() or os.access(p, os.X_OK):
        return
    try:
        p.chmod(p.stat().st_mode | 0o111)
    except OSError:
        pass  # read-only install: check_dsftool / the build log will say it can't run


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
    _ensure_executable(ds)
    return LocalConfig(xplane_root=xp, dsftool=ds, from_file=bool(xp_s))


def save(xplane_root: Path | str, dsftool: Path | str | None = None, path: Path | None = None) -> Path:
    """Write config/local.toml. DSFTool is written only when a custom one is given (the bundled one
    is found automatically); inside the project it's stored relative (portable)."""
    path = path or LOCAL_TOML
    text = ("# ForestStudio XP - this machine's settings (written by the app; not in git).\n"
            "# ForestStudio only READS from the X-Plane install, except its own Custom Scenery/forest_* link.\n\n"
            # json.dumps = a TOML basic string; a literal '...' breaks on paths like C:\Users\O'Neil
            f"[xplane]\nroot = {json.dumps(str(Path(xplane_root)))}\n")
    if dsftool and Path(dsftool).resolve() != BUNDLED_DSFTOOL.resolve():
        ds = Path(dsftool)
        try:
            ds_txt = str(ds.resolve().relative_to(REPO_ROOT))
        except ValueError:
            ds_txt = str(ds)
        text += f"\n[tools]\n# custom DSFTool (optional; the bundled one is used otherwise)\ndsftool = {json.dumps(ds_txt)}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
