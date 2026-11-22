"""Saved build jobs (the Works view)."""

from forest import jobs
from forest.jobs import JobRecord


def test_job_lifecycle_from_log_lines(sandbox):
    rec = JobRecord.new(["+06-076", "+06-075"], "medium", "colombia", 2)
    for t in rec.d["tiles"]:
        rec.step(t, "data", "running")
    rec.track("", "+06-076: rasters ready (18 MB)")
    rec.track("", "OSM: one pass for 2 tile(s) in 140 s")
    assert rec.d["steps"]["+06-076"]["data"]["status"] == "done"
    rec.track("+06-076", "[    0.0s] rasters cached")
    assert rec.d["steps"]["+06-076"]["classify"]["status"] == "running"
    rec.track("+06-076", "[   24.0s] classified: 19,715,601 tree cells")
    rec.track("+06-076", "[   44.0s] vectorised 95191 polygons")
    rec.track("+06-076", "[   48.0s] wrote C:\\out\\studio_colombia\\+06-076.txt")
    assert rec.d["steps"]["+06-076"]["compile"]["status"] == "running"
    rec.tile_finished("+06-076", 0, [], {"forest_km2": 6000.0, "estimated_trees": 25_000_000,
                                         "polygons": 90_000, "dsf_mb": 13.0})
    rec.track("+06-075", "[    0.0s] rasters cached")
    rec.tile_finished("+06-075", 1, ["Traceback …", "ERROR: too many intervals in polygon"], None)
    st = rec.d["steps"]["+06-075"]
    assert st["classify"]["status"] == "failed" and "too many intervals" in st["classify"]["note"]
    assert st["vectorise"]["status"] == "skipped" and st["compile"]["status"] == "skipped"
    rec.finish()
    assert rec.d["status"] == "partial"
    rep = rec.report()
    assert rep["built"] == 1 and rep["failed"] == 1 and rep["forest_km2"] == 6000.0
    assert rec.progress() == 1.0


def test_list_load_delete(sandbox):
    a = JobRecord.new(["+06-076"], "medium", "colombia", 1)
    a.log("hello")
    a.finish()
    listed = jobs.list_jobs()
    assert [j["id"] for j in listed] == [a.id]
    assert jobs.read_log(a.id) == ["hello"]
    assert JobRecord.load(a.id).d["status"] in ("done", "failed")
    assert not jobs.delete("../../etc/passwd")          # ids are validated
    assert jobs.delete(a.id) and jobs.list_jobs() == []


def test_interrupted_jobs_are_marked(sandbox):
    a = JobRecord.new(["+06-076"], "medium", "colombia", 1)   # never finished
    assert jobs.mark_interrupted() == 1
    b = JobRecord.load(a.id)
    assert b.d["status"] == "interrupted"
    assert all(s["status"] == "skipped" for s in b.d["steps"]["+06-076"].values())


def test_cancel_marks_remaining_steps_skipped(sandbox):
    a = JobRecord.new(["+06-076", "+06-075"], "medium", "colombia", 1)
    a.tile_finished("+06-076", 0, [], {})
    a.finish(cancelled=True)
    assert a.d["status"] == "cancelled"
    assert all(s["status"] == "skipped" for s in a.d["steps"]["+06-075"].values())


def test_concurrent_saves_never_fail(sandbox):
    # Parallel builds finish together and all save the same record (raced on Windows before).
    import threading

    rec = JobRecord.new(["+06-076", "+06-075", "+06-074"], "medium", "colombia", 3)
    errors = []

    def go():
        try:
            for _ in range(30):
                rec.save()
                JobRecord.load(rec.id)      # the Build queue view reads it meanwhile
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert JobRecord.load(rec.id).id == rec.id
