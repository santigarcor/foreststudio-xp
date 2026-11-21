import pytest

from forest.dsf.text import (FILL_LINE, FILL_POINTS, FILL_SOLID, ForestPolygon, OverlayTile, oriented,
                             season_rasters, signed_area)

SQUARE_CW = [(-75.5, 6.5), (-75.5, 6.6), (-75.4, 6.6), (-75.4, 6.5)]  # clockwise


def test_signed_area_sign():
    assert signed_area(SQUARE_CW) < 0
    assert signed_area(SQUARE_CW[::-1]) > 0


def test_oriented_drops_closing_point_and_fixes_winding():
    closed = SQUARE_CW + [SQUARE_CW[0]]
    ccw = oriented(closed, ccw=True)
    assert len(ccw) == 4 and signed_area(ccw) > 0
    assert signed_area(oriented(closed, ccw=False)) < 0


def _parse(text):
    return [ln for ln in text.splitlines() if ln]


def test_overlay_text_structure_and_winding():
    hole = [(-75.47, 6.53), (-75.43, 6.53), (-75.43, 6.57), (-75.47, 6.57)]  # CCW, must be flipped
    t = OverlayTile(lat=6, lon=-76, agent="test")
    t.polygons.append(ForestPolygon("lib/a.for", SQUARE_CW, [hole], density=200))
    t.polygons.append(ForestPolygon("lib/b.for", SQUARE_CW, density=255, fill=FILL_POINTS))
    t.exclude_for.append(t_bounds := (-76, 6, -75, 7))
    lines = _parse(t.to_text())
    assert lines[0] == "A" and lines[2] == "DSF2TEXT"
    props = [ln for ln in lines if ln.startswith("PROPERTY")]
    # sim/west|east|north|south must come last among properties (DSFTool requirement)
    assert [p.split()[1] for p in props[-4:]] == ["sim/west", "sim/east", "sim/north", "sim/south"]
    assert f"PROPERTY sim/exclude_for {t_bounds[0]:.7f}/{t_bounds[1]:.7f}/{t_bounds[2]:.7f}/{t_bounds[3]:.7f}" in lines
    defs = [ln for ln in lines if ln.startswith("POLYGON_DEF")]
    assert defs == ["POLYGON_DEF lib/a.for", "POLYGON_DEF lib/b.for"]
    begins = [ln for ln in lines if ln.startswith("BEGIN_POLYGON")]
    assert begins[0] == "BEGIN_POLYGON 0 200 2"          # solid: param = density
    assert begins[1] == f"BEGIN_POLYGON 1 {255 + FILL_POINTS} 2"  # points: density + 512
    # first winding (exterior) CCW, second (hole) CW
    text = t.to_text()
    windings = text.split("BEGIN_WINDING")[1:3]
    pts = [[tuple(map(float, ln.split()[1:3])) for ln in w.splitlines() if ln.startswith("POLYGON_POINT")]
           for w in windings]
    assert signed_area(pts[0]) > 0
    assert signed_area(pts[1]) < 0


def test_line_fill_param():
    t = OverlayTile(lat=6, lon=-76)
    t.polygons.append(ForestPolygon("lib/a.for", [(-75.5, 6.5), (-75.45, 6.5), (-75.4, 6.55)],
                                    density=255, fill=FILL_LINE))
    assert f"BEGIN_POLYGON 0 {255 + FILL_LINE} 2" in t.to_text()


def test_point_outside_tile_is_rejected():
    t = OverlayTile(lat=6, lon=-76)
    t.polygons.append(ForestPolygon("lib/a.for", [(-74.9, 6.5), (-74.8, 6.5), (-74.8, 6.6)]))
    with pytest.raises(ValueError, match="outside tile"):
        t.to_text()


def test_density_out_of_range_is_rejected():
    t = OverlayTile(lat=6, lon=-76)
    t.polygons.append(ForestPolygon("lib/a.for", SQUARE_CW, density=300, fill=FILL_SOLID))
    with pytest.raises(ValueError, match="density"):
        t.to_text()


def test_season_rasters_encode_all_year(tmp_path):
    rs = season_rasters("win", size=4)
    assert [r.name for r in rs] == ["spr1", "spr2", "sum1", "sum2", "fal1", "fal2", "win1", "win2"]
    win1 = next(r for r in rs if r.name == "win1")
    assert set(win1.data) == {round(365 / win1.scale)}
    assert all(set(r.data) == {0} for r in rs if r.name != "win1")
    t = OverlayTile(lat=6, lon=-76, rasters=rs)
    path = t.write_to(tmp_path / "x.txt")
    txt = path.read_text()
    assert txt.count("RASTER_DEF") == 8 and (tmp_path / "+06-076.win1.raw").exists()


def test_rasters_need_a_folder():
    t = OverlayTile(lat=6, lon=-76, rasters=season_rasters("sum", size=2))
    with pytest.raises(ValueError):
        t.to_text()
