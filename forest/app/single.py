"""One app window per data folder.

Two windows on the same data folder would each think the other's running job was cut off
(jobs.mark_interrupted) and could build into the same pack at once. The first window holds an
OS-level lock on `<data>/app.lock` for as long as it runs; the operating system releases it when
the process ends, even after a crash, so a stale file never blocks a later start.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LOCK_NAME = "app.lock"
_held = None   # the open lock file, kept for the life of the process


def _try_lock(fh) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire(root: Path, wait_s: float = 0.0) -> bool:
    """Take the data folder's app lock (retrying for `wait_s`, e.g. while a restarting window
    closes). False = another window already has it."""
    global _held
    if _held is not None:
        return True
    root.mkdir(parents=True, exist_ok=True)
    fh = open(root / LOCK_NAME, "a+")
    end = time.monotonic() + wait_s
    while True:
        if _try_lock(fh):
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n")   # for people looking at the folder; the lock is what counts
            fh.flush()
            _held = fh
            return True
        if time.monotonic() >= end:
            fh.close()
            return False
        time.sleep(0.25)


def release() -> None:
    global _held
    if _held is not None:
        try:
            _held.close()   # closing the file releases the lock
        finally:
            _held = None


def tell_already_open() -> None:
    msg = ("ForestStudio XP is already open on this data folder.\n\n"
           "Use the window that is already open (look in the taskbar).")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "ForestStudio XP", 0x40)   # MB_ICONINFORMATION
            return
        except (AttributeError, OSError):
            pass
    print(msg, file=sys.stderr)
