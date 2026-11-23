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


def run_measured(cmd: list[str]) -> tuple[subprocess.CompletedProcess, float | None]:
    """Like run(), plus the program's peak memory in GB (None if it can't be read)."""
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    out, err = p.communicate()
    peak = None
    try:
        if sys.platform == "win32":
            peak = _win_peak_bytes(p)
        else:
            import resource

            # Largest child so far; DSFTool is the only big child of a build process.
            kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            peak = kb if sys.platform == "darwin" else kb * 1024   # bytes on macOS, KB on Linux
    except Exception:  # noqa: BLE001 - measuring is best effort
        peak = None
    return subprocess.CompletedProcess(cmd, p.returncode, out, err), (peak / 1e9 if peak else None)


def _win_peak_bytes(p: subprocess.Popen) -> int | None:
    """Peak working set of a finished process, read through its still-open handle."""
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    fn = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    if not fn(int(p._handle), ctypes.byref(pmc), pmc.cb):
        return None
    return int(pmc.PeakWorkingSetSize)


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
