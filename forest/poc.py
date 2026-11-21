"""Phase 0 proof of concept: three forest test patches west of SKRG (Rionegro).

Each patch uses a different default asset side by side, with a matching
sim/exclude_for so the default forest under it is removed. Compare them in the sim.
"""

from __future__ import annotations

from pathlib import Path

from forest.assets.catalog import seasonal_assets
from forest.dsf.text import ForestPolygon, OverlayTile, season_rasters

# ~1 km squares, about 1.5-2 km west of SKRG runway 18/36 (lon -75.423).
# Evergreen-only (no EXPORT_SEASON variants): the base mesh puts Rionegro in
# "winter" from day 136 to day 40, so seasonal assets show bare trees most of the year.
PATCHES = [
    ("lib/vegetation/forests/broadleaves/very_hot.for", (-75.455, 6.150)),  # SW
    ("lib/g8/rainforest_vhot_rain.for", (-75.445, 6.150)),  # SE
    ("lib/vegetation/forests/conifers/hot_dry.for", (-75.455, 6.160)),  # NW pine/cypress
]
SIZE = 0.009  # degrees ≈ 1 km at this latitude


def build(out_root: Path, density: int = 200, season: str | None = None) -> Path:
    tile = OverlayTile(lat=6, lon=-76, agent="forest-studio poc")
    if season:
        # Experiment: does X-Plane honour season rasters from an overlay DSF?
        tile.rasters = season_rasters(season)
    seasonal = set(seasonal_assets())
    for asset, (w, s) in PATCHES:
        if asset in seasonal:
            raise ValueError(f"{asset} has seasonal variants; use an evergreen asset in Colombia")
        e, n = w + SIZE, s + SIZE
        tile.polygons.append(
            ForestPolygon(asset=asset, exterior=[(w, s), (e, s), (e, n), (w, n)], density=density)
        )
        tile.exclude_for.append((w, s, e, n))
    return tile.write(out_root)
