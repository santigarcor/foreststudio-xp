"""Parse X-Plane library.txt files (read-only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

VISIBILITY = {"PUBLIC", "PRIVATE", "SEMI_DEPRECATED", "DEPRECATED"}


@dataclass
class Export:
    virtual: str
    real: str  # relative to the library folder
    kind: str  # EXPORT | EXPORT_SEASON | EXPORT_EXCLUDE | EXPORT_EXTEND ...
    season: str | None
    region: str
    visibility: str


@dataclass
class Library:
    folder: Path
    exports: list[Export] = field(default_factory=list)

    def by_virtual(self) -> dict[str, list[Export]]:
        out: dict[str, list[Export]] = {}
        for e in self.exports:
            out.setdefault(e.virtual, []).append(e)
        return out


def parse_library(path: Path) -> Library:
    lib = Library(folder=path.parent)
    region = "all"
    visibility = "PUBLIC"
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        cmd = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        if cmd in VISIBILITY:
            visibility = cmd
        elif cmd == "REGION":
            region = rest.strip()
        elif cmd == "EXPORT_SEASON":
            season, rest2 = rest.split(None, 1)
            virtual, real = rest2.split(None, 1)
            lib.exports.append(Export(virtual, real.strip(), cmd, season, region, visibility))
        elif cmd.startswith("EXPORT"):
            virtual, real = rest.split(None, 1)
            lib.exports.append(Export(virtual, real.strip(), cmd, None, region, visibility))
    return lib
