"""How many tile builds can run at once on this machine.

Memory is the real limit: one build peaks at ~3-5 GB (measured: +06-076 Medellín 4.6 GB,
+12-082 San Andrés 2.8 GB), while CPU use is one core per build.

    workers = min(physical cores - 1, (available RAM - reserve) / peak per build), at least 1
"""

from __future__ import annotations

import math

RESERVE_GB = 2.0           # left for Windows, the app and other programs
DEFAULT_PEAK_GB = 4.6      # measured on the heaviest test tile; replaced by build history
MAX_WORKERS = 8            # beyond this, disk and the single OSM file become the bottleneck
DOWNLOAD_WORKERS = 4       # satellite windows: network-bound, not CPU/RAM-bound


def system() -> dict:
    import psutil

    vm = psutil.virtual_memory()
    return {
        "cores_physical": psutil.cpu_count(logical=False) or 1,
        "cores_logical": psutil.cpu_count() or 1,
        "ram_total_gb": round(vm.total / 1e9, 1),
        "ram_available_gb": round(vm.available / 1e9, 1),
    }


RECENT_BUILDS = 30         # the peak comes from these: older builds (other code, other tiles) age out


def peak_gb() -> float:
    """Peak memory per build: 90th percentile of the last RECENT_BUILDS measured builds, else the
    default. Only recent builds count, so a high peak from older code doesn't keep Auto low forever."""
    from forest import history

    recent = [e["peak_gb"] for e in history.events()
              if e.get("kind") == "build" and isinstance(e.get("peak_gb"), (int, float))][-RECENT_BUILDS:]
    vals = sorted(recent)
    if not vals:
        return DEFAULT_PEAK_GB
    return max(1.0, vals[min(len(vals) - 1, math.floor(0.9 * len(vals)))])


def auto_workers() -> dict:
    s = system()
    peak = peak_gb()
    by_cpu = max(1, s["cores_physical"] - 1)
    by_ram = max(1, int((s["ram_available_gb"] - RESERVE_GB) // peak))
    n = max(1, min(by_cpu, by_ram, MAX_WORKERS))
    limit = "memory" if by_ram < by_cpu else "CPU cores"
    return {**s, "peak_gb": round(peak, 1), "by_cpu": by_cpu, "by_ram": by_ram, "workers": n,
            "limited_by": limit, "max": min(MAX_WORKERS, s["cores_physical"])}
