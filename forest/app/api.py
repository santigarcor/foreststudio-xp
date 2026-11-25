"""Python side of the app, exposed to the page as `window.pywebview.api`."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from collections import deque
from pathlib import Path

from forest import __version__, config, fsutil
from forest.app import settings as st
from forest.assets.catalog import load_catalog
from forest.build import OVERLAY_CUT, OVERLAY_OSM, OVERLAY_PALETTE, OVERLAY_SCRUB, OVERLAY_SHADE, OVERLAY_TOWN
from forest.config import REPO_ROOT  # the code folder (subprocess cwd); data is config.DATA_ROOT
from forest.tiles import CACHE, Tile


def _is_tile_in(name: str, region) -> bool:
    try:
        return region.contains(Tile.parse(name))
    except ValueError:
        return False


_DL_DEFAULT = {"OSM": "the OpenStreetMap file", "ecoregions": "the biome map (Ecoregions 2017)",
               "climate": "the climate map (Köppen-Geiger)"}


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
        self.procs: set = set()                     # running forest.worker.Worker (for cancel)
        self.active: set[str] = set()               # tiles currently building
        self.workers = 1
        self.record = None                          # forest.jobs.JobRecord (saved job)
        self.region = ""                            # data jobs: the region whose files download
        self.progress: dict | None = None           # current download: {"what", "mb", "pct"}
        self.phase: str | None = None               # what the data steps are doing, in plain words
        # per data group (country files read side by side; "" = the only one): its tiles, the file
        # being downloaded ("the OpenStreetMap file …"), satellite maps ready / tiles, its phase
        self.groups: dict[str, dict] = {}

    def group(self, key: str) -> dict:
        return self.groups.setdefault(key, {"tiles": None, "dl_name": "", "sat": [0, 0], "phase": None})

    def claim(self) -> bool:
        """Mark a job as running unless one already is (one check-and-set under the lock:
        pywebview runs each JS call on its own thread, so two quick clicks could both start one)."""
        with self.lock:
            if self.running:
                return False
            self.running = True
            return True

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
        self.migration += migrate.run_packs()   # one-time: per-region packs -> the one pack
        self.job = Job()
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
        # The same lists with seasonal types too, for regions that allow them (evergreen_only = false).
        self.forests = sorted(
            f["virtual"] for f in cat
            if "/trees/" not in f["virtual"] and "planters" not in f["virtual"] and f["mesh_count"] >= 5)
        self.forests_all = sorted(
            f["virtual"] for f in cat if "planters" not in f["virtual"] and f["max_trees_per_km2"])
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
        # pythonw: no console. The new window waits for this one's app lock (forest/app/single.py).
        # The same interpreter flags as this process (-I in an installed build: tools/package).
        import subprocess

        from forest import proc as hidden
        from forest.app.main import RESTART_FLAG
        flags = subprocess._args_from_interpreter_flags()
        hidden.popen([sys.executable, *flags, "-m", "forest.app.main", RESTART_FLAG], cwd=REPO_ROOT)
        win.destroy()
        return {"ok": True}

    def setup_checks(self) -> dict:
        """Status line like OrthoStudio's: how many required checks pass."""
        from forest import regions
        from forest.fetch import sources
        from forest.fetch.ecoregions import clipped_path

        names = regions.names()
        items = {
            "X-Plane 12 folder": config.check_xplane(self.cfg.xplane_root)["ok"],
            "DSFTool": config.check_dsftool(self.cfg.dsftool)["ok"],
            "forest catalog": self.catalog_ok,
            "OSM country extract": any(regions.get(n).osm_file.exists() for n in names),
            "ecoregions map": sources.ecoregions_zip().exists() or any(clipped_path(n).exists() for n in names),
        }
        return {"items": items, "ok": sum(items.values()), "total": len(items)}

    # ---------- state ----------
    def get_state(self) -> dict:
        from forest import regions

        default = regions.default_name()
        return {
            "version": __version__,
            "profiles": st.list_profiles(),
            "regions": regions.names(),
            "region_info": {n: regions.get(n).info() for n in regions.names()},
            "default_region": default,
            "schema": {k: {"type": v[0].__name__, "min": v[1], "max": v[2], "step": v[3],
                           "group": v[4], "label": v[5], "help": v[6]}
                       for k, v in st.PROFILE_SCHEMA.items()},
            "density_levels_help": st.DENSITY_LEVELS_HELP,
            "fps_impact": st.FPS_IMPACT,
            "ecoregions": self.ecoregion_names(default),
            "overlay": {"palette": OVERLAY_PALETTE, "shade": OVERLAY_SHADE, "cut": OVERLAY_CUT, "osm": OVERLAY_OSM,
                        "scrub": OVERLAY_SCRUB, "town": OVERLAY_TOWN},
            "evergreen": self.evergreen,
            "evergreen_all": self.evergreen_all,
            "forests": self.forests,          # incl. seasonal types, for regions that allow them
            "forests_all": self.forests_all,
            "asset_info": self.asset_info,
            "xplane_root": str(self.cfg.xplane_root),
            "dsftool_ok": self.cfg.dsftool.exists(),
            "setup": self.setup_checks(),
            "osm_pbf": any((config.CACHE_ROOT / "osm").glob("*.osm.pbf")),
        }

    @staticmethod
    def ecoregion_names(region: str) -> list[str]:
        """Ecoregion names inside the region (for the biome editor); [] until the map is downloaded."""
        try:
            from forest.fetch.ecoregions import ecoregion_names
            return ecoregion_names(region)
        except (OSError, FileNotFoundError, ValueError):
            return []

    def tile_status(self, profile: str = "") -> dict:
        """Every tile of every region: its region (whose forest types build it), land / data
        cached / built / installed (= built and the pack is linked into X-Plane), the last
        build's stats and, for built tiles, why they're outdated compared with `profile` + the
        region's current forest types + the downloaded data."""
        from forest import regions
        from forest.build import Settings, outdated_reasons, settings_fingerprint
        from forest.pack import pack_status
        from forest.paths import dsf_path, studio_path

        fps: dict[str, str | None] = {}

        def fp_of(region: str) -> str | None:
            if region not in fps:
                try:
                    fps[region] = settings_fingerprint(Settings.load(profile, region)) if profile else None
                except (OSError, ValueError):
                    fps[region] = None
            return fps[region]

        xp = self.cfg.xplane_root
        gs = xp / "Global Scenery" / "X-Plane 12 Global Scenery" / "Earth nav data"
        linked = pack_status()["installed"]
        out: dict[str, dict] = {}
        for name, region in regions.owners().items():
            t = Tile.parse(name)
            c = CACHE / t.name
            built = dsf_path(t).exists()
            stats = studio_path(t, ".stats.json")
            s = {
                "region": region,
                "land": (gs / t.folder / f"{t.name}.dsf").exists(),
                "cached": (c / "worldcover.tif").exists() and (c / "osm.json").exists(),
                "built": built,
                "installed": built and linked,
            }
            j = None
            if stats.exists():
                try:
                    j = json.loads(stats.read_text(encoding="utf-8"))
                    s["stats"] = {k: j.get(k) for k in
                                  ("estimated_trees", "forest_km2", "polygons", "budget_scale", "profile", "seconds")}
                except (OSError, ValueError):
                    j = None
            fp = fp_of(region) if built else None
            if fp:
                s["outdated"] = outdated_reasons(j, t, profile, region, fp)
            out[t.name] = s
        return out

    # ---------- X-Plane pack (one linked folder) ----------
    def pack_status(self) -> dict:
        from forest.pack import pack_status
        return pack_status()

    def install_pack(self) -> dict:
        from forest.pack import link_pack
        if self.job.running:
            return {"ok": False, "message": "a build is running; wait for it to finish"}
        try:
            return link_pack()
        except (OSError, RuntimeError) as exc:
            return {"ok": False, "message": str(exc)}

    def uninstall_pack(self) -> dict:
        from forest.pack import unlink_pack
        try:
            return unlink_pack()
        except (OSError, RuntimeError) as exc:
            return {"ok": False, "message": str(exc)}

    # ---------- settings ----------
    def get_profile(self, name: str) -> dict:
        from forest.build import PROFILE_DEFAULTS
        return {**PROFILE_DEFAULTS, **st.read_toml(config.profile_path(name))}

    def default_profile(self, name: str) -> dict:
        """The values shipped with ForestStudio for this profile (for "Reset to defaults").
        A profile of the user's own (e.g. "medium-copy") resets to the shipped medium one."""
        from forest.build import PROFILE_DEFAULTS
        base = name if (config.DEFAULTS_DIR / "profiles" / f"{name}.toml").exists() else "medium"
        values = st.read_toml(config.DEFAULTS_DIR / "profiles" / f"{base}.toml")
        return {"base": base, "values": {**PROFILE_DEFAULTS, **values}}

    def save_profile(self, name: str, data: dict) -> dict:
        try:
            st.write_profile(name, data)
            return {"ok": True}
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def get_rules(self, region: str) -> dict:
        from forest.build import fill_rule_defaults
        return fill_rule_defaults(st.read_toml(config.rules_path(region)), region)

    def default_rules(self, region: str) -> dict:
        """The forest types shipped with ForestStudio for this region (for "Reset to defaults")."""
        import tomllib

        from forest import regions
        from forest.build import fill_rule_defaults
        path = config.DEFAULTS_DIR / "rules" / f"{region}.toml"
        if path.exists():
            return {"ok": True, "rules": fill_rule_defaults(st.read_toml(path), region)}
        try:   # a region without hand-made rules: the global defaults by biome
            return {"ok": True, "rules": tomllib.loads(regions.template_rules_text(regions.get(region)))}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def save_rules(self, region: str, data: dict) -> dict:
        try:
            from forest import regions

            allowed = self.evergreen_all if regions.get(region).evergreen_only else self.forests_all
            st.write_rules(region, data, set(allowed))
            return {"ok": True}
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    # ---------- results ----------
    def get_overlay(self, tile: str) -> dict | None:
        t = Tile.parse(tile)
        from forest.paths import studio_path
        png = studio_path(t, ".overlay.png")
        if not png.exists():
            return None
        return {"bounds": [[t.lat, t.lon], [t.lat + 1, t.lon + 1]],
                "url": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()}

    def get_stats(self, tile: str) -> dict | None:
        t = Tile.parse(tile)
        from forest.paths import studio_path
        p = studio_path(t, ".stats.json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # ---------- deleting tiles ----------
    def _tile_files(self, t: Tile) -> dict[str, list[Path]]:
        from forest.paths import dsf_path, studio_files

        dsf = dsf_path(t)
        cache = CACHE / t.name
        return {
            "output": ([dsf] if dsf.exists() else []) + studio_files(t),
            "cache": [cache] if cache.exists() and any(cache.iterdir()) else [],
        }

    def delete_preview(self, tiles: list[str]) -> dict:
        """What deleting these tiles would remove, per kind, with sizes (nothing is deleted)."""

        try:
            ts = [Tile.parse(x) for x in tiles]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        res = {k: {"tiles": 0, "mb": 0.0} for k in ("output", "cache")}
        for t in ts:
            for kind, paths in self._tile_files(t).items():
                if paths:
                    res[kind]["tiles"] += 1
                    res[kind]["mb"] += sum(fsutil.size_mb(q) for q in paths)
        for v in res.values():
            v["mb"] = round(v["mb"], 1)
        return {"ok": True, "tiles": len(ts), "pack_linked": self.pack_status()["installed"], **res}

    def delete_tiles(self, tiles: list[str], kinds: list[str]) -> dict:
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
            files = self._tile_files(t)
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
            d = dsf_path(t).parent
            try:
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass
        return {"ok": True, "removed": removed}

    # ---------- estimates ----------
    def estimate(self, tiles: list[str], workers: int = 0) -> dict:
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
        from forest import regions
        need_osm = [t for t in ts if not is_current(t, regions.osm_sources(t) or None)]
        from forest.paths import dsf_path
        new_out = [t for t in ts if not dsf_path(t).exists()]
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

    def _xplane_missing(self) -> str | None:
        """Why tiles can't be downloaded or built here, or None. Both read X-Plane: the elevation
        comes from its base mesh and the forest types from its library, so without a valid folder a
        job would download hundreds of MB and then fail."""
        root = self.cfg.xplane_root
        x = config.check_xplane(root if str(root) not in ("", ".") else None)
        if x["ok"]:
            return None
        return ("Set your X-Plane 12 folder in Settings first: ForestStudio reads the ground height "
                "and the forest types from it (missing: " + "; ".join(x["problems"]) + ").")

    def start_build(self, tiles: list[str], profile: str, workers: int = 0) -> dict:
        """Build tiles of any regions together: each with its own region's forest types
        (regions.owner). Every built tile goes into the one linked pack, so it's in X-Plane at once."""
        from forest import regions
        if self.job.running:
            return {"ok": False, "error": "a build is already running"}
        if err := self._xplane_missing():
            return {"ok": False, "error": err}
        try:
            names = [Tile.parse(x).name for x in tiles]
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not names:
            return {"ok": False, "error": "select at least one tile"}
        own = regions.owners()
        outside = [x for x in names if x not in own]
        if outside:
            return {"ok": False, "error": f"no region covers {', '.join(outside)}: add it in Data → Regions"}
        # Sea-only tiles: X-Plane has no base-mesh tile there (no elevation, often no satellite map
        # either), so the build could only fail with a cryptic download error.
        gs = self.cfg.xplane_root / "Global Scenery" / "X-Plane 12 Global Scenery" / "Earth nav data"
        if gs.is_dir():
            sea = [x for x in names if not (gs / Tile.parse(x).folder / f"{x}.dsf").exists()]
            if sea:
                return {"ok": False, "error": f"{', '.join(sea)}: sea only (no land in X-Plane's scenery "
                                              "there), nothing to build. Unselect it and try again."}
        info = self.parallel_info()
        n = int(workers) if workers and int(workers) > 0 else info["workers"]
        n = max(1, min(n, info["max"], len(names)))
        from forest.jobs import JobRecord

        j = self.job
        if not j.claim():
            return {"ok": False, "error": "a build is already running"}
        try:
            j.cancel, j.done, j.failed, j.queue = False, [], [], list(names)
            j.current, j.workers, j.region, j.progress = None, n, "", None
            j.phase, j.groups = None, {}
            used = sorted({own[x] for x in names})
            j.record = JobRecord.new(names, profile, ", ".join(regions.get(r).label for r in used), n)
            for t in names:
                j.record.step(t, "data", "running")
            owners = {x: own[x] for x in names}
            threading.Thread(target=self._run, args=(names, profile, owners, n), daemon=True).start()
        except BaseException:
            j.running, j.record = False, None
            raise
        return {"ok": True, "workers": n, "job": j.record.id}

    # ---------- data (downloads without building) ----------
    def data_status(self) -> dict:
        """Country-wide files (each region's OSM file, the ecoregions map) and every tile's
        downloaded data (no network)."""
        from forest import regions
        from forest.build import DATA_FILES
        from forest.fetch import sources
        from forest.fetch.ecoregions import clipped_path
        from forest.fetch.osm_pbf import is_current
        from forest.paths import dsf_path

        osm = {}
        for n in regions.names():
            try:
                r = regions.get(n)
            except ValueError:
                continue
            osm[n] = {**sources.status(r.osm_file, r.osm_url), "file": r.osm_file.name,
                      "cutout": clipped_path(n).exists()}
        eco = sources.status(sources.ecoregions_zip(), sources.ECOREGIONS_URL)
        from forest.fetch import climate
        clim = sources.status(climate.climate_path(), climate.KOPPEN_URL)

        def _osm_stale(t, o_time):   # older than a file it comes from, or from other files
            if not o_time:
                return False
            srcs = regions.osm_sources(t)
            newest = max((sources.read_meta(p).get("downloaded") or 0 for p in srcs), default=0)
            return o_time < newest or not is_current(t, srcs or None)

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
                "leaftype": files["leaftype.tif"],
                "osm": files["osm.json"], "osm_date": o_time,
                "osm_stale": _osm_stale(t, o_time),
                "elevation": (d / "elevation_int16.npy").exists(),
                "built": dsf_path(t).exists(),
            })
        return {"osm": osm, "ecoregions": eco, "climate": clim, "tiles": tiles, "busy": self.job.running}

    def check_osm_update(self, region: str = "colombia") -> dict:
        from forest.fetch import sources
        try:
            return {"ok": True, **sources.check_update(sources.osm_file(region), sources.osm_url(region))}
        except OSError as exc:
            return {"ok": False, "error": f"Couldn't reach Geofabrik: {exc}"}

    # ---------- regions from Geofabrik's list ----------
    def geofabrik_list(self) -> dict:
        """Geofabrik's extracts as a tree (no network), with the regions already added."""
        from forest import regions
        from forest.fetch import geofabrik, osm_pbf, sources

        if not geofabrik.has_index():
            return {"ok": True, "has_index": False, "entries": []}
        added = {}
        for n in regions.names():
            try:
                r = regions.get(n)
            except ValueError:
                continue
            added[r.osm_file.name] = {"region": n, "downloaded": r.osm_file.exists(),
                                      "user": regions.is_user_region(n)}
        out = []
        for e in geofabrik.tree():
            fn = e["pbf"].rsplit("/", 1)[-1]
            meta = sources.read_meta(osm_pbf.PBF_DIR / fn) if fn in added else {}
            out.append({**e, **({"added": added[fn], "size": meta.get("size")} if fn in added else {})})
        return {"ok": True, "has_index": True, "entries": out,
                "index_date": int(geofabrik.index_path().stat().st_mtime)}

    def geofabrik_download_index(self) -> dict:
        from forest.fetch import geofabrik
        try:
            geofabrik.download_index()
        except OSError as exc:
            return {"ok": False, "error": f"Couldn't download Geofabrik's list: {exc}"}
        return self.geofabrik_list()

    def geofabrik_sizes(self, ids: list[str]) -> dict:
        """Download sizes (MB) of these extracts, asked from Geofabrik (HEAD requests)."""
        from forest.fetch import geofabrik
        es = geofabrik.entries()
        urls = {i: es[i]["pbf"] for i in ids[:80] if i in es}
        got = geofabrik.sizes(list(urls.values()))
        return {"ok": True, "sizes": {i: (round(got[u] / 1e6) if got.get(u) else None) for i, u in urls.items()}}

    def add_region(self, eid: str) -> dict:
        from forest import regions
        try:
            r = regions.add_from_geofabrik(eid)
            config.rules_path(r.name)          # forest types from the global defaults
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        # Its data downloads right away (OSM file, ecoregions, cut-out); if something else is
        # running, the Regions list offers the download afterwards.
        dl = self.start_data_job("region", [], r.name)
        return {"ok": True, "region": r.name, "tiles": len(r.tiles()), "download": dl, **self._regions_state()}

    def remove_region(self, name: str) -> dict:
        from forest import regions
        if self.job.running:
            return {"ok": False, "error": "Wait until the running build or download finishes."}
        try:
            gone = regions.remove(name)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "removed": gone, **self._regions_state()}

    def _regions_state(self) -> dict:
        from forest import regions
        return {"regions": regions.names(), "default_region": regions.default_name(),
                "region_info": {n: regions.get(n).info() for n in regions.names()}}

    def start_data_job(self, kind: str, tiles: list[str] | None = None, region: str = "colombia",
                       force: bool = False) -> dict:
        """kind: 'tiles' (download data for these tiles), 'osm' (download/update the region's
        country file and re-extract every tile that has OSM data), 'ecoregions', 'region'
        (everything a newly added region needs: OSM file, ecoregions map, its cut-out)."""
        from forest.jobs import JobRecord

        if self.job.running:
            return {"ok": False, "error": "A build or download is already running."}
        if kind == "tiles":
            if err := self._xplane_missing():   # a tile's data includes its elevation
                return {"ok": False, "error": err}
            try:
                names = sorted({Tile.parse(x).name for x in tiles or []})
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if not names:
                return {"ok": False, "error": "Select some tiles on the map first."}
            args, rkind, label = ("fetch", {"tiles": names}), "data", "downloading data"
        elif kind == "osm":
            from forest import regions
            reg = regions.get(region)
            names = [d.name for d in sorted(CACHE.glob("*")) if (d / "osm.json").exists()
                     and _is_tile_in(d.name, reg)] if CACHE.exists() else []
            args = ("data_osm", {"region": region, "update": True, "force": bool(force)})
            rkind, label = "osm_update", "updating OSM data"
        elif kind == "region":
            from forest import regions
            try:
                label = f"downloading {regions.get(region).label}"
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            names, args, rkind = [], ("data_region", {"region": region}), "data"
        elif kind == "ecoregions":
            names, args, rkind, label = [], ("data_ecoregions", {"region": region, "update": True}), "data", "downloading ecoregions"
        else:
            return {"ok": False, "error": f"unknown data job '{kind}'"}
        j = self.job
        if not j.claim():
            return {"ok": False, "error": "A build or download is already running."}
        try:
            j.cancel, j.done, j.failed, j.queue = False, [], [], list(names)
            j.current, j.workers = label, 1
            j.phase, j.groups = None, {}
            j.region = region if kind in ("osm", "region", "ecoregions") else ""
            j.progress = None
            j.record = JobRecord.new(names, "", region, 1, kind=rkind)
            for t in names:
                j.record.step(t, "data", "running")
            threading.Thread(target=self._run_data, args=(args, label), daemon=True).start()
        except BaseException:
            j.running, j.record = False, None
            raise
        return {"ok": True, "job": j.record.id, "tiles": len(names)}

    def _run_data(self, args: tuple[str, dict], label: str) -> None:
        j = self.job
        t0 = time.time()
        rc = 1
        try:
            j.add(f"=== {label} ===")
            rc = self._task(*args)
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
        for w in procs:
            w.stop()   # the worker and its DSFTool
        return {"ok": True}

    def job_status(self, after: int = 0) -> dict:
        j = self.job
        with j.lock:
            lines = [ln for ln in j.log if int(ln.split("\t", 1)[0]) > after]
            current = ", ".join(sorted(j.active)) if j.active else j.current
        return {"running": j.running, "current": current, "done": j.done, "failed": j.failed,
                "queue": j.queue, "lines": lines, "workers": j.workers,
                "region": j.region if j.running else "", "progress": j.progress if j.running else None,
                "phase": j.phase if j.running else None,
                "job": j.record.id if j.record else None}

    def _task(self, task: str, kwargs: dict, tag: str = "", tail: list | None = None,
              group: str = "") -> int:
        """Run a forest.tasks function in a fresh worker process and follow its messages (log
        lines, download progress, data phases, build steps). Returns its exit code."""
        from forest import events
        from forest.worker import Worker

        prefix = f"[{tag}] " if tag else ""
        shown = " ".join(f"{k}={','.join(v) if isinstance(v, list) else v}" for k, v in kwargs.items())
        self.job.add(prefix + f"$ {task} {shown}")
        w = Worker(task, **kwargs)
        with self.job.lock:
            if self.job.cancel:
                return 1
            w.start()
            self.job.procs.add(w)
        logged: dict = {}                  # download -> last 10 % step written to the log
        rc = 1

        def keep(line: str) -> None:
            if tail is not None:
                tail.append(line)
                del tail[:-40]
        try:
            for msg in w.messages():
                ev = msg.pop("event", "log")
                if ev == "exit":
                    rc = int(msg.get("rc", 1))
                    if msg.get("crashed") and not self.job.cancel:
                        line = f"the worker stopped unexpectedly (exit {rc})"
                        self.job.add(prefix + line)
                        keep(line)
                    continue
                if ev == "error":
                    err = f"{msg.get('type')}: {msg.get('message')}"
                    for ln in (msg.get("traceback") or "").splitlines():
                        self.job.add(prefix + ln)
                    self.job.add(prefix + "ERROR: " + err)
                    keep(err)
                    continue
                self._on_event(ev, msg, tag, group)
                if ev == "download" and msg.get("pct") is not None:
                    if logged.get(msg["what"]) == msg["pct"] // 10:
                        continue        # the log keeps one line per 10 %; the page shows the rest
                    logged[msg["what"]] = msg["pct"] // 10
                line = events.text(ev, msg)
                if line:
                    self.job.add(prefix + line)
                    keep(line)
            return rc
        finally:
            with self.job.lock:
                self.job.procs.discard(w)

    def _set_phase(self, text: str | None, note: bool = False, tiles: list[str] | None = None,
                   group: str = "") -> None:
        """What a data step is doing now (Tiles tab, Data view: every group's phase, joined); with
        `note`, also written on the data step of its tiles still waiting for it (Build queue)."""
        j = self.job
        with j.lock:
            g = j.group(group)
            g["phase"] = text
            j.phase = " · ".join(x["phase"] for x in j.groups.values() if x["phase"]) or None
        if note and text and j.record is not None:
            j.record.data_note(text, tiles if tiles is not None else g["tiles"])

    def _on_event(self, ev: str, d: dict, tag: str = "", group: str = "") -> None:
        """One progress event from a worker: download %, plain-words phase, build steps."""
        j = self.job
        if j.record is not None:
            j.record.on_event(ev, d)
        if tag:                            # a tile build: its steps are all the record needs
            return
        g = j.group(group)
        if ev == "download":
            pct = d.get("pct")
            j.progress = {"what": d["what"], "mb": int(d["mb"]), "pct": pct}
            name = g["dl_name"] or _DL_DEFAULT.get(d["what"], d["what"])
            done = f"{pct} % " if pct is not None else ""
            self._set_phase(f"Downloaded {name}" if pct == 100 else f"Downloading {name}… {done}({d['mb']} MB)",
                            note=True, group=group)
        elif ev == "downloading":
            g["dl_name"] = d.get("name") or _DL_DEFAULT.get(d["what"], d["what"])
            self._set_phase(f"Downloading {g['dl_name']}…", note=True, group=group)
        elif ev == "sat_start":
            g["sat"] = [0, int(d["n"])]
            self._set_phase(f"Getting satellite maps for {d['n']} tile(s)…", group=group)
        elif ev == "sat_tile":
            g["sat"][0] += 1
            self._set_phase(f"Satellite maps: {g['sat'][0]} of {g['sat'][1]} tile(s) done", group=group)
        elif ev == "osm_reading":
            j.progress = None
            self._set_phase(f"Reading OpenStreetMap ({d['file']}, {d['mb']} MB) for {d['n']} tile(s) — "
                            "this can take a few minutes", note=True, tiles=d.get("tiles"), group=group)
        elif ev in ("osm_done", "ready"):
            j.progress = None
            self._set_phase(None, group=group)

    def _build_one(self, name: str, profile: str, region: str) -> None:
        j = self.job
        if j.cancel:
            return
        with j.lock:
            j.active.add(name)
            j.queue = [n for n in j.queue if n != name]
        j.add(f"=== {name}: building ({profile}, {region}) ===")
        tail: list[str] = []
        rc = 1
        # This runs in a thread pool whose futures nobody reads: an exception here would vanish
        # and leave the tile "building" forever, so every error ends as a failed tile.
        try:
            rc = self._task("build", {"tile": name, "profile": profile, "region": region, "compile": True},
                            tag=name, tail=tail)
            if j.record is not None:
                j.record.tile_finished(name, rc, tail, self._tile_result(name) if rc == 0 else None)
        except Exception as exc:  # noqa: BLE001 - report and carry on with the other tiles
            rc = 1
            err = f"{type(exc).__name__}: {exc}"
            j.add(f"[{name}] ERROR: {err}")
            if j.record is not None:
                try:
                    j.record.tile_finished(name, rc, tail + [err], None)
                except Exception:  # noqa: BLE001 - the record itself is broken; the log has it
                    pass
        with j.lock:
            j.active.discard(name)
            if not j.cancel or rc == 0:
                (j.done if rc == 0 else j.failed).append(name)
        j.add(f"=== {name}: {'ok' if rc == 0 else ('cancelled' if j.cancel else f'failed (exit {rc})')} ===")

    def _data_failed(self, name: str) -> bool:
        """The tile's data step failed (no country file, satellite download error): mark the
        tile failed with that reason instead of starting a build that can only fail."""
        j = self.job
        st = j.record.d["steps"].get(name, {}).get("data", {}) if j.record is not None else {}
        if st.get("status") != "failed":
            return False
        note = st.get("note") or "data download failed"
        j.add(f"=== {name}: not built — {note} ===")
        with j.lock:
            j.queue = [n for n in j.queue if n != name]
            j.failed.append(name)
        j.record.tile_finished(name, 1, [note], None)
        return True

    def _tile_result(self, name: str) -> dict:
        from forest.paths import dsf_path
        t = Tile.parse(name)
        st = self.get_stats(name) or {}
        out = {k: st.get(k) for k in ("forest_km2", "estimated_trees", "polygons", "budget_scale", "seconds")}
        out["dsf_mb"] = round(fsutil.size_mb(dsf_path(t)), 2)
        return out

    def _run(self, names: list[str], profile: str, owners: dict[str, str], workers: int = 1) -> None:
        """Data, then builds. Tiles are split into groups that share no OSM country file
        (regions.osm_groups); the groups' data steps run side by side and each group's tiles start
        building as soon as its own data is ready. Data steps and builds share the `workers`
        slots: reading a country file needs memory like a build does."""
        from concurrent.futures import ThreadPoolExecutor, wait

        from forest import regions

        j = self.job
        t_all = time.time()
        try:
            j.current = "downloading data"
            groups = regions.osm_groups(names, owners)
            j.add(f"=== data for {len(names)} tile(s) in {len(groups)} group(s); "
                  f"{workers} at a time for data and builds ===")
            # Files every country shares, once, before the groups run side by side.
            if self._task("prepare", {"tiles": names}) != 0:
                j.add("shared files could not be prepared; the builds may fail")
            if j.cancel:
                j.add("cancelled")
                return
            slots = threading.Semaphore(workers)
            fetching = [len(groups)]
            builds: list = []
            pool = ThreadPoolExecutor(max_workers=workers + len(groups))

            def build(name: str) -> None:
                with slots:
                    self._build_one(name, profile, owners[name])

            def data(key: str, tiles: list[str]) -> None:
                try:
                    with slots:
                        if j.cancel:
                            return
                        j.add(f"=== data for {key or ', '.join(tiles)} ===")
                        if self._task("fetch", {"tiles": tiles}, group=key) != 0:
                            j.add("some data is missing; those tiles will fail, the others are built")
                    self._set_phase(None, group=key)
                    if not j.cancel:
                        for n in tiles:
                            if self._data_failed(n):     # nothing to build from: fail it now
                                continue
                            builds.append(pool.submit(build, n))
                except Exception as exc:  # noqa: BLE001 - nobody reads this future: fail its tiles
                    j.add(f"ERROR in the data step: {type(exc).__name__}: {exc}")
                    with j.lock:
                        j.failed.extend(tiles)
                        j.queue = [n for n in j.queue if n not in tiles]
                    if j.record is not None:
                        for n in tiles:
                            j.record.step(n, "data", "failed", str(exc)[:200])
                finally:
                    with j.lock:
                        fetching[0] -= 1
                        if fetching[0] == 0:
                            j.current = None

            futs = []
            for tiles in groups:
                key = ""
                if len(groups) > 1:
                    key = ", ".join(tiles) if len(tiles) <= 3 else f"{tiles[0]} +{len(tiles) - 1}"
                with j.lock:
                    j.group(key)["tiles"] = list(tiles)
                futs.append(pool.submit(data, key, tiles))
            wait(futs)            # every group's data is done and its builds are queued
            wait(list(builds))
            pool.shutdown()
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
