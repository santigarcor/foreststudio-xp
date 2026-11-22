"""Start helper programs (DSFTool, our own CLI) without ever opening a console window."""

from __future__ import annotations

import subprocess
import sys

# On Windows a console program started from a windowless app (pythonw.exe, the desktop app)
# gets a new visible console unless told otherwise.
HIDDEN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _startupinfo():
    if sys.platform != "win32":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run, hidden. Output is captured unless the caller says otherwise."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, creationflags=HIDDEN, startupinfo=_startupinfo(),
                          stdin=subprocess.DEVNULL, **kw)


def popen(cmd: list[str], **kw) -> subprocess.Popen:
    """subprocess.Popen, hidden."""
    return subprocess.Popen(cmd, creationflags=HIDDEN, startupinfo=_startupinfo(),
                            stdin=subprocess.DEVNULL, **kw)


def terminate_tree(p: subprocess.Popen) -> None:
    """Stop a process and everything it started. Popen.terminate() alone leaves children
    running on Windows (a cancelled build kept its DSFTool compiling)."""
    if p.poll() is not None:
        return
    try:
        import psutil

        children = psutil.Process(p.pid).children(recursive=True)
    except Exception:  # noqa: BLE001 - psutil missing or the process just ended
        children = []
    for c in children:
        try:
            c.terminate()
        except Exception:  # noqa: BLE001 - already gone
            pass
    try:
        p.terminate()
    except OSError:
        pass


def python_exe() -> str:
    """A console-less Python for child processes: python.exe works with pipes and is started
    hidden; pythonw.exe would lose stdout on some systems."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[: -len("pythonw.exe")] + "python.exe"
        try:
            import os
            if os.path.exists(cand):
                return cand
        except OSError:
            pass
    return exe
