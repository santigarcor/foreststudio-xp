"""Saved build jobs ("Works"): what was built, each tile's steps, timings, results and log.

cache/jobs/<id>.json   the job record (updated while it runs)
cache/jobs/<id>.log    the full log

Steps per tile: data (satellite + OSM) → classify → vectorise → compile.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from forest import config

JOBS_DIR = config.CACHE_ROOT / "jobs"
STEPS = ["data", "classify", "vectorise", "compile"]
STEP_LABELS = {"data": "Data", "classify": "Classify", "vectorise": "Vectorise", "compile": "Compile"}
KINDS = {"build": "Build", "data": "Download data", "osm_update": "Update OSM data"}



def _now() -> float:
    return time.time()


class JobRecord:
    def __init__(self, data: dict):
        self.d = data
        self.lock = threading.RLock()   # re-entrant: full() reads summary + report under it
        self._log_fh = None   # the job's log, kept open while it runs (see log())

    # ---------- create / load ----------
    @classmethod
    def new(cls, tiles: list[str], profile: str, region: str, workers: int,
            kind: str = "build") -> "JobRecord":
        """kind: build (all steps) | data / osm_update (download only: the Data step)."""
        jid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        steps = STEPS if kind == "build" else ["data"]
        rec = cls({
            "id": jid, "created": _now(), "ended": None, "status": "running", "kind": kind,
            "step_names": steps,
            "profile": profile, "region": region, "workers": workers, "tiles": list(tiles),
            "steps": {t: {s: {"status": "pending", "started": None, "seconds": None, "note": ""}
                          for s in steps} for t in tiles},
            "results": {}, "error": "", "log_lines": 0,
        })
        rec.save()
        return rec

    @classmethod
    def load(cls, jid: str) -> "JobRecord | None":
        p = JOBS_DIR / f"{jid}.json"
        try:
            return cls(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    @property
    def kind(self) -> str:
        return self.d.get("kind", "build")

    @property
    def step_names(self) -> list[str]:
        return self.d.get("step_names", STEPS)

    @property
    def id(self) -> str:
        return self.d["id"]

    @property
    def log_path(self) -> Path:
        return JOBS_DIR / f"{self.id}.log"

    def save(self) -> None:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        p = JOBS_DIR / f"{self.id}.json"
        tmp = p.with_suffix(".json.tmp")
        # Write and swap under the lock: parallel builds finish at the same time and share
        # the .tmp name (an unlocked replace raced and failed with PermissionError on Windows).
        with self.lock:
            tmp.write_text(json.dumps(self.d, indent=1), encoding="utf-8")
            # Windows refuses the swap while another thread reads the record (Build queue
            # view polling list_jobs): retry briefly instead of losing the update.
            for attempt in range(20):
                try:
                    os.replace(tmp, p)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)

    # ---------- updates ----------
    def log(self, line: str) -> None:
        # Opened once and line-buffered (the Build queue view reads the file while the job runs)
        # instead of opening and closing it for every line of 3 parallel builds.
        with self.lock:
            if self._log_fh is None:
                JOBS_DIR.mkdir(parents=True, exist_ok=True)
                self._log_fh = self.log_path.open("a", encoding="utf-8", buffering=1)
            self._log_fh.write(line.rstrip("\n") + "\n")
            self.d["log_lines"] = self.d.get("log_lines", 0) + 1

    def close_log(self) -> None:
        with self.lock:
            if self._log_fh is not None:
                self._log_fh.close()
                self._log_fh = None

    def step(self, tile: str, step: str, status: str, note: str = "") -> None:
        if tile not in self.d["steps"]:
            return
        with self.lock:
            st = self.d["steps"][tile][step]
            if status == "running" and st["started"] is None:
                st["started"] = _now()
            if status in ("done", "failed", "skipped") and st["started"] is not None and st["seconds"] is None:
                st["seconds"] = round(_now() - st["started"], 1)
            st["status"] = status
            if note:
                st["note"] = note

    def data_note(self, note: str, tiles: list[str] | None = None) -> None:
        """What the shared data step is doing (downloading the country file, reading OSM…), shown
        on every tile whose data isn't finished yet (only `tiles` when given: e.g. the tiles one
        OSM country file is read for, not a neighbour waiting for another country's file)."""
        for t in self.d["tiles"]:
            if tiles is not None and t not in tiles:
                continue
            if self.d["steps"][t]["data"]["status"] in ("pending", "running"):
                self.step(t, "data", "running", note)

    def on_event(self, ev: str, d: dict) -> None:
        """Advance steps from a worker's progress event (forest.events)."""
        if ev == "sat_tile":
            if d.get("ok"):
                self.step(d["tile"], "data", "running", "satellite maps ready · OpenStreetMap next")
            else:
                self.step(d["tile"], "data", "failed", str(d.get("error", ""))[:200])
        elif ev == "osm_done":
            note = "OSM: already up to date" if not d.get("n") else f"OSM read for {d['n']} tile(s)"
            only = d.get("tiles")
            for t in self.d["tiles"]:
                if only is not None and t not in only:
                    continue           # another country's tiles: another worker reads their file
                if self.d["steps"][t]["data"]["status"] != "failed":
                    self.step(t, "data", "done", note)
        elif ev == "step" and d.get("step") in self.step_names:
            self.step(d["tile"], d["step"], d.get("status", "running"), d.get("note", ""))

    def tile_finished(self, tile: str, rc: int, last_lines: list[str], result: dict | None) -> None:
        steps = self.d["steps"].get(tile, {})
        if rc == 0:
            for s in self.step_names:
                if steps[s]["status"] in ("pending", "running"):
                    self.step(tile, s, "done")
        else:
            already = next((s for s in self.step_names if steps[s]["status"] == "failed"), None)
            failed = already or next((s for s in self.step_names if steps[s]["status"] == "running"),
                                     next((s for s in self.step_names if steps[s]["status"] == "pending"),
                                          self.step_names[-1]))
            err = next((ln for ln in reversed(last_lines) if ln.strip()), f"exit code {rc}")
            if not already:            # e.g. the Data step already failed with its own reason
                self.step(tile, failed, "failed", err.strip()[:300])
            for s in self.step_names[self.step_names.index(failed) + 1:]:
                self.step(tile, s, "skipped")
        with self.lock:
            self.d["results"][tile] = {"ok": rc == 0, **(result or {}),
                                      **({} if rc == 0 else {"error": "\n".join(last_lines[-6:])})}
        self.save()

    def finish(self, cancelled: bool = False, rc: int = 0) -> None:
        with self.lock:
            res = self.d["results"]
            if self.kind != "build":   # download jobs: a tile is ok when its Data step finished
                for t in self.d["tiles"]:
                    st = self.d["steps"][t]["data"]
                    if st["status"] == "running" and rc == 0 and not cancelled:
                        st["status"] = "done"
                    res[t] = {"ok": st["status"] == "done",
                              **({} if st["status"] == "done" else {"error": st.get("note") or "not downloaded"})}
            failed = [t for t, r in res.items() if not r.get("ok")]
            self.d["ended"] = _now()
            self.d["status"] = ("cancelled" if cancelled else "failed" if failed and len(failed) == len(self.d["tiles"])
                                else "partial" if failed else "done")
            for t in self.d["tiles"]:  # tiles never started (cancel): mark their steps skipped
                for s in self.step_names:
                    if self.d["steps"][t][s]["status"] in ("pending", "running"):
                        self.d["steps"][t][s]["status"] = "skipped"
        self.save()
        self.close_log()

    # ---------- views ----------
    def progress(self) -> float:
        total = len(self.d["tiles"]) * len(self.step_names) or 1
        done = sum(1 for t in self.d["steps"].values() for s in t.values()
                   if s["status"] in ("done", "failed", "skipped"))
        return round(done / total, 3)

    def summary(self) -> dict:
        with self.lock:            # build threads add results while the app reads them
            return self._summary()

    def _summary(self) -> dict:
        res = self.d["results"]
        ok = [r for r in res.values() if r.get("ok")]
        end = self.d["ended"] or _now()
        return {
            "id": self.id, "created": self.d["created"], "status": self.d["status"], "kind": self.kind,
            "profile": self.d["profile"], "region": self.d["region"], "workers": self.d["workers"],
            "tiles": self.d["tiles"], "progress": self.progress(),
            "elapsed": round(end - self.d["created"], 1),
            "built": len(ok), "failed": len(res) - len(ok),
        }

    def report(self) -> dict:
        """Totals for the 'Final report' panel."""
        with self.lock:
            return self._report()

    def _report(self) -> dict:
        res = self.d["results"]
        ok = [r for r in res.values() if r.get("ok")]
        per_step = {}
        for s in self.step_names:
            secs = [t[s]["seconds"] for t in self.d["steps"].values() if t[s]["seconds"] is not None]
            per_step[s] = {
                "done": sum(1 for t in self.d["steps"].values() if t[s]["status"] == "done"),
                "failed": sum(1 for t in self.d["steps"].values() if t[s]["status"] == "failed"),
                "skipped": sum(1 for t in self.d["steps"].values() if t[s]["status"] == "skipped"),
                "seconds_total": round(sum(secs), 1), "seconds_avg": round(sum(secs) / len(secs), 1) if secs else None,
            }
        return {
            "tiles": len(self.d["tiles"]), "built": len(ok), "failed": len(res) - len(ok),
            "forest_km2": round(sum(r.get("forest_km2") or 0 for r in ok), 1),
            "trees": round(sum(r.get("estimated_trees") or 0 for r in ok)),
            "polygons": sum(r.get("polygons") or 0 for r in ok),
            "dsf_mb": round(sum(r.get("dsf_mb") or 0 for r in ok), 1),
            "per_step": per_step,
        }

    def full(self) -> dict:
        import copy
        with self.lock:            # a copy: the window serialises it after the lock is released
            return {**self._summary(), "steps": copy.deepcopy(self.d["steps"]),
                    "results": copy.deepcopy(self.d["results"]), "report": self._report(),
                    "log_lines": self.d.get("log_lines", 0), "error": self.d.get("error", ""),
                    "step_names": self.step_names, "step_labels": STEP_LABELS,
                    "kind_label": KINDS.get(self.kind, self.kind)}


# ---------- module helpers ----------
# Summaries of finished jobs by (file, modified time, size): the Build queue view polls
# list_jobs every 1.5 s while a job runs, and old records never change.
_summaries: dict[Path, tuple[tuple[int, int], dict]] = {}


def list_jobs(limit: int = 200) -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    out = []
    for p in sorted(JOBS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            st = p.stat()
        except OSError:
            continue
        key = (st.st_mtime_ns, st.st_size)
        hit = _summaries.get(p)
        if hit and hit[0] == key and hit[1]["status"] != "running":   # running: elapsed changes
            out.append(hit[1])
            continue
        rec = JobRecord.load(p.stem)
        if rec:
            summary = rec.summary()
            _summaries[p] = (key, summary)
            out.append(summary)
    return out


def read_log(jid: str, tail: int = 5000) -> list[str]:
    p = JOBS_DIR / f"{jid}.log"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-tail:]


def delete(jid: str) -> bool:
    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", jid):
        return False
    gone = False
    for suffix in (".json", ".log"):
        p = JOBS_DIR / f"{jid}{suffix}"
        _summaries.pop(p, None)
        if p.exists():
            p.unlink()
            gone = True
    return gone


def mark_interrupted() -> int:
    """Jobs still 'running' on disk when the app starts were cut off by closing it."""
    n = 0
    for p in JOBS_DIR.glob("*.json") if JOBS_DIR.exists() else []:
        rec = JobRecord.load(p.stem)
        if rec and rec.d.get("status") == "running":
            rec.d["status"] = "interrupted"
            rec.d["ended"] = rec.d.get("ended") or p.stat().st_mtime
            for t in rec.d["steps"].values():
                for s in t.values():
                    if s["status"] in ("pending", "running"):
                        s["status"] = "skipped"
            rec.save()
            n += 1
    return n
