"""Measured timings and sizes of past fetches/builds, used for time & disk estimates.

One JSON line per event in cache/history.jsonl:
  {"kind": "rasters"|"osm_pass"|"build", "tile": ..., "seconds": ..., ...sizes in MB}
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from forest.config import REPO_ROOT

HISTORY = REPO_ROOT / "cache" / "history.jsonl"

# Used until real measurements exist (from the first test tiles).
DEFAULTS = {
    "rasters_s": 35.0,     # download WorldCover + Hansen windows for one tile
    "osm_pass_s": 140.0,   # one pass over the Colombia .pbf (any number of tiles)
    "build_s": 50.0,       # classify + vectorise + write + compile one tile
    "raster_mb": 18.0,     # cached rasters per tile
    "osm_mb": 40.0,        # cached osm.json per tile (cities far more, jungle far less)
    "dsf_mb": 8.0,         # installed .dsf per tile
    "out_extra_mb": 3.0,   # overlay/preview/stats per tile in out/
}


def record(kind: str, **data) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": kind, "time": round(time.time()), **data}) + "\n")


def _events() -> list[dict]:
    if not HISTORY.exists():
        return []
    out = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def averages() -> dict[str, float]:
    """Medians of the last measurements per quantity, falling back to DEFAULTS."""
    ev = _events()[-500:]
    avg = dict(DEFAULTS)

    def med(kind: str, key: str):
        v = [e[key] for e in ev if e.get("kind") == kind and isinstance(e.get(key), (int, float))]
        return statistics.median(v) if v else None

    for kind, key, name in [("rasters", "seconds", "rasters_s"), ("osm_pass", "seconds", "osm_pass_s"),
                            ("build", "seconds", "build_s"), ("rasters", "mb", "raster_mb"),
                            ("osm_pass", "mb_per_tile", "osm_mb"), ("build", "dsf_mb", "dsf_mb"),
                            ("build", "out_extra_mb", "out_extra_mb")]:
        m = med(kind, key)
        if m is not None:
            avg[name] = float(m)
    avg["samples"] = len(ev)
    return avg


def dir_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_size / 1e6
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1e6
