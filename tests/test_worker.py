"""App workers (forest.worker): one fresh process per task, progress as messages, errors with
their traceback, a crash fails only that task, cancel stops it."""

import sys
import time

from forest.worker import Worker, _worker_python, run


def test_events_prints_and_exit_code_arrive_in_order():
    msgs = list(run("tests.worker_tasks:talk", n=3))
    kinds = [m["event"] for m in msgs]
    assert kinds[0] == "sat_start" and kinds.count("download") == 3 and kinds[-1] == "exit"
    assert {"event": "log", "text": "a stray print line"} in msgs
    assert msgs[-1]["rc"] == 3 and not msgs[-1].get("crashed")


def test_an_exception_comes_back_with_its_traceback():
    msgs = list(run("tests.worker_tasks:fail"))
    err = next(m for m in msgs if m["event"] == "error")
    assert err["type"] == "ValueError" and "seasonal" in err["message"]
    assert "worker_tasks.py" in err["traceback"]
    assert msgs[-1] == {"event": "exit", "rc": 1}


def test_a_crash_fails_only_that_task():
    msgs = list(run("tests.worker_tasks:crash"))
    assert msgs[-1]["event"] == "exit" and msgs[-1]["rc"] == 7 and msgs[-1]["crashed"]
    assert list(run("tests.worker_tasks:talk", n=1))[-1]["rc"] == 3   # the next one is fine


def test_stop_ends_the_worker():
    w = Worker("tests.worker_tasks:sleep", seconds=60).start()
    it = w.messages()
    assert next(it)["text"] == "sleeping"
    t0 = time.time()
    w.stop()
    rest = list(it)
    assert rest[-1]["event"] == "exit" and rest[-1]["crashed"]
    assert time.time() - t0 < 20


def test_workers_have_no_console_on_windows():
    exe = _worker_python()
    if sys.platform == "win32" and sys.executable.lower().endswith("python.exe"):
        assert exe and exe.lower().endswith("pythonw.exe")
    else:
        assert exe is None


def test_the_app_runner_follows_a_worker(sandbox):
    """Api._task: log lines, phase and exit code from a real worker."""
    import forest.app.api as api
    from forest.jobs import JobRecord

    a = api.Api.__new__(api.Api)
    a.job = api.Job()
    a.job.record = JobRecord.new(["+06-076"], "medium", "colombia", 1)
    tail: list[str] = []
    rc = a._task("tests.worker_tasks:talk", {"n": 2}, tail=tail)
    assert rc == 3
    log = [ln.split("\t", 1)[1] for ln in a.job.log]
    assert "satellite maps: downloading for 2 tile(s)" in log and "done talking" in log
    assert a.job.progress == {"what": "OSM", "mb": 1, "pct": 10}
    assert tail[-1] == "done talking"
    rc = a._task("tests.worker_tasks:fail", {}, tail=tail)
    assert rc == 1 and tail[-1].startswith("ValueError: seasonal")
