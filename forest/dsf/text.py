"""Write overlay DSFs in DSFTool's text format.

Facts from the X-Plane "DSF Usage" spec:
- Forest polygon param = density (0-255) + fill code (0 solid, 256 line, 512 points).
- Exteriors wind counter-clockwise, holes clockwise; no self-intersection.
- sim/exclude_for takes a rectangle "west/south/east/north".

The text keywords (POLYGON_DEF, BEGIN_POLYGON, ...) follow DSFTool's -dsf2text output.
TODO(phase0): confirm against a DSFTool decode of a real tile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

Ring = list[tuple[float, float]]  # (lon, lat)

FILL_SOLID, FILL_LINE, FILL_POINTS = 0, 256, 512


def signed_area(ring: Ring) -> float:
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        a += x1 * y2 - x2 * y1
    return a / 2.0


def oriented(ring: Ring, ccw: bool) -> Ring:
    ring = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    return ring if (signed_area(ring) > 0) == ccw else ring[::-1]


@dataclass
class ForestPolygon:
    asset: str  # virtual path, e.g. lib/vegetation/forests/broadleaves/warm.for
    exterior: Ring
    holes: list[Ring] = field(default_factory=list)
    density: int = 255  # 0-255
    fill: int = FILL_SOLID
    kind: str = ""  # build bookkeeping only ("town" = scattered town trees); not written


SEASON_RASTERS = ("spr1", "spr2", "sum1", "sum2", "fal1", "fal2", "win1", "win2")
# Matches the XP12 base mesh: 1 byte per cell, value * scale = day of year.
SEASON_SCALE = 1.994536


@dataclass
class Raster:
    name: str
    width: int
    height: int
    data: bytes
    bpp: int = 1
    flags: int = 6
    scale: float = 1.0
    offset: float = 0.0


def season_rasters(always: str, size: int = 128) -> list[Raster]:
    """Rasters that hold one season all year.

    Base-mesh tiles encode "all year" as <season>1=365, <season>2=0 with every other
    value 0 (seen in +06-076, see PLAN.md).
    """
    days = {n: 0 for n in SEASON_RASTERS}
    days[f"{always}1"] = 365
    out = []
    for n in SEASON_RASTERS:
        v = round(days[n] / SEASON_SCALE)
        out.append(Raster(n, size, size, bytes([v]) * (size * size), scale=SEASON_SCALE))
    return out


@dataclass
class OverlayTile:
    lat: int  # south-west corner
    lon: int
    polygons: list[ForestPolygon] = field(default_factory=list)
    exclude_for: list[tuple[float, float, float, float]] = field(default_factory=list)  # w,s,e,n
    rasters: list[Raster] = field(default_factory=list)
    agent: str = "forest-studio"

    @property
    def name(self) -> str:
        return f"{self.lat:+03d}{self.lon:+04d}"

    @property
    def folder(self) -> str:
        return f"{self.lat // 10 * 10:+03d}{self.lon // 10 * 10:+04d}"

    def to_text(self, raster_dir: Path | None = None) -> str:
        out = ["A", f"800 {self.agent}", "DSF2TEXT", ""]
        props = [
            ("sim/planet", "earth"),
            ("sim/overlay", "1"),
            ("sim/creation_agent", self.agent),
        ]
        for w, s, e, n in self.exclude_for:
            props.append(("sim/exclude_for", f"{w:.7f}/{s:.7f}/{e:.7f}/{n:.7f}"))
        props += [
            ("sim/west", str(self.lon)),
            ("sim/east", str(self.lon + 1)),
            ("sim/north", str(self.lat + 1)),
            ("sim/south", str(self.lat)),
        ]
        out += [f"PROPERTY {k} {v}" for k, v in props]
        out.append("")

        assets = sorted({p.asset for p in self.polygons})
        index = {a: i for i, a in enumerate(assets)}
        out += [f"POLYGON_DEF {a}" for a in assets]
        out.append("")

        if self.rasters:
            if raster_dir is None:
                raise ValueError("raster_dir required when the tile has rasters")
            out += [f"RASTER_DEF {r.name}" for r in self.rasters]
            for r in self.rasters:
                raw = raster_dir / f"{self.name}.{r.name}.raw"
                raw.write_bytes(r.data)
                out.append(
                    f"RASTER_DATA version=1 bpp={r.bpp} flags={r.flags} width={r.width} "
                    f"height={r.height} scale={r.scale:.6f} offset={r.offset:.6f} {raw}"
                )
            out.append("")

        for p in self.polygons:
            if not 0 <= p.density <= 255:
                raise ValueError(f"density out of range: {p.density}")
            out.append(f"BEGIN_POLYGON {index[p.asset]} {p.density + p.fill} 2")
            for ring, ccw in [(p.exterior, True)] + [(h, False) for h in p.holes]:
                out.append("BEGIN_WINDING")
                for lon, lat in oriented(ring, ccw):
                    self._check(lon, lat)
                    out.append(f"POLYGON_POINT {lon:.9f} {lat:.9f}")
                out.append("END_WINDING")
            out.append("END_POLYGON")
        return "\n".join(out) + "\n"

    def _check(self, lon: float, lat: float) -> None:
        if not (self.lon <= lon <= self.lon + 1 and self.lat <= lat <= self.lat + 1):
            raise ValueError(f"point {lon},{lat} outside tile {self.name}")

    def write(self, scenery_root: Path) -> Path:
        path = scenery_root / "Earth nav data" / self.folder / f"{self.name}.txt"
        return self.write_to(path)

    def write_to(self, path: Path) -> Path:
        """Write the DSF text to an explicit file (raster .raw files go next to it)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_text(raster_dir=path.parent), encoding="utf-8")
        return path
