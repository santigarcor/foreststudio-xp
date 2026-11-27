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

    monkeypatch.setattr(a, "_task", boom)
    a._build_one("+06-076", "medium", "colombia")   # must not raise
    assert a.job.failed == ["+06-076"] and not a.job.active
    assert any("stats file is broken" in ln for ln in a.job.log)
    res = a.job.record.d["results"]["+06-076"]
    assert not res["ok"] and "stats file is broken" in res["error"]


def test_events_read_as_plain_log_lines():
    from forest import events

    assert events.text("download", {"what": "OSM", "mb": 57, "pct": 45}) == "download OSM: 57 MB 45%"
    assert events.text("download", {"what": "ecoregions", "mb": 12, "pct": None}) == "download ecoregions: 12 MB"
    assert events.text("sat_tile", {"tile": "+06-076", "ok": True, "mb": 18.2}) == "+06-076: rasters ready (18 MB)"
    assert events.text("osm_done", {"n": 0, "seconds": 1}) == "OSM: already up to date"
    assert events.text("step", {"tile": "+06-076", "step": "classify", "status": "running"}) == ""


def test_data_phase_follows_the_fetch_events(sandbox):
    from forest.app.api import Api, Job
    from forest.jobs import JobRecord

    a = Api.__new__(Api)
    a.job = Job()
    a.job.record = JobRecord.new(["+25-081", "+26-081"], "medium", "Florida", 1)
    a._on_event("downloading", {"what": "OSM", "name": "the OpenStreetMap file florida-latest.osm.pbf",
                                "url": "https://d/north-america/us/florida-latest.osm.pbf"})
    assert a.job.phase == "Downloading the OpenStreetMap file florida-latest.osm.pbf…"
    assert a.job.record.d["steps"]["+25-081"]["data"]["note"] == a.job.phase      # Build queue too
    a._on_event("download", {"what": "OSM", "mb": 300, "pct": 45})
    assert a.job.progress == {"what": "OSM", "mb": 300, "pct": 45}
    assert a.job.phase == "Downloading the OpenStreetMap file florida-latest.osm.pbf… 45 % (300 MB)"
    a._on_event("sat_start", {"n": 2})
    a._on_event("sat_tile", {"tile": "+25-081", "ok": True, "mb": 40})
    assert a.job.phase == "Satellite maps: 1 of 2 tile(s) done"
    assert a.job.record.d["steps"]["+25-081"]["data"]["note"].startswith("satellite maps ready")
    a._on_event("osm_reading", {"file": "colombia-latest.osm.pbf", "mb": 335, "n": 1, "tiles": ["+25-081"]})
    assert "colombia" in a.job.record.d["steps"]["+25-081"]["data"]["note"]
    assert "colombia" not in a.job.record.d["steps"]["+26-081"]["data"]["note"]   # another file's tile
    a._on_event("osm_reading", {"file": "florida-latest.osm.pbf", "mb": 590, "n": 2})
    assert a.job.phase.startswith("Reading OpenStreetMap (florida-latest.osm.pbf, 590 MB) for 2 tile(s)")
    assert a.job.record.d["steps"]["+26-081"]["data"]["note"] == a.job.phase
    a._on_event("osm_done", {"n": 2, "seconds": 95})
    assert a.job.phase is None
    assert a.job.record.d["steps"]["+26-081"]["data"]["status"] == "done"


def test_tiles_are_grouped_by_shared_country_files(monkeypatch):
    from pathlib import Path

    from forest import regions

    files = {"+06-076": ["colombia"], "+06-075": ["colombia"], "+07-078": ["colombia", "panama"],
             "+09-080": ["panama"], "+30-082": ["florida"], "+40-010": []}
    monkeypatch.setattr(regions, "osm_files", lambda t, r=None: [Path(f"{f}.pbf") for f in files[t.name]])
    groups = regions.osm_groups(list(files), {})
    # the border tile joins Colombia and Panama into one group (one pass per file stays one pass)
    assert groups == [["+06-076", "+06-075", "+07-078", "+09-080"], ["+30-082"], ["+40-010"]]


def test_a_country_builds_while_another_country_file_is_read(sandbox, monkeypatch):
    """Two countries, two slots: Florida's tile is built while Colombia's file is still being
    read, and data steps + builds never use more than the slots."""
    import time

    from forest import regions
    from forest.jobs import JobRecord

    a = _api()
    names = ["+06-076", "+30-082"]
    owners = {"+06-076": "colombia", "+30-082": "florida"}
    monkeypatch.setattr(regions, "osm_groups", lambda n, o: [["+06-076"], ["+30-082"]])
    monkeypatch.setattr(a, "_tile_result", lambda n: {})
    a.job.running, a.job.queue = True, list(names)
    a.job.record = JobRecord.new(names, "medium", "Colombia, Florida", 2)
    seen, running, peak = [], [0], [0]
    lock = threading.Lock()

    def task(name, kwargs, tag="", tail=None, group=""):
        what = (name, tag or ",".join(kwargs.get("tiles", [])))
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            seen.append(("start",) + what)
        if name == "fetch":
            a._on_event("osm_reading", {"file": "x.pbf", "mb": 1, "n": 1, "tiles": kwargs["tiles"]}, group=group)
            time.sleep(0.6 if kwargs["tiles"] == ["+06-076"] else 0.05)
            a._on_event("osm_done", {"n": 1, "seconds": 1, "tiles": kwargs["tiles"]}, group=group)
        elif name == "build":
            time.sleep(0.05)
        with lock:
            running[0] -= 1
            seen.append(("end",) + what)
        return 0

    monkeypatch.setattr(a, "_task", task)
    a._run(names, "medium", owners, workers=2)
    assert seen.index(("end", "build", "+30-082")) < seen.index(("end", "fetch", "+06-076"))
    assert peak[0] <= 2 and sorted(a.job.done) == names and not a.job.running


def test_start_build_refuses_tiles_no_osm_file_covers(sandbox, monkeypatch):
    """Panama tiles inside Colombia's box (MPTO): no OSM file covers them, so no build."""
    from forest import regions

    a = _api()
    monkeypatch.setattr(a, "_xplane_missing", lambda: None, raising=False)
    monkeypatch.setattr(regions, "owners", lambda: {"+06-076": "colombia", "+09-080": "colombia"})
    monkeypatch.setattr(regions, "covered_tiles", lambda: frozenset({"+06-076"}))
    r = a.start_build(["+06-076", "+09-080"], "medium")
    assert not r["ok"] and "+09-080" in r["error"] and "+06-076" not in r["error"]
    assert "Data → Regions" in r["error"]
