"""Country-wide downloads (served from a local test server), download jobs and the Data view."""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from forest import jobs
from forest.fetch import sources


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "country.osm.pbf").write_bytes(b"x" * 3_000_000)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_download_writes_file_and_meta(server, sandbox):
    dest = sandbox.cache / "osm" / "country.osm.pbf"
    seen = []
    sources.download(f"{server}/country.osm.pbf", dest, lambda d, t: seen.append((d, t)))
    assert dest.stat().st_size == 3_000_000 and not dest.with_name(dest.name + ".part").exists()
    meta = sources.read_meta(dest)
    assert meta["size"] == 3_000_000 and meta["remote_date"] and meta["downloaded"]
    assert seen[-1] == (3_000_000, 3_000_000)
    st = sources.status(dest, "u")
    assert st["exists"] and st["mb"] == 3.0


def test_failed_download_leaves_nothing(server, sandbox):
    dest = sandbox.cache / "osm" / "missing.pbf"
    with pytest.raises(OSError):
        sources.download(f"{server}/missing.pbf", dest)
    assert not dest.exists() and not dest.with_name(dest.name + ".part").exists()


def test_check_update_compares_server_date(server, sandbox, monkeypatch):
    dest = sandbox.cache / "osm" / "country.osm.pbf"
    url = f"{server}/country.osm.pbf"
    sources.download(url, dest)
    assert sources.check_update(dest, url)["newer"] is False
    ours = sources.read_meta(dest)["remote_date"]
    monkeypatch.setattr(sources, "remote_info", lambda u, timeout=30: {"remote_date": ours + 86400, "remote_size": 9})
    assert sources.check_update(dest, url)["newer"] is True


def test_download_job_has_only_the_data_step(sandbox):
    rec = jobs.JobRecord.new(["+06-076", "+06-075"], "", "colombia", 1, kind="data")
    assert rec.step_names == ["data"] and set(rec.d["steps"]["+06-076"]) == {"data"}
    rec.step("+06-076", "data", "running")
    rec.step("+06-075", "data", "failed", "no network")
    rec.finish(rc=0)
    full = jobs.JobRecord.load(rec.id).full()
    assert full["kind"] == "data" and full["kind_label"] == "Download data"
    assert full["built"] == 1 and full["failed"] == 1 and full["status"] == "partial"
    assert full["step_names"] == ["data"] and full["progress"] == 1.0


def test_old_job_records_still_load(sandbox):
    rec = jobs.JobRecord.new(["+06-076"], "medium", "colombia", 1)
    del rec.d["kind"], rec.d["step_names"]          # a record written before job kinds existed
    rec.save()
    full = jobs.JobRecord.load(rec.id).full()
    assert full["kind"] == "build" and full["step_names"] == jobs.STEPS


def test_data_status_lists_tiles_and_stale_osm(sandbox):
    import os

    api = pytest.importorskip("forest.app.api")
    from forest.fetch import osm_pbf
    pbf = osm_pbf.PBF_DIR / "colombia-latest.osm.pbf"
    pbf.parent.mkdir(parents=True, exist_ok=True)
    pbf.write_bytes(b"pbf")
    (pbf.with_name(pbf.name + ".json")).write_text(json.dumps({"remote_date": 2_000_000_000, "downloaded": 2_000_000_000}))
    d = sandbox.cache / "tiles" / "+06-076"
    d.mkdir(parents=True)
    for n in ("worldcover.tif", "treecover2000.tif", "lossyear.tif"):
        (d / n).write_bytes(b"t")
    (d / "osm.json").write_text('{"version": %d, "elements": []}' % osm_pbf.VERSION)
    os.utime(d / "osm.json", (1_900_000_000, 1_900_000_000))         # extracted from an older file
    a = api.Api.__new__(api.Api)                                      # no window, no catalog needed
    a.job = api.Job()
    st = a.data_status()
    assert st["osm"]["colombia"]["exists"] and not st["ecoregions"]["exists"]
    (t,) = st["tiles"]
    assert t["tile"] == "+06-076" and t["satellite"] and t["osm"] and t["osm_stale"] and not t["built"]
