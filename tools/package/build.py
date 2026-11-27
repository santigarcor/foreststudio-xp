"""Build the ForestStudio XP installer for the system this runs on.

    python tools/package/build.py [--out dist] [--check] [--keep-stage]

Windows → ForestStudio-XP-<v>-windows-x64-setup.exe   (Inno Setup 6)
macOS   → ForestStudio-XP-<v>-macos-<arm64|x86_64>.dmg (hdiutil; unsigned)
Linux   → ForestStudio-XP-<v>-linux-x86_64.tar.gz      (launcher + install.sh)
plus <artifact>.sha256 next to it (the release workflow merges them into SHA256SUMS.txt).

No PyInstaller: the package holds a relocatable Python (python-build-standalone, fetched by uv)
with the locked runtime dependencies (uv.lock) and the app as source, in the same layout as the
repository, so config.REPO_ROOT, the shipped defaults, the bundled DSFTool, the worker processes
(pythonw.exe on Windows) and Api.restart_app all work exactly as they do from a checkout:

    <root>/python/...                         the bundled Python (+ site-packages)
    <root>/app/{forest, config, tools/xptools/<this system>, LICENSE, README.md, CHANGELOG.md}

A .pth file in site-packages puts <root>/app on the module path, and launchers run Python with
-I (isolated: ignores PYTHONPATH and the user's site-packages) and `-m forest.app.main`.

Standard library + uv only. --check smoke-tests the packed tree with the BUNDLED Python
(tools/package/smoke.py) before the installer is made.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
NAME = "ForestStudio-XP"
APP_TITLE = "ForestStudio XP"
PYTHON = "3.13.18"            # python-build-standalone CPython, fetched by uv (pinned: reproducible builds)
APP_ID = "ForestStudio.XP"    # Windows AppUserModelID; the same string as forest/app/main.py

# Top-level repo items shipped in <root>/app (git-tracked files only, so local junk never ships).
APP_ITEMS = ["forest", "config", "LICENSE", "README.md", "CHANGELOG.md"]
# Packages that may be built from source (pure Python, no wheel on PyPI); everything else must be a wheel.
SDIST_OK = {"proxy-tools"}
# Parts of the standard library the app never uses (Tk, tests, IDLE, pip bootstrap).
STDLIB_DROP = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "turtle.py",
               "__phello__"]


def log(msg: str) -> None:
    print(f"[package] {msg}", flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


# ---------------------------------------------------------------- version / target

def read_version() -> str:
    text = (ROOT / "forest" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        sys.exit("forest/__init__.py has no __version__")
    return m.group(1)


def check_tag(version: str) -> None:
    """Building from a tag (GitHub Actions): the tag must be exactly v<version>."""
    if os.environ.get("GITHUB_REF_TYPE") == "tag" or os.environ.get("GITHUB_REF", "").startswith("refs/tags/"):
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if tag != f"v{version}":
            sys.exit(f"Tag {tag!r} doesn't match forest.__version__ {version!r} (expected 'v{version}'). "
                     f"Bump __version__ or fix the tag.")


def target() -> tuple[str, str, str]:
    """(system, arch label, xptools folder)."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        if machine not in ("amd64", "x86_64"):
            sys.exit(f"Windows builds are x64 only (this is {machine})")
        return "windows", "x64", "windows"
    if sys.platform == "darwin":
        return "macos", "arm64" if machine in ("arm64", "aarch64") else "x86_64", "macos"
    if sys.platform.startswith("linux"):
        if machine not in ("x86_64", "amd64"):
            sys.exit(f"Linux builds are x86_64 only (the bundled DSFTool is; this is {machine})")
        return "linux", "x86_64", "linux"
    sys.exit(f"unsupported system {sys.platform}")


def find_uv() -> str:
    exe = shutil.which("uv")
    if exe:
        return exe
    cand = Path(sys.executable).with_name("uv.exe" if sys.platform == "win32" else "uv")
    if cand.exists():
        return str(cand)
    sys.exit("uv not found: install it (https://docs.astral.sh/uv/) or `pip install uv`")


# ---------------------------------------------------------------- bundled Python

def stage_python(uv: str, work: Path, dest: Path) -> Path:
    """Fetch python-build-standalone with uv into a private folder and copy it to dest.
    Returns the bundled python executable (python.exe / bin/python3)."""
    store = work / "uv-python"
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(store)}
    run([uv, "python", "install", f"cpython-{PYTHON}", "--no-bin"], env=env)
    found = subprocess.run([uv, "python", "find", "--managed-python", f"cpython@{PYTHON}"], env=env,
                           check=True, capture_output=True, text=True).stdout.strip()
    exe = Path(found)
    home = exe.parent if sys.platform == "win32" else exe.parent.parent
    if not str(home.resolve()).startswith(str(store.resolve())):
        sys.exit(f"uv found a Python outside the private install folder: {home}")
    log(f"bundling Python {PYTHON} from {home}")
    shutil.copytree(home, dest, symlinks=True)
    for marker in dest.rglob("EXTERNALLY-MANAGED"):
        marker.unlink()   # uv marks its Pythons; this one is ours to install into
    py = dest / ("python.exe" if sys.platform == "win32" else "bin/python3")
    if sys.platform == "win32" and not (dest / "pythonw.exe").exists():
        sys.exit("the bundled Python has no pythonw.exe (forest.worker needs it: no console windows)")
    return py


def site_packages(py: Path) -> Path:
    out = subprocess.run([py, "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return Path(out)


def install_deps(uv: str, py: Path, work: Path) -> None:
    req = work / "requirements.txt"
    run([uv, "export", "--quiet", "--frozen", "--no-dev", "--no-emit-project", "--no-header",
         "--format", "requirements-txt", "-o", req], cwd=ROOT, stdout=subprocess.DEVNULL)
    # Wheels for everything except proxy-tools (a pywebview dependency published only as a tiny
    # pure-Python sdist). Hashes come from uv.lock.
    names = [m.group(1) for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", req.read_text(encoding="utf-8"), re.M)]
    wheels = [n for n in names if n.lower() not in SDIST_OK]
    run([uv, "pip", "install", "--quiet", "--python", py, "--require-hashes", "--no-deps",
         "--link-mode", "copy", "--only-binary", ",".join(wheels), "-r", req])


def trim_python(home: Path) -> None:
    lib = home / "Lib" if sys.platform == "win32" else next((home / "lib").glob("python3.*"))
    for name in STDLIB_DROP:
        p = lib / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    # Tcl/Tk (tkinter is gone) and the headers/static libs nobody links against here.
    for p in [home / "tcl", home / "include", home / "libs", home / "Scripts", home / "share"]:
        if p.exists():
            shutil.rmtree(p)
    if sys.platform == "win32":   # careful: Lib == lib on Windows, so no Unix patterns here
        pats = ["DLLs/_tkinter.pyd", "DLLs/tcl*.dll", "DLLs/tk*.dll"]
    else:
        pats = ["lib/libtcl*", "lib/libtk*", "lib/tcl8*", "lib/tk8*", "lib/itcl*", "lib/thread2*",
                "lib/pkgconfig", "lib/python3.*/lib-dynload/_tkinter*", "lib/python3.*/config-3.*"]
    for pat in pats:
        for p in home.glob(pat):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in list(home.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)
    # The packages' own test suites (scipy, numpy, shapely, pyproj…): never imported by the app,
    # tens of MB, and the deepest paths in the tree (Windows' 260-character limit, see check_paths).
    site = lib / "site-packages"
    for p in sorted(site.rglob("tests"), key=lambda q: len(q.parts)):
        if p.is_dir() and p.exists():
            shutil.rmtree(p)


# Windows refuses paths over 260 characters (MAX_PATH): the installer aborts. The default install
# folder is C:\Users\<name>\AppData\Local\Programs\ForestStudio XP\ (~70 characters with a 20-letter
# user name), so paths inside the package must stay well under 260 - 70.
MAX_REL_PATH = 170


def check_paths(root: Path) -> None:
    longest = max((str(f.relative_to(root)) for f in root.rglob("*")), key=len)
    log(f"longest path in the package: {len(longest)} characters")
    if len(longest) > MAX_REL_PATH:
        sys.exit(f"path too long for Windows installs ({len(longest)} > {MAX_REL_PATH}): {longest}")


def write_pth(site: Path, home: Path) -> None:
    """<root>/app on the module path, relative to site-packages (the tree is relocatable)."""
    rel = os.path.relpath(home.parent / "app", site)
    (site / "foreststudio_xp.pth").write_text(rel + "\n", encoding="utf-8")


# ---------------------------------------------------------------- the app

def tracked_files(paths: list[str]) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=ROOT, check=True,
                         capture_output=True).stdout.decode("utf-8")
    return [Path(p) for p in out.split("\0") if p]


def stage_app(dest: Path, xp_dir: str) -> None:
    files = tracked_files(APP_ITEMS + ["tools/xptools/NOTICE.md", f"tools/xptools/{xp_dir}"])
    for rel in files:
        src = ROOT / rel
        if "__pycache__" in rel.parts or not src.is_file():
            continue
        if rel.parts[0] == "config" and rel.name == "local.toml":
            continue   # never a user's settings
        to = dest / rel
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, to)
    dsftool = dest / "tools" / "xptools" / xp_dir / ("DSFTool.exe" if xp_dir == "windows" else "DSFTool")
    if not dsftool.is_file():
        sys.exit(f"DSFTool missing in the package: {dsftool}")
    if xp_dir != "windows":
        make_executable(dsftool)
    log(f"app: {len(files)} files")


def make_executable(p: Path) -> None:
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def compile_all(py: Path, root: Path) -> None:
    run([py, "-I", "-m", "compileall", "-q", "-j", "0", root / "app", site_packages(py)])


def tree_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6


# ---------------------------------------------------------------- smoke test

def smoke(py: Path, root: Path, work: Path) -> None:
    """Run tools/package/smoke.py with the bundled Python, isolated, on a temp data folder."""
    home = work / "smoke-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV", "CONDA"))}
    env["FORESTSTUDIO_HOME"] = str(home)
    if sys.platform.startswith("linux"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    script = work / "smoke.py"
    shutil.copy2(HERE / "smoke.py", script)   # outside the tree: nothing of the repo on the path
    run([py, "-I", script, root], env=env, cwd=work)


# ---------------------------------------------------------------- per-OS packages

def build_windows(root: Path, version: str, out: Path) -> Path:
    iscc = find_iscc()
    run([iscc, "/Q", f"/DAppVersion={version}", f"/DSourceDir={root}", f"/DOutputDir={out}",
         f"/DRepoDir={ROOT}", f"/DAppId={APP_ID}", HERE / "windows" / "foreststudio-xp.iss"])
    exe = out / f"{NAME}-{version}-windows-x64-setup.exe"
    if not exe.is_file():
        sys.exit(f"Inno Setup didn't make {exe}")
    return exe


def find_iscc() -> str:
    cands = [os.environ.get("ISCC", ""), shutil.which("iscc") or "", shutil.which("ISCC") or ""]
    for base in [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]:
        if base:
            cands.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    for c in cands:
        if c and Path(c).is_file():
            return c
    sys.exit("Inno Setup 6 not found (choco install innosetup, or set ISCC=<path to ISCC.exe>)")


def build_macos(root: Path, version: str, arch: str, out: Path, work: Path) -> Path:
    app = work / "dmg" / f"{APP_TITLE}.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    res = contents / "Resources"
    res.mkdir()
    shutil.move(str(root / "python"), res / "python")
    shutil.move(str(root / "app"), res / "app")
    launcher = contents / "MacOS" / "ForestStudioXP"
    shutil.copy2(HERE / "macos" / "launcher.sh", launcher)
    make_executable(launcher)
    plist = (HERE / "macos" / "Info.plist").read_text(encoding="utf-8").replace("@VERSION@", version)
    (contents / "Info.plist").write_text(plist, encoding="utf-8")
    make_icns(ROOT / "forest" / "app" / "icons" / "foreststudio-xp-1024.png", res / "foreststudio-xp.icns", work)
    adhoc_sign(app)
    (work / "dmg" / "Applications").symlink_to("/Applications")
    dmg = out / f"{NAME}-{version}-macos-{arch}.dmg"
    if dmg.exists():
        dmg.unlink()
    run(["hdiutil", "create", "-volname", f"{APP_TITLE} {version}", "-srcfolder", work / "dmg",
         "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9", "-ov", dmg])
    return dmg


def make_icns(png: Path, icns: Path, work: Path) -> None:
    iconset = work / "foreststudio-xp.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            run(["sips", "-z", px, px, png, "--out", iconset / name], stdout=subprocess.DEVNULL)
    run(["iconutil", "-c", "icns", iconset, "-o", icns])


def adhoc_sign(app: Path) -> None:
    """Apple Silicon runs only signed arm64 code: give every Mach-O file without a valid signature
    an ad-hoc one ("-": free, no certificate; this is not Developer ID signing)."""
    magics = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
    n = 0
    for f in app.rglob("*"):
        if not f.is_file() or f.is_symlink():
            continue
        with open(f, "rb") as fh:
            if fh.read(4) not in magics:
                continue
        ok = subprocess.run(["codesign", "-v", f], capture_output=True).returncode == 0
        if not ok:
            subprocess.run(["codesign", "-f", "-s", "-", f], check=True, capture_output=True)
            n += 1
    log(f"ad-hoc signed {n} Mach-O file(s)")


def build_linux(root: Path, version: str, out: Path, work: Path) -> Path:
    top = f"{NAME}-{version}-linux-x86_64"
    for name in ("foreststudio-xp", "install.sh"):
        shutil.copy2(HERE / "linux" / name, root / name)
        make_executable(root / name)
    for p in (root / "python" / "bin").iterdir():
        if p.is_file() and not p.is_symlink():
            make_executable(p)
    tgz = out / f"{top}.tar.gz"
    log(f"writing {tgz.name}")
    with tarfile.open(tgz, "w:gz", compresslevel=9) as tar:
        tar.add(root, arcname=top)   # modes (+x on DSFTool, python, launchers) and symlinks kept
    return tgz


def write_sha256(artifact: Path) -> Path:
    h = hashlib.sha256()
    with open(artifact, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    p = artifact.with_name(artifact.name + ".sha256")
    # LF even on Windows: sha256sum -c (and the merged SHA256SUMS.txt) reject CRLF lines.
    p.write_text(f"{h.hexdigest()}  {artifact.name}\n", encoding="utf-8", newline="\n")
    return p


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="dist", help="output folder (default: dist)")
    ap.add_argument("--check", action="store_true", help="smoke-test the packed tree with the bundled Python")
    ap.add_argument("--keep-stage", action="store_true", help="keep the packed tree (prints where)")
    args = ap.parse_args()

    version = read_version()
    check_tag(version)
    system, arch, xp_dir = target()
    out = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    uv = find_uv()
    log(f"ForestStudio XP {version} for {system}-{arch}")

    work = Path(tempfile.mkdtemp(prefix="fsxp-package-"))
    try:
        root = work / "stage"
        py = stage_python(uv, work, root / "python")
        install_deps(uv, py, work)
        trim_python(root / "python")
        write_pth(site_packages(py), root / "python")
        stage_app(root / "app", xp_dir)
        compile_all(py, root)
        log(f"packed tree: {tree_mb(root):.0f} MB")
        check_paths(root)
        if args.check:
            smoke(py, root, work)
        if system == "windows":
            art = build_windows(root, version, out)
        elif system == "macos":
            art = build_macos(root, version, arch, out, work)
        else:
            art = build_linux(root, version, out, work)
        sums = write_sha256(art)
        log(f"done: {art} ({art.stat().st_size / 1e6:.0f} MB), {sums.name}")
    finally:
        if args.keep_stage:
            log(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
