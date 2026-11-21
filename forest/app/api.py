"""Python side of the app, exposed to the page as `window.pywebview.api`."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from forest import config
from forest.app import settings as st
from forest.assets.catalog import load_catalog
from forest.build import OVERLAY_CUT, OVERLAY_OSM, OVERLAY_PALETTE, OVERLAY_SHADE
from forest.config import REPO_ROOT  # the code folder (subprocess cwd); data is config.DATA_ROOT
from forest.tiles import CACHE, Tile

# Map extent offered in the tile picker (Colombia incl. San Andrés, plus margin).
GRID_LAT = (-5, 13)
GRID_LON = (-82, -66)


class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.log: deque[str] = deque(maxlen=4000)
        self.seq = 0
        self.running = False
        self.cancel = False
        self.current: str | None = None
        self.done: list[str] = []
        self.failed: list[str] = []
        self.queue: list[str] = []
        self.procs: set[subprocess.Popen] = set()   # running subprocesses (for cancel)
        self.active: set[str] = set()               # tiles currently building
        self.workers = 1
        self.record = None                          # forest.jobs.JobRecord (saved job)

    def add(self, line: str) -> None:
        with self.lock:
            self.seq += 1
            self.log.append(f"{self.seq}\t{line.rstrip()}")
        if self.record is not None:
            self.record.log(f"{time.strftime('%H:%M:%S')}  {line.rstrip()}")


class Api:
    def __init__(self) -> None:
        from forest import jobs, migrate

        self.migration = migrate.run()   # one-time: old in-repo cache/out/settings -> ~/.foreststudio
        self.job = Job()
        self.record = None          # forest.jobs.JobRecord of the running job
        jobs.mark_interrupted()     # jobs left "running" by a closed app
        self.reload()

    def reload(self) -> None:
        """(Re)read ~/.foreststudio/config/local.toml and the forest catalog (after Settings change)."""
        from forest.assets.catalog import ensure_catalog

        self.cfg = config.load()
        cat: list = []
        try:
            if ensure_catalog():
                cat = load_catalog()["forests"]
        except (OSError, ValueError):
            cat = []
        self.catalog_ok = bool(cat)

        def tier(m):  # million triangles per km² at full density
            return None if m is None else "high" if m >= 7 else "medium" if m >= 3 else "low"

        self.evergreen = sorted(
            f["virtual"] for f in cat
            if not f["seasons"] and "/trees/" not in f["virtual"] and "planters" not in f["virtual"]
            and f["mesh_count"] >= 5
        )
        # Plantations may also use evergreen single-species tree sets (date palm, banana, pine…).
        self.evergreen_all = sorted(
            f["virtual"] for f in cat
            if not f["seasons"] and "planters" not in f["virtual"] and f["max_trees_per_km2"])
        self.asset_info = {f["virtual"]: {"trees_km2": f["max_trees_per_km2"], "species": f["species"],
                                          "tris_tree": f.get("avg_tree_tris"),
                                          "mtris_km2": f.get("tris_per_km2_m"),
                                          "cost": tier(f.get("tris_per_km2_m"))}
                           for f in cat}

    # ---------- app settings (X-Plane location; DSFTool is bundled, no setting) ----------
    def get_app_settings(self) -> dict:
        x = config.check_xplane(self.cfg.xplane_root if str(self.cfg.xplane_root) not in ("", ".") else None)
        det_x = config.detect_xplane()
        return {
            "xplane_root": "" if str(self.cfg.xplane_root) in ("", ".") else str(self.cfg.xplane_root),
            "saved": self.cfg.from_file, "xplane_check": x,
            "dsftool_check": config.check_dsftool(self.cfg.dsftool),
            "detected_xplane": str(det_x) if det_x else "",
            "catalog_ok": self.catalog_ok,
        }

    def check_paths(self, xplane_root: str) -> dict:
        return {"xplane_check": config.check_xplane(xplane_root.strip() or None)}

    def save_app_settings(self, xplane_root: str) -> dict:
        from forest.assets.catalog import ensure_catalog

        if self.job.running:
            return {"ok": False, "error": "a build is running; wait for it to finish"}
        x = config.check_xplane(xplane_root.strip() or None)
        if not x["ok"]:
            return {"ok": False, "error": "Not an X-Plane 12 folder: missing " + ", ".join(x["problems"])}
        changed = Path(xplane_root.strip()) != self.cfg.xplane_root
        config.save(xplane_root.strip(), self.cfg.dsftool)  # keeps a hand-set custom DSFTool, if any
        self.cfg = config.load()
        if changed or not self.catalog_ok:
            ensure_catalog(force=True)  # forest library of the chosen X-Plane
        self.reload()
        return {"ok": True, **self.get_app_settings()}

    def pick_path(self, kind: str = "folder") -> str | None:
        """Native folder/file picker (desktop window only; None in the dev browser)."""
        try:
            import webview
            win = webview.windows[0]
            dlg = webview.FileDialog.FOLDER if kind == "folder" else webview.FileDialog.OPEN
            res = win.create_file_dialog(dlg)
            return res[0] if res else None
        except Exception:  # noqa: BLE001 - no window (dev server) or dialog unsupported
            return None

    def credits(self) -> dict:
        from forest import credits
        return credits.as_dict()

    # ---------- data folder (where downloads, builds and settings live) ----------
    def data_folder_info(self) -> dict:
        from forest import datafolder
        return datafolder.info()

    def check_data_folder(self, folder: str) -> dict:
        from forest import datafolder
        return datafolder.check(folder)

    def change_data_folder(self, folder: str) -> dict:
        from forest import datafolder
        if self.job.running:
            return {"ok": False, "error": "A build is running; wait for it to finish."}
        try:
            return datafolder.change(folder)
        except OSError as exc:
            return {"ok": False, "error": f"Moving failed: {exc}. Nothing was lost; files already moved "
                                          f"are in the new folder, the rest are still in the old one."}

    def restart_app(self) -> dict:
        """Start a fresh window (it reads the new data folder) and close this one."""
        try:
            import webview
            win = webview.windows[0]
        except Exception:  # noqa: BLE001 - dev server: no window to restart
            return {"ok": False, "error": "Restart the app to use the new folder."}
        from forest import proc as hidden

        hidden.popen([sys.executable, "-m", "forest.app.main"], cwd=REPO_ROOT)  # pythonw: no console
        win.destroy()
        return {"ok": True}

    def setup_checks(self) -> dict:
        """Status line like OrthoStudio's: how many required checks pass."""
        from forest.fetch.osm_pbf import DEFAULT_PBF
        items = {
            "X-Plane 12 folder": config.check_xplane(self.cfg.xplane_root)["ok"],
            "DSFTool": config.check_dsftool(self.cfg.dsftool)["ok"],
            "forest catalog": self.catalog_ok,
            "OSM country extract": DEFAULT_PBF.exists(),
            "ecoregions map": (config.CACHE_ROOT / "ecoregions" / "colombia.geojson").exists()
                              or (config.CACHE_ROOT / "ecoregions" / "raw" / "Ecoregions2017.zip").exists(),
        }
        return {"items": items, "ok": sum(items.values()), "total": len(items)}

    # ---------- state ----------
    def get_state(self) -> dict:
        return {
            "profiles": st.list_profiles(),
            "regions": st.list_regions(),
            "schema": {k: {"type": v[0].__name__, "min": v[1], "max": v[2], "step": v[3],
                           "group": v[4], "label": v[5], "help": v[6]}
                       for k, v in st.PROFILE_SCHEMA.items()},
            "density_levels_help": st.DENSITY_LEVELS_HELP,
            "fps_impact": st.FPS_IMPACT,
            "ecoregions": self._ecoregions(),
            "overlay": {"palette": OVERLAY_PALETTE, "shade": OVERLAY_SHADE, "cut": OVERLAY_CUT, "osm": OVERLAY_OSM},
            "evergreen": self.evergreen,
            "evergreen_all": self.evergreen_all,
            "asset_info": self.asset_info,
            "grid": {"lat": GRID_LAT, "lon": GRID_LON},
            "xplane_root": str(self.cfg.xplane_root),
            "dsftool_ok": self.cfg.dsftool.exists(),
            "setup": self.setup_checks(),
            "osm_pbf": any((config.CACHE_ROOT / "osm").glob("*.osm.pbf")),
        }

    @staticmethod
    def _ecoregions() -> list[str]:
        try:
            from forest.fetch.ecoregions import ecoregion_names
            return ecoregion_names()
        except (OSError, FileNotFoundError, ValueError):
            return []

    def tile_status(self, region: str = "colombia", pack: str = "", profile: str = "") -> dict:
        """Per tile: land / ortho / data cached / built / installed (= built and the region's
        pack is linked into X-Plane), the last build's stats and, for built tiles, why they're
        outdated compared with `profile` + the current forest types + the downloaded data."""
        from forest.build import Settings, outdated_reasons, settings_fingerprint
        from forest.pack import pack_status
        from forest.paths import dsf_path, studio_path

        try:
            fp = settings_fingerprint(Settings.load(profile, region)) if profile else None
        except (OSError, ValueError):
            fp = None

        xp = self.cfg.xplane_root
        cs = xp / "Custom Scenery"
        gs = xp / "Global Scenery" / "X-Plane 12 Global Scenery" / "Earth nav data"
        ortho = {q.name.removeprefix("zOrthoStudio_") for q in cs.glob("zOrthoStudio_*")}
        linked = pack_status(region)["installed"]
        out: dict[str, dict] = {}
        for lat in range(GRID_LAT[0], GRID_LAT[1]):
            for lon in range(GRID_LON[0], GRID_LON[1]):
                t = Tile(lat, lon)
                c = CACHE / t.name
                built = dsf_path(region, t).exists()
                stats = studio_path(region, t, ".stats.json")
                s = {
                    "land": (gs / t.folder / f"{t.name}.dsf").exists(),
                    "cached": (c / "worldcover.tif").exists() and (c / "osm.json").exists(),
                    "built": built,
                    "installed": built and linked,
                    "ortho": t.name in ortho,
                }
                j = None
                if stats.exists():
                    try:
                        j = json.loads(stats.read_text(encoding="utf-8"))
                        s["stats"] = {k: j.get(k) for k in
                                      ("estimated_trees", "forest_km2", "polygons", "budget_scale", "profile", "seconds")}
                    except (OSError, ValueError):
                        j = None
                if built and fp:
                    s["outdated"] = outdated_reasons(j, t, profile, region, fp)
                out[t.name] = s
        return out

    # ---------- X-Plane pack (one linked folder) ----------
    def pack_status(self, region: str = "colombia") -> dict:
        from forest.pack import pack_status
        return pack_status(region)

    def install_pack(self, region: str = "colombia") -> dict:
        from forest.pack import link_pack
        if self.job.running:
            return {"ok": False, "message": "a build is running; wait for it to finish"}
        try:
            return link_pack(region)
        except (OSError, RuntimeError) as exc:
            return {"ok": False, "message": str(exc)}

    def uninstall_pack(self, region: str = "colombia") -> dict:
        from forest.pack import unlink_pack
        try:
            return unlink_pack(region)
        except (OSError, RuntimeError) as exc:
            return {"ok": False, "message": str(exc)}

    # ---------- settings ----------
    def get_profile(self, name: str) -> dict:
        return st.read_toml(config.profile_path(name))

    def save_profile(self, name: str, data: dict) -> dict:
        try:
            st.write_profile(name, data)
            return {"ok": True}
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def get_rules(self, region: str) -> dict:
        return st.read_toml(config.rules_path(region))

    def save_rules(self, region: str, data: dict) -> dict:
        try:
            st.write_rules(region, data, set(self.evergreen_all))
            return {"ok": True}
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    # ---------- results ----------
    def get_overlay(self, tile: str, region: str = "colombia") -> dict | None:
        t = Tile.parse(tile)
        from forest.paths import studio_path
        png = studio_path(region, t, ".overlay.png")
        if not png.exists():
            return None
        return {"bounds": [[t.lat, t.lon], [t.lat + 1, t.lon + 1]],
                "url": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()}

    def get_stats(self, tile: str, region: str = "colombia") -> dict | None:
        t = Tile.parse(tile)
        from forest.paths import studio_path
        p = studio_path(region, t, ".stats.json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # ---------- deleting tiles ----------
    def _tile_files(self, t: Tile, region: str) -> dict[str, list[Path]]:
        from forest.paths import dsf_path, studio_files

        dsf = dsf_path(region, t)
        cache = CACHE / t.name
        return {
            "output": ([dsf] if dsf.exists() else []) + studio_files(region, t),
            "cache": [cache] if cache.exists() and any(cache.iterdir()) else [],
        }

    def delete_preview(self, tiles: list[str], region: str = "colombia", pack: str = "") -> dict:
        """What deleting these tiles would remove, per kind, with sizes (nothing is deleted)."""
        from forest import history

        try:
            ts = [Tile.parse(x) for x in tiles]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        res = {k: {"tiles": 0, "mb": 0.0} for k in ("output", "cache")}
        for t in ts:
            for kind, paths in self._tile_files(t, region).items():
                if paths:
                    res[kind]["tiles"] += 1
                    res[kind]["mb"] += sum(history.dir_mb(q) for q in paths)
        for v in res.values():
            v["mb"] = round(v["mb"], 1)
        return {"ok": True, "tiles": len(ts), "pack_linked": self.pack_status(region)["installed"], **res}

    def delete_tiles(self, tiles: list[str], kinds: list[str], region: str = "colombia", pack: str = "") -> dict:
        """Delete built tiles ('output': the .dsf in the pack + the app's files) and/or their
        downloaded data ('cache'). Only touches the data folder's out/ and cache/tiles/;
        the X-Plane side is just a link to out/, so a deleted tile disappears there too."""
        import shutil

        from forest.paths import dsf_path

        if self.job.running:
            return {"ok": False, "error": "a build is running; wait for it to finish"}
        kinds = [k for k in kinds if k in ("output", "cache")]
        if not kinds:
            return {"ok": False, "error": "choose what to delete"}
        try:
            ts = [Tile.parse(x) for x in tiles]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        allowed = [config.OUT_ROOT.resolve(), CACHE.resolve()]
        removed = {k: 0 for k in kinds}
        for t in ts:
            files = self._tile_files(t, region)
            for kind in kinds:
                for q in files[kind]:
                    where = q.parent.resolve() / q.name
                    if not any(where.is_relative_to(a_) for a_ in allowed):
                        return {"ok": False, "error": f"refusing to delete outside our folders: {q}"}
                    if q.is_symlink() or q.is_file():
                        q.unlink(missing_ok=True)
                    elif q.is_dir():
                        shutil.rmtree(q)
                if files[kind]:
                    removed[kind] += 1
            d = dsf_path(region, t).parent
            try:
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass
        return {"ok": True, "removed": removed}

    # ---------- estimates ----------
    def estimate(self, tiles: list[str], region: str = "colombia", install: bool = True,
                 pack: str = "", workers: int = 0) -> dict:
        """Time and disk needed to build these tiles, from measured history (or defaults)."""
        import shutil

        from forest import history
        from forest.fetch.osm_pbf import is_current

        try:
            ts = [Tile.parse(x) for x in tiles]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        avg = history.averages()
        need_rasters = [t for t in ts if not (CACHE / t.name / "worldcover.tif").exists()]
        need_osm = [t for t in ts if not is_current(t)]
        from forest.paths import dsf_path
        new_out = [t for t in ts if not dsf_path(region, t).exists()]
        import math

        from forest.resources import DOWNLOAD_WORKERS
        info = self.parallel_info()
        w = int(workers) if workers and int(workers) > 0 else info["workers"]
        w = max(1, min(w, info["max"], max(1, len(ts))))
        seconds = (math.ceil(len(need_rasters) / DOWNLOAD_WORKERS) * avg["rasters_s"]
                   + (avg["osm_pass_s"] if need_osm else 0)
                   # parallel builds share disk and memory bandwidth: ~85 % efficiency each extra worker
                   + math.ceil(len(ts) / w) * avg["build_s"] * (1 + 0.15 * (w - 1)))
        cache_mb = len(need_rasters) * avg["raster_mb"] + len(need_osm) * avg["osm_mb"]
        out_mb = len(new_out) * (avg["dsf_mb"] + avg["out_extra_mb"])
        inst_mb = 0  # the pack is linked into X-Plane as one folder: no copies

        def free_gb(p: Path) -> float | None:
            try:
                while not p.exists():
                    p = p.parent
                return round(shutil.disk_usage(p).free / 1e9, 1)
            except OSError:
                return None

        return {
            "ok": True, "tiles": len(ts), "seconds": round(seconds), "workers": w,
            "download_tiles": len(need_rasters), "osm_pass": bool(need_osm),
            "cache_mb": round(cache_mb), "out_mb": round(out_mb), "install_mb": round(inst_mb),
            "free_gb_project": free_gb(config.DATA_ROOT), "free_gb_xplane": free_gb(self.cfg.xplane_root),
            "measured": avg["samples"] > 0,
        }

    # ---------- builds ----------
    def parallel_info(self) -> dict:
        """Recommended number of simultaneous builds for this machine right now."""
        from forest.resources import auto_workers
        return auto_workers()

    def start_build(self, tiles: list[str], profile: str, region: str, install: bool, pack: str,
                    workers: int = 0) -> dict:
        if self.job.running:
            return {"ok": False, "error": "a build is already running"}
        try:
            names = [Tile.parse(x).name for x in tiles]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not names:
            return {"ok": False, "error": "select at least one tile"}
        info = self.parallel_info()
        n = int(workers) if workers and int(workers) > 0 else info["workers"]
        n = max(1, min(n, info["max"], len(names)))
        from forest.jobs import JobRecord

        j = self.job
        j.running, j.cancel, j.done, j.failed, j.queue = True, False, [], [], list(names)
        j.current, j.workers = None, n
        j.record = JobRecord.new(names, profile, region, n)
        for t in names:
            j.record.step(t, "data", "running")
        threading.Thread(target=self._run, args=(names, profile, region, install, pack, n), daemon=True).start()
        return {"ok": True, "workers": n, "job": j.record.id}

    # ---------- data (downloads without building) ----------
    def data_status(self, region: str = "colombia") -> dict:
        """Country-wide files and every tile's downloaded data (no network)."""
        from forest.build import DATA_FILES
        from forest.fetch import sources
        from forest.fetch.ecoregions import clipped_path
        from forest.fetch.osm_pbf import is_current
        from forest.paths import dsf_path

        osm = sources.status(sources.osm_file(region), sources.OSM_URLS[region])
        eco = sources.status(sources.ecoregions_zip(), sources.ECOREGIONS_URL)
        eco["ready"] = clipped_path().exists()
        osm_time = osm["downloaded"] if osm["exists"] else None
        tiles = []
        for d in sorted(CACHE.glob("*")) if CACHE.exists() else []:
            try:
                t = Tile.parse(d.name)
            except ValueError:
                continue
            files = {n: (d / n).exists() for n in DATA_FILES}
            o = d / "osm.json"
            o_time = int(o.stat().st_mtime) if o.exists() else None
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            tiles.append({
                "tile": t.name, "mb": round(size / 1e6, 1),
                "satellite": files["worldcover.tif"] and files["treecover2000.tif"] and files["lossyear.tif"],
                "osm": files["osm.json"], "osm_date": o_time,
                "osm_stale": bool(o_time and osm_time and o_time < osm_time) or (files["osm.json"] and not is_current(t)),
                "elevation": (d / "elevation_int16.npy").exists(),
                "built": dsf_path(region, t).exists(),
            })
        return {"osm": osm, "ecoregions": eco, "tiles": tiles, "busy": self.job.running}

    def check_osm_update(self, region: str = "colombia") -> dict:
        from forest.fetch import sources
        try:
            return {"ok": True, **sources.check_update(sources.osm_file(region), sources.OSM_URLS[region])}
        except OSError as exc:
            return {"ok": False, "error": f"Couldn't reach Geofabrik: {exc}"}

    def start_data_job(self, kind: str, tiles: list[str] | None = None, region: str = "colombia",
                       force: bool = False) -> dict:
        """kind: 'tiles' (download data for these tiles), 'osm' (download/update the country
        file and re-extract every tile that has OSM data), 'ecoregions'."""
        from forest.jobs import JobRecord

        if self.job.running:
            return {"ok": False, "error": "A build or download is already running."}
        if kind == "tiles":
            try:
                names = sorted({Tile.parse(x).name for x in tiles or []})
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if not names:
                return {"ok": False, "error": "Select some tiles on the map first."}
            args, rkind, label = ["fetch", "--tiles", ",".join(names), "--region", region], "data", "downloading data"
        elif kind == "osm":
            names = [d.name for d in sorted(CACHE.glob("*")) if (d / "osm.json").exists()] if CACHE.exists() else []
            args = ["data", "osm", "--region", region, "--update"] + (["--force"] if force else [])
            rkind, label = "osm_update", "updating OSM data"
        elif kind == "ecoregions":
            names, args, rkind, label = [], ["data", "ecoregions", "--update"], "data", "downloading ecoregions"
        else:
            return {"ok": False, "error": f"unknown data job '{kind}'"}
        j = self.job
        j.running, j.cancel, j.done, j.failed, j.queue = True, False, [], [], list(names)
        j.current, j.workers = label, 1
        j.record = JobRecord.new(names, "", region, 1, kind=rkind)
        for t in names:
            j.record.step(t, "data", "running")
        threading.Thread(target=self._run_data, args=(args, label), daemon=True).start()
        return {"ok": True, "job": j.record.id, "tiles": len(names)}

    def _run_data(self, args: list[str], label: str) -> None:
        j = self.job
        t0 = time.time()
        rc = 1
        try:
            j.add(f"=== {label} ===")
            rc = self._step(args)
            with j.lock:
                j.done = list(j.queue) if rc == 0 else []
                j.failed = [] if rc == 0 else list(j.queue)
            j.add("cancelled" if j.cancel else ("done" if rc == 0 else f"failed (exit {rc})"))
        finally:
            with j.lock:
                j.current, j.running, j.active = None, False, set()
            j.add(f"finished in {time.time() - t0:.0f} s")
            if j.record is not None:
                j.record.finish(cancelled=j.cancel, rc=rc)
                j.record = None

    def cancel_build(self) -> dict:
        self.job.cancel = True
        with self.job.lock:
            procs = list(self.job.procs)
        for pr in procs:
            if pr.poll() is None:
                pr.terminate()
        return {"ok": True}

    def job_status(self, after: int = 0) -> dict:
        j = self.job
        with j.lock:
            lines = [ln for ln in j.log if int(ln.split("\t", 1)[0]) > after]
            current = ", ".join(sorted(j.active)) if j.active else j.current
        return {"running": j.running, "current": current, "done": j.done, "failed": j.failed,
                "queue": j.queue, "lines": lines, "workers": j.workers,
                "job": j.record.id if j.record else None}

    def _step(self, args: list[str], tag: str = "", tail: list | None = None) -> int:
        from forest import proc as hidden

        cmd = [hidden.python_exe(), "-W", "ignore", "-u", "-m", "forest.cli", *args]
        prefix = f"[{tag}] " if tag else ""
        self.job.add(prefix + "$ forest " + " ".join(args))
        proc = hidden.popen(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
        with self.job.lock:
            self.job.procs.add(proc)
        try:
            assert proc.stdout
            for line in proc.stdout:
                if line.startswith(("Scanning for", "Got dimension", "Got entire", "Converting ", "Converted ")):
                    continue
                self.job.add(prefix + line)
                if self.job.record is not None:
                    self.job.record.track(tag, line)
                if tail is not None:
                    tail.append(line.rstrip())
                    del tail[:-40]
            return proc.wait()
        finally:
            with self.job.lock:
                self.job.procs.discard(proc)

    def _build_one(self, name, profile, region, install, pack) -> None:
        j = self.job
        if j.cancel:
            return
        with j.lock:
            j.active.add(name)
            j.queue = [n for n in j.queue if n != name]
        j.add(f"=== {name}: building ({profile}, {region}) ===")
        args = ["build", "--tile", name, "--profile", profile, "--region", region, "--compile"]
        tail: list[str] = []
        rc = self._step(args, tag=name, tail=tail)
        if j.record is not None:
            j.record.tile_finished(name, rc, tail, self._tile_result(name, region) if rc == 0 else None)
        with j.lock:
            j.active.discard(name)
            if not j.cancel or rc == 0:
                (j.done if rc == 0 else j.failed).append(name)
        j.add(f"=== {name}: {'ok' if rc == 0 else ('cancelled' if j.cancel else f'failed (exit {rc})')} ===")

    def _tile_result(self, name: str, region: str) -> dict:
        from forest import history
        from forest.paths import dsf_path
        t = Tile.parse(name)
        st = self.get_stats(name, region) or {}
        out = {k: st.get(k) for k in ("forest_km2", "estimated_trees", "polygons", "budget_scale", "seconds")}
        out["dsf_mb"] = round(history.dir_mb(dsf_path(region, t)), 2)
        return out

    def _run(self, names, profile, region, install, pack, workers: int = 1) -> None:
        from concurrent.futures import ThreadPoolExecutor

        j = self.job
        t_all = time.time()
        try:
            # 1) Data for all tiles: satellite rasters (4 downloads at a time), OSM in ONE pass.
            j.current = "downloading data"
            j.add(f"=== data for {len(names)} tile(s) ===")
            if self._step(["fetch", "--tiles", ",".join(names)]) != 0:
                j.add("some data is missing; those tiles will fail, the others are built")
            if j.cancel:
                j.add("cancelled")
                return
            # 2) Build tiles, `workers` at a time; a failure doesn't stop the others.
            j.current = None
            j.add(f"=== building {len(names)} tile(s), {workers} at a time ===")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for name in names:
                    pool.submit(self._build_one, name, profile, region, install, pack)
            if j.cancel:
                j.add("cancelled — finished tiles are kept")
        finally:
            with j.lock:
                j.current, j.running, j.active = None, False, set()
            j.add(f"finished in {time.time() - t_all:.0f} s")
            if j.record is not None:
                j.record.finish(cancelled=j.cancel)
                j.record = None

    # ---------- disk space ----------
    def disk_usage(self) -> dict:
        from forest import diskusage
        return diskusage.usage()

    def disk_clean(self, kind: str) -> dict:
        from forest import diskusage
        if self.job.running:
            return {"ok": False, "error": "a build is running; wait for it to finish"}
        if kind == "jobs":
            return self.clear_jobs()
        return diskusage.clean(kind)

    # ---------- saved jobs ("Works") ----------
    def list_jobs(self) -> list[dict]:
        from forest import jobs
        return jobs.list_jobs()

    def get_job(self, jid: str) -> dict | None:
        from forest.jobs import JobRecord
        rec = self.job.record if self.job.record is not None and self.job.record.id == jid else JobRecord.load(jid)
        return rec.full() if rec else None

    def get_job_log(self, jid: str, tail: int = 5000) -> list[str]:
        from forest import jobs
        return jobs.read_log(jid, tail)

    def delete_job(self, jid: str) -> dict:
        from forest import jobs
        if self.job.record is not None and self.job.record.id == jid:
            return {"ok": False, "error": "this job is still running"}
        return {"ok": jobs.delete(jid)}

    def clear_jobs(self) -> dict:
        """Delete all finished jobs' records and logs."""
        from forest import jobs
        n = 0
        for jsum in jobs.list_jobs(10_000):
            if jsum["status"] != "running" and jobs.delete(jsum["id"]):
                n += 1
        return {"ok": True, "deleted": n}
