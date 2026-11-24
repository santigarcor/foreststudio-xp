"""Country-wide source files: the OpenStreetMap country extract (Geofabrik) and the RESOLVE
Ecoregions map. Downloaded on demand (first build, or the Data view), updated on request.

Each downloaded file gets a small `<file>.json` next to it with the server's date, size and
when it was downloaded, so the app can tell whether a newer version exists.
"""

from __future__ import annotations

import json
import time
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

USER_AGENT = "ForestStudio-XP (X-Plane scenery generator)"
CHUNK = 1 << 20

# Each region's OSM extract is set in its region file (config/regions/<name>.toml).
ECOREGIONS_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"


def osm_url(region: str) -> str:
    from forest import regions
    return regions.get(region).osm_url


def osm_file(region: str) -> Path:
    from forest import regions
    return regions.get(region).osm_file


def ecoregions_zip() -> Path:
    from forest.fetch import ecoregions
    return ecoregions.ZIP


def _meta_path(p: Path) -> Path:
    return p.with_name(p.name + ".json")


def read_meta(p: Path) -> dict:
    try:
        return json.loads(_meta_path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remote_info(url: str, timeout: float = 30) -> dict:
    """Server date (unix seconds) and size, from a HEAD request."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        lm = r.headers.get("Last-Modified")
        return {"remote_date": int(parsedate_to_datetime(lm).timestamp()) if lm else None,
                "remote_size": int(r.headers.get("Content-Length") or 0) or None}


def download(url: str, dest: Path, progress: Callable[[int, int | None], None] | None = None) -> Path:
    """Download to <dest>.part, then replace dest in one step (a half download never counts)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, part.open("wb") as fh:
            total = int(r.headers.get("Content-Length") or 0) or None
            lm = r.headers.get("Last-Modified")
            done = 0
            while chunk := r.read(CHUNK):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise OSError(f"download incomplete ({done} of {total} bytes)")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    _meta_path(dest).write_text(json.dumps({
        "url": url, "remote_date": int(parsedate_to_datetime(lm).timestamp()) if lm else None,
        "size": done, "downloaded": int(time.time())}), encoding="utf-8")
    return dest


def status(p: Path, url: str) -> dict:
    """What we have locally (no network)."""
    m = read_meta(p)
    exists = p.exists()
    return {"exists": exists, "path": str(p), "url": url,
            "mb": round(p.stat().st_size / 1e6, 1) if exists else 0,
            "remote_date": m.get("remote_date"),
            "downloaded": m.get("downloaded") or (int(p.stat().st_mtime) if exists else None)}


def check_update(p: Path, url: str) -> dict:
    """Compare with the server (network). newer = the server has a newer file than ours."""
    local = status(p, url)
    info = remote_info(url)
    ours = local["remote_date"] or local["downloaded"]
    newer = bool(not local["exists"] or (info["remote_date"] and ours and info["remote_date"] > ours + 60))
    return {**local, **info, "newer": newer}


def print_progress(label: str) -> Callable[[int, int | None], None]:
    """Download progress as `download` events, one per 1 % (the app logs every 10th and shows the
    rest as a progress bar; the CLI prints each)."""
    last = [-1]

    def cb(done: int, total: int | None) -> None:
        step = int(done * 100 / total) if total else done // (50 * CHUNK)
        if step != last[0]:
            last[0] = step
            from forest import events
            events.emit("download", what=label, mb=round(done / 1e6), pct=done * 100 // total if total else None)
    return cb
