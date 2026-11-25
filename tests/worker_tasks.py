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


def try_app_lock(root: str) -> int:
    """0 = this (second) process got the data folder's app lock, 1 = another window holds it."""
    from pathlib import Path

    from forest.app import single
    return 0 if single.acquire(Path(root)) else 1
