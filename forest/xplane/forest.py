"""Read the header and tree list of a .for file without loading mesh data."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ForestInfo:
    path: str
    spacing: tuple[float, float] | None = None
    random: tuple[float, float] | None = None
    lod: float | None = None
    seasonal_texture: bool = False
    skip_surfaces: list[str] = field(default_factory=list)
    meshes: list[str] = field(default_factory=list)  # 3D tree mesh names
    mesh_tris: dict[str, int] = field(default_factory=dict)  # mesh name -> triangles
    tree_count: int = 0  # 2D billboard TREE lines

    @property
    def avg_tree_tris(self) -> float | None:
        """Mean triangles of the near-range 3D tree models (no LOD stand-ins, no forest-floor debris)."""
        t = [n for m, n in self.mesh_tris.items()
             if not m.removeprefix("3D_").removeprefix("3d_").startswith(("lod_", "ff_", "stones", "stump", "lp_"))]
        return sum(t) / len(t) if t else None

    @property
    def max_trees_per_km2(self) -> float | None:
        """Tree count at DSF density 255 (before user render settings)."""
        if not self.spacing or self.spacing[0] <= 0 or self.spacing[1] <= 0:
            return None
        return 1_000_000.0 / (self.spacing[0] * self.spacing[1])

    @property
    def species(self) -> list[str]:
        """Mesh names grouped to a species-ish label: 3D_kapok_04 -> kapok."""
        out: set[str] = set()
        for m in self.meshes:
            name = m.removeprefix("3D_").removeprefix("3d_")
            if name.startswith(("lod_", "ff_")):  # LOD stand-ins, forest-floor debris
                continue
            head = name.rsplit("_", 1)[0] if name[-1:].isdigit() else name
            out.add(head)
        return sorted(out)


def read_forest(path: Path) -> ForestInfo:
    info = ForestInfo(path=str(path))
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            c = line[:1]
            # Fast path: skip mesh vertex / index data, which is >99% of the file.
            if c == "V" and line.startswith("VERTEX"):
                continue
            if c == "I" and line.startswith("IDX"):
                continue
            parts = line.split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "SPACING" and len(parts) >= 3:
                info.spacing = (float(parts[1]), float(parts[2]))
            elif cmd == "RANDOM" and len(parts) >= 3:
                info.random = (float(parts[1]), float(parts[2]))
            elif cmd == "LOD" and len(parts) >= 2:
                info.lod = float(parts[1])
            elif cmd == "SEASONAL":
                info.seasonal_texture = True
            elif cmd == "SKIP_SURFACE" and len(parts) >= 2:
                info.skip_surfaces.append(parts[1])
            elif cmd == "MESH" and len(parts) >= 2:
                info.meshes.append(parts[1])
                # MESH <name> <near> <far> <vertex count> <index count> ...
                if len(parts) >= 6 and parts[5].isdigit():
                    info.mesh_tris[parts[1]] = int(parts[5]) // 3
            elif cmd in ("TREE", "TREE2"):
                info.tree_count += 1
    return info
