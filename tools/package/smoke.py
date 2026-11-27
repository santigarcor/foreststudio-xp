"""Smoke test of a packed ForestStudio XP tree, run by build.py --check with the BUNDLED Python:

    <root>/python/python -I smoke.py <root>      (FORESTSTUDIO_HOME = a temp folder)

Proves the package works on its own: imports (incl. the webview backend of this system), the
shipped defaults and DSFTool are found through config.REPO_ROOT, the CLI runs, and a worker
process starts and exits (spawn; pythonw.exe on Windows). Never touches real data or X-Plane.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""), flush=True)
    if not ok:
        FAILS.append(name)


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    home = os.environ.get("FORESTSTUDIO_HOME")
    assert home and Path(home).is_dir(), "FORESTSTUDIO_HOME must be a temp folder"
    print(f"smoke test of {root} on Python {sys.version.split()[0]} ({sys.executable})", flush=True)
    check("bundled python", Path(sys.executable).resolve().is_relative_to(root), sys.executable)

    import osmium  # noqa: F401
    import rasterio  # noqa: F401
    import shapely  # noqa: F401
    import webview  # noqa: F401

    import forest
    import forest.app.main  # noqa: F401
    import forest.build  # noqa: F401
    from forest import config, proc
    check("imports", True, f"forest {forest.__version__}, rasterio {rasterio.__version__} "
                           f"(GDAL {rasterio.__gdal_version__}), shapely {shapely.__version__}")
    check("app from the package", Path(forest.__file__).resolve().is_relative_to(root / "app"), forest.__file__)
    check("data folder is the temp one", config.DATA_ROOT.resolve() == Path(home).resolve(), str(config.DATA_ROOT))

    # the webview backend this system uses
    try:
        if sys.platform == "win32":
            import clr  # noqa: F401  (pythonnet → WinForms + WebView2)
            import webview.platforms.winforms  # noqa: F401
        elif sys.platform == "darwin":
            import webview.platforms.cocoa  # noqa: F401
        else:
            import webview.platforms.qt  # noqa: F401
            from PyQt6 import QtWebEngineWidgets  # noqa: F401
        check("webview backend", True)
    except Exception as exc:  # noqa: BLE001
        check("webview backend", False, f"{type(exc).__name__}: {exc}")

    # shipped defaults
    want = ["regions/colombia.toml", "profiles/low.toml", "profiles/medium.toml", "profiles/high.toml",
            "rules/colombia.toml", "rules/_global.toml"]
    missing = [w for w in want if not (config.DEFAULTS_DIR / w).is_file()]
    check("shipped defaults", not missing, f"missing {missing}" if missing else str(config.DEFAULTS_DIR))

    # bundled DSFTool runs (hidden)
    exe = config.BUNDLED_DSFTOOL
    if exe.is_file():
        config._ensure_executable(exe)
        r = proc.run([str(exe), "--version"], timeout=60)
        text = ((r.stdout or "") + (r.stderr or "")).strip()
        check("DSFTool runs", r.returncode == 0 and "DSFTool" in text, f"rc {r.returncode}, {text[:60]!r}")
    else:
        check("DSFTool present", False, str(exe))

    # the CLI
    r = proc.run([sys.executable, "-I", "-m", "forest.cli", "--help"], timeout=120)
    check("forest CLI", r.returncode == 0 and "build" in r.stdout, f"rc {r.returncode} {r.stderr.strip()[-200:]}")

    # a worker process: spawn (+ pythonw.exe on Windows) with a trivial task
    from forest.worker import _worker_python, run
    msgs = list(run("forest.events:log", line="worker ok"))
    ok = msgs and msgs[-1].get("event") == "exit" and msgs[-1].get("rc") == 0 and not msgs[-1].get("crashed")
    ok = ok and {"event": "log", "text": "worker ok"} in msgs
    check("worker process", bool(ok), f"via {_worker_python() or sys.executable}; {msgs[-2:]}")
    if sys.platform == "win32":
        check("worker on pythonw.exe", (_worker_python() or "").lower().endswith("pythonw.exe"))

    if FAILS:
        print(f"smoke test FAILED: {', '.join(FAILS)}", flush=True)
        sys.exit(1)
    print("smoke test passed", flush=True)


if __name__ == "__main__":
    main()
