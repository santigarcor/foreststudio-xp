"""Pre-release fixes: no silent Overpass fallback, sea tiles, one window per data folder,
recent memory peaks, job views under the lock, JSON-only dev server, TOML strings."""

import json
import threading
import tomllib
import urllib.error
import urllib.request

import pytest

from forest.tiles import Tile

TILE = Tile.parse("+06-076")


# ---------- B3: no Overpass fallback ----------

def test_no_country_file_and_no_osm_data_fails_with_a_clear_message(sandbox):
    from forest.fetch.osm import OsmFileMissing, fetch_osm

    with pytest.raises(OsmFileMissing, match="Data → Regions"):
        fetch_osm(TILE, "colombia")


def test_old_unversioned_osm_data_is_not_used(sandbox):
    """A file the former Overpass fallback wrote has no version: never built from."""
    from forest.fetch.osm import OsmFileMissing, fetch_osm

    (TILE.cache / "osm.json").write_text('{"elements": []}')
    with pytest.raises(OsmFileMissing):
        fetch_osm(TILE, "colombia")


def test_current_osm_data_builds_without_the_country_file(sandbox):
    """The country file was deleted to save space: the tile's own current data is enough."""
    from forest.fetch.osm import fetch_osm
    from forest.fetch.osm_pbf import VERSION

    (TILE.cache / "osm.json").write_text(json.dumps({"version": VERSION, "elements": []}))
    assert fetch_osm(TILE, "colombia") == TILE.cache_path / "osm.json"


def test_a_tile_whose_data_failed_is_not_built(sandbox):
    import forest.app.api as api
    from forest.jobs import JobRecord

    a = api.Api.__new__(api.Api)
    a.job = api.Job()
    a.job.record = JobRecord.new(["+06-076"], "medium", "colombia", 1)
    a.job.queue = ["+06-076"]
    a.job.record.on_event("step", {"tile": "+06-076", "step": "data", "status": "failed",
                                   "note": "+06-076: OpenStreetMap country file missing"})
    assert a._data_failed("+06-076")
    assert a.job.failed == ["+06-076"] and a.job.queue == []
    st = a.job.record.d["steps"]["+06-076"]
    assert st["data"]["status"] == "failed" and "country file missing" in st["data"]["note"]
    assert st["classify"]["status"] == "skipped"          # not "failed": the data step is the reason
    assert not a.job.record.d["results"]["+06-076"]["ok"]


# ---------- N3: sea-only tiles ----------

def test_sea_only_tiles_are_refused(sandbox):
    import forest.app.api as api
    from forest import config

    a = api.Api.__new__(api.Api)
    a.job = api.Job()
    a.cfg = config.load()
    land = Tile.parse("+06-076")
    gs = sandbox.xplane / "Global Scenery" / "X-Plane 12 Global Scenery" / "Earth nav data" / land.folder
    gs.mkdir(parents=True, exist_ok=True)
    (gs / "+06-076.dsf").write_bytes(b"x")
    r = a.start_build(["+06-076", "+06-077"], "medium")
    assert not r["ok"] and "+06-077" in r["error"] and "sea only" in r["error"]
    assert "+06-076" not in r["error"]


# ---------- N5: one window per data folder ----------

def test_a_second_window_cannot_take_the_data_folder(sandbox):
    from forest.app import single
    from forest.worker import run

    assert single.acquire(sandbox.root)
    try:
        msgs = list(run("tests.worker_tasks:try_app_lock", root=str(sandbox.root)))
        assert msgs[-1]["rc"] == 1                       # the other process is turned away
    finally:
        single.release()
    msgs = list(run("tests.worker_tasks:try_app_lock", root=str(sandbox.root)))
    assert msgs[-1]["rc"] == 0                           # free again once this one lets go


# ---------- N1: recent peaks only ----------

def test_auto_workers_peak_comes_from_recent_builds(sandbox):
    from forest import history, resources

    for _ in range(40):
        history.record("build", tile="+06-076", seconds=60, peak_gb=9.0)     # old, heavy code
    for _ in range(resources.RECENT_BUILDS):
        history.record("build", tile="+06-076", seconds=60, peak_gb=3.0)     # today's builds
    assert resources.peak_gb() == 3.0


# ---------- N4: job views under the lock ----------

def test_job_views_are_copies_taken_under_the_lock(sandbox):
    from forest.jobs import JobRecord

    rec = JobRecord.new(["+06-076", "+06-075", "+06-074"], "medium", "colombia", 3)
    stop = threading.Event()
    errors = []

    def finish_tiles():
        i = 0
        while not stop.is_set():
            rec.tile_finished(["+06-076", "+06-075", "+06-074"][i % 3], 0, [], {"forest_km2": i})
            i += 1

    def read():
        try:
            for _ in range(300):
                full = rec.full()
                full["results"]["x"] = {}                    # a copy: the record is untouched
        except RuntimeError as exc:                          # "dictionary changed size…"
            errors.append(exc)

    w = threading.Thread(target=finish_tiles)
    w.start()
    try:
        read()
    finally:
        stop.set()
        w.join()
    assert not errors and "x" not in rec.d["results"]


# ---------- N6: dev server accepts JSON requests only ----------

def test_dev_server_refuses_simple_cross_site_requests():
    from http.server import ThreadingHTTPServer

    from forest.app import devserver

    class Fake:
        def delete_tiles(self, *a):
            return {"ok": True, "deleted": list(a)}

    old, devserver.API = devserver.API, Fake()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), devserver.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/delete_tiles"
    try:
        plain = urllib.request.Request(url, data=b'["+06-076"]', headers={"Content-Type": "text/plain"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(plain, timeout=10)
        assert e.value.code == 415
        ok = urllib.request.Request(url, data=b'["+06-076"]', headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(ok, timeout=10) as r:
            assert json.loads(r.read()) == {"ok": True, "deleted": ["+06-076"]}
    finally:
        srv.shutdown()
        devserver.API = old


# ---------- N7: TOML strings ----------

@pytest.mark.parametrize("s", ["plain", r'a "quote" and \ backslash', "two\nlines\tand tab",
                               "control\x01\x1f\x7f", "Bogotá ñ", "tree 🌳", "O'Neil"])
def test_toml_strings_round_trip(s):
    from forest.app.settings import _q
    from forest.config import toml_str

    assert tomllib.loads("k = " + toml_str(s))["k"] == s
    assert tomllib.loads("k = " + _q(s))["k"] == s
