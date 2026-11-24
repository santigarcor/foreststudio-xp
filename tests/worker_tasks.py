"""Small tasks for tests/test_worker.py, run inside real worker processes."""

import os
import time


def talk(n: int = 3) -> int:
    from forest import events
    events.emit("sat_start", n=n)
    for i in range(n):
        events.emit("download", what="OSM", mb=i, pct=i * 10)
    print("a stray print line")
    events.log("done talking")
    return 3


def fail() -> int:
    raise ValueError("seasonal assets not allowed")


def crash() -> int:
    os._exit(7)


def sleep(seconds: float = 60) -> int:
    from forest import events
    events.log("sleeping")
    time.sleep(seconds)
    return 0
