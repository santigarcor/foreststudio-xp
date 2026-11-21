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

from forest.config import REPO_ROOT

JOBS_DIR = REPO_ROOT / "cache" / "jobs"
STEPS = ["data", "classify", "vectorise", "compile"]
STEP_LABELS = {"data": "Data", "classify": "Classify", "vectorise": "Vectorise", "compile": "Compile"}

# Log lines that move a tile from one step to the next (see build.py / cli.py output).
_TILE_RASTERS = re.compile(r"^([+-]\d{2}[+-]\d{3}): rasters ready")
_TILE_DATA_FAIL = re.compile(r"^([+-]\d{2}[+-]\d{3}): data download FAILED")


def _now() -> float:
    return time.time()


class JobRecord:
    def __init__(self, data: dict):
        self.d = data
        self.lock = threading.Lock()

    # ---------- create / load ----------
    @classmethod
    def new(cls, tiles: list[str], profile: str, region: str, workers: int) -> "JobRecord":
        jid = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        rec = cls({
            "id": jid, "created": _now(), "ended": None, "status": "running",
            "profile": profile, "region": region, "workers": workers, "tiles": list(tiles),
            "steps": {t: {s: {"status": "pending", "started": None, "seconds": None, "note": ""}
                          for s in STEPS} for t in tiles},
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
    def id(self) -> str:
        return self.d["id"]

    @property
    def log_path(self) -> Path:
        return JOBS_DIR / f"{self.id}.log"

    def save(self) -> None:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        p = JOBS_DIR / f"{self.id}.json"
        tmp = p.with_suffix(".json.tmp")
        with self.lock:
            tmp.write_text(json.dumps(self.d, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    # ---------- updates ----------
    def log(self, line: str) -> None:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        with self.lock, self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
            self.d["log_lines"] = self.d.get("log_lines", 0) + 1

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

    def track(self, tag: str, line: str) -> None:
        """Advance steps from one log line (tag = tile name for build lines, '' for data)."""
        text = line.strip()
        if not tag:
            m = _TILE_RASTERS.match(text)
            if m:
                self.step(m.group(1), "data", "running", "satellite data ready")
                return
            m = _TILE_DATA_FAIL.match(text)
            if m:
                self.step(m.group(1), "data", "failed", text.split("FAILED:", 1)[-1].strip()[:200])
                return
            if text.startswith("OSM:"):
                for t in self.d["tiles"]:
                    if self.d["steps"][t]["data"]["status"] != "failed":
                        self.step(t, "data", "done", text)
            return
        if "rasters cached" in text:
            self.step(tag, "data", "done")
            self.step(tag, "classify", "running")
        elif "classified:" in text:
            self.step(tag, "classify", "done")
            self.step(tag, "vectorise", "running")
        elif "vectorised" in text:
            self.step(tag, "vectorise", "done")
        elif text.startswith("[") and "wrote " in text:
            self.step(tag, "compile", "running")

    def tile_finished(self, tile: str, rc: int, last_lines: list[str], result: dict | None) -> None:
        steps = self.d["steps"].get(tile, {})
        if rc == 0:
            for s in STEPS:
                if steps[s]["status"] in ("pending", "running"):
                    self.step(tile, s, "done")
        else:
            failed = next((s for s in STEPS if steps[s]["status"] == "running"),
                          next((s for s in STEPS if steps[s]["status"] == "pending"), "compile"))
            err = next((ln for ln in reversed(last_lines) if ln.strip()), f"exit code {rc}")
            self.step(tile, failed, "failed", err.strip()[:300])
            for s in STEPS[STEPS.index(failed) + 1:]:
                self.step(tile, s, "skipped")
        with self.lock:
            self.d["results"][tile] = {"ok": rc == 0, **(result or {}),
                                      **({} if rc == 0 else {"error": "\n".join(last_lines[-6:])})}
        self.save()

    def finish(self, cancelled: bool = False) -> None:
        with self.lock:
            res = self.d["results"]
            failed = [t for t, r in res.items() if not r.get("ok")]
            self.d["ended"] = _now()
            self.d["status"] = ("cancelled" if cancelled else "failed" if failed and len(failed) == len(self.d["tiles"])
                                else "partial" if failed else "done")
            for t in self.d["tiles"]:  # tiles never started (cancel): mark their steps skipped
                for s in STEPS:
                    if self.d["steps"][t][s]["status"] in ("pending", "running"):
                        self.d["steps"][t][s]["status"] = "skipped"
        self.save()

    # ---------- views ----------
    def progress(self) -> float:
        total = len(self.d["tiles"]) * len(STEPS) or 1
        done = sum(1 for t in self.d["steps"].values() for s in t.values()
                   if s["status"] in ("done", "failed", "skipped"))
        return round(done / total, 3)

    def summary(self) -> dict:
        res = self.d["results"]
        ok = [r for r in res.values() if r.get("ok")]
        end = self.d["ended"] or _now()
        return {
            "id": self.id, "created": self.d["created"], "status": self.d["status"],
            "profile": self.d["profile"], "region": self.d["region"], "workers": self.d["workers"],
            "tiles": self.d["tiles"], "progress": self.progress(),
            "elapsed": round(end - self.d["created"], 1),
            "built": len(ok), "failed": len(res) - len(ok),
        }

    def report(self) -> dict:
        """Totals for the 'Final report' panel."""
        res = self.d["results"]
        ok = [r for r in res.values() if r.get("ok")]
        per_step = {}
        for s in STEPS:
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
        return {**self.summary(), "steps": self.d["steps"], "results": self.d["results"],
                "report": self.report(), "log_lines": self.d.get("log_lines", 0),
                "error": self.d.get("error", ""), "step_names": STEPS, "step_labels": STEP_LABELS}


# ---------- module helpers ----------
def list_jobs(limit: int = 200) -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    out = []
    for p in sorted(JOBS_DIR.glob("*.json"), reverse=True)[:limit]:
        rec = JobRecord.load(p.stem)
        if rec:
            out.append(rec.summary())
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
