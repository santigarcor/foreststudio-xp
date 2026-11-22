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
    r = a.start_build(["+06-076"], "medium", "colombia", False, "")
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
