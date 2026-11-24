"""The app's job runner: one job at a time, and a tile error never vanishes."""

import threading

import forest.app.api as api


def _api() -> api.Api:
    a = api.Api.__new__(api.Api)          # no window, no catalog, no migration needed
    a.job = api.Job()
    return a


def test_only_one_job_can_claim_the_runner():
    job = api.Job()
    wins = []
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        wins.append(job.claim())

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1


def test_start_build_refuses_while_running(sandbox, monkeypatch):
    a = _api()
    a.job.running = True
    r = a.start_build(["+06-076"], "medium")
    assert not r["ok"] and "already running" in r["error"]
    r = a.start_data_job("tiles", ["+06-076"])
    assert not r["ok"]


def test_error_in_a_build_thread_marks_the_tile_failed(sandbox, monkeypatch):
    from forest.jobs import JobRecord

    a = _api()
    a.job.record = JobRecord.new(["+06-076"], "medium", "colombia", 1)
    a.job.queue = ["+06-076"]

    def boom(*args, **kw):
        raise RuntimeError("stats file is broken")

    monkeypatch.setattr(a, "_step", boom)
    a._build_one("+06-076", "medium", "colombia")   # must not raise
    assert a.job.failed == ["+06-076"] and not a.job.active
    assert any("stats file is broken" in ln for ln in a.job.log)
    res = a.job.record.d["results"]["+06-076"]
    assert not res["ok"] and "stats file is broken" in res["error"]


def test_download_progress_lines_are_parsed():
    from forest.app.api import _DOWNLOAD_RE

    m = _DOWNLOAD_RE.match("download OSM: 57 MB 45%\n")
    assert (m["what"], m["mb"], m["pct"]) == ("OSM", "57", "45")
    m = _DOWNLOAD_RE.match("download ecoregions: 12 MB")
    assert m["what"] == "ecoregions" and m["pct"] is None
    assert not _DOWNLOAD_RE.match("[+06-076] download OSM: 1 MB 2%")


def test_data_phase_follows_the_fetch_log(sandbox):
    from forest.app.api import Api, Job
    from forest.jobs import JobRecord

    a = Api.__new__(Api)
    a.job = Job()
    a.job.record = JobRecord.new(["+25-081", "+26-081"], "medium", "Florida", 1)
    a._data_phase("OSM country file missing: downloading https://d/north-america/us/florida-latest.osm.pbf")
    assert a.job.phase == "Downloading the OpenStreetMap file florida-latest.osm.pbf…"
    assert a.job.record.d["steps"]["+25-081"]["data"]["note"] == a.job.phase      # Build queue too
    a._data_phase("satellite maps: downloading for 2 tile(s)")
    a._data_phase("+25-081: rasters ready (40 MB)")
    assert a.job.phase == "Satellite maps: 1 of 2 tile(s) done"
    a._data_phase("reading OpenStreetMap: florida-latest.osm.pbf (590 MB) for 2 tile(s)")
    assert a.job.phase.startswith("Reading OpenStreetMap (florida-latest.osm.pbf, 590 MB) for 2 tile(s)")
    assert a.job.record.d["steps"]["+26-081"]["data"]["note"] == a.job.phase
    a._data_phase("OSM: one pass for 2 tile(s) in 95 s")
    assert a.job.phase is None
