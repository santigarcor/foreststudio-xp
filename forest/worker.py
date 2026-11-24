"""Run one task (forest.tasks) in its own fresh process and get its progress as messages.

Why a process per task, not a thread: a tile build holds 2–4 GB at its peak and the libraries it
uses (GDAL, numpy, shapely) don't give memory back reliably; ending the process after each tile
does. A crash (or a GDAL abort) only fails that tile. Cancel stops the process and its DSFTool.

Messages (dicts) arrive in order over a pipe: every forest.events event as {"event": name, **data},
stray prints as {"event": "log", "text": ...}, then exactly one of
  {"event": "exit", "rc": int}                         the task returned
  {"event": "error", "type", "message", "traceback"}   the task raised (followed by exit rc 1)
If the process dies without saying anything (killed, crashed), the parent makes up
{"event": "exit", "rc": <exit code or 1>, "crashed": True}.

No console windows (rule 4): on Windows the worker runs on pythonw.exe (no console), and the
DSFTool it starts goes through forest.proc as before.
"""

from __future__ import annotations

import io
import multiprocessing as mp
import sys
import threading
from typing import Iterator

_CTX = mp.get_context("spawn")   # the same on every system; fork would copy the app's threads


class _LineSink(io.TextIOBase):
    """stdout/stderr of the worker: each complete line becomes a log message."""

    def __init__(self, send) -> None:
        self._send = send
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._send({"event": "log", "text": line.rstrip()})
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._send({"event": "log", "text": self._buf.rstrip()})
        self._buf = ""


def _resolve(task: str):
    """A forest.tasks function by name, or "module:function" (tests)."""
    import importlib

    from forest import tasks
    if ":" in task:
        mod, fn = task.split(":", 1)
        return getattr(importlib.import_module(mod), fn)
    return getattr(tasks, task)


def _child(conn, task: str, kwargs: dict) -> None:
    """Worker process entry point (must stay importable at module level for spawn)."""
    import traceback
    import warnings

    from forest import events

    lock = threading.Lock()   # downloads run in threads (satellite maps 4 at a time)

    def send(msg: dict) -> None:
        with lock:
            conn.send(msg)

    warnings.filterwarnings("ignore")
    events.set_sink(lambda ev, data: send({"event": ev, **data}))
    sys.stdout = sys.stderr = _LineSink(send)
    rc = 1
    try:
        rc = int(_resolve(task)(**kwargs) or 0)
    except BaseException as exc:  # noqa: BLE001 - everything goes back to the app
        send({"event": "error", "type": type(exc).__name__, "message": str(exc)[:2000],
              "traceback": traceback.format_exc()[-6000:]})
        rc = 1
    finally:
        try:
            sys.stdout.flush()
            send({"event": "exit", "rc": rc})
        finally:
            conn.close()


def _worker_python() -> str | None:
    """pythonw.exe next to python.exe on Windows (no console window); None = multiprocessing's
    default (the frozen app itself, or python on macOS/Linux)."""
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        return None
    from pathlib import Path
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    if exe.name.lower() == "python.exe" and w.exists():
        return str(w)
    return None


class Worker:
    """One task in one fresh process. Iterate `messages()` to follow it; `stop()` cancels it."""

    def __init__(self, task: str, **kwargs) -> None:
        self.task, self.kwargs = task, kwargs
        self.process = None
        self._conn = None

    def start(self) -> "Worker":
        exe = _worker_python()
        if exe:
            _CTX.set_executable(exe)
        recv, send = _CTX.Pipe(duplex=False)
        self.process = _CTX.Process(target=_child, args=(send, self.task, self.kwargs), daemon=True,
                                    name=f"forest-{self.task}")
        self.process.start()
        send.close()   # the child holds the other end: EOF when it exits
        self._conn = recv
        return self

    def messages(self) -> Iterator[dict]:
        exited = False
        try:
            while True:
                try:
                    msg = self._conn.recv()
                except (EOFError, OSError):
                    break
                if msg.get("event") == "exit":
                    exited = True
                yield msg
        finally:
            self._conn.close()
            self.process.join(timeout=30)
        if not exited:
            code = self.process.exitcode
            yield {"event": "exit", "rc": code if code not in (None, 0) else 1, "crashed": True}

    def stop(self) -> None:
        """Stop the worker and everything it started (DSFTool)."""
        if self.process is None or not self.process.is_alive():
            return
        from forest import proc
        proc.terminate_pid(self.process.pid)

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None


def run(task: str, **kwargs) -> Iterator[dict]:
    """Start a worker and follow it (convenience for tests and scripts)."""
    yield from Worker(task, **kwargs).start().messages()
