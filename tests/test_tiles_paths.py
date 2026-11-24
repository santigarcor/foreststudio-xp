import pytest

from forest.tiles import Grid, Tile


@pytest.mark.parametrize("name,lat,lon,folder", [
    ("+06-076", 6, -76, "+00-080"),
    ("+10-076", 10, -76, "+10-080"),
    ("+12-082", 12, -82, "+10-090"),
    ("-01-071", -1, -71, "-10-080"),
    ("+00+010", 0, 10, "+00+010"),
])
def test_tile_parse_name_folder(name, lat, lon, folder):
    t = Tile.parse(name)
    assert (t.lat, t.lon) == (lat, lon)
    assert t.name == name
    assert t.folder == folder
    assert t.bounds == (lon, lat, lon + 1, lat + 1)


@pytest.mark.parametrize("bad", ["6-76", "+6-076", "+06-76", "06-076", "", "+06-076x"])
def test_tile_parse_rejects_bad_names(bad):
    with pytest.raises(ValueError):
        Tile.parse(bad)


def test_grid_transform_maps_corners():
    g = Grid(Tile(6, -76), 600)
    assert g.shape == (600, 600)
    x, y = g.transform @ (0, 0)
    assert (x, y) == pytest.approx((-76, 7))  # row 0 = north edge
    x, y = g.transform @ (600, 600)
    assert (x, y) == pytest.approx((-75, 6))


def test_cache_path_does_not_create_folder(sandbox):
    t = Tile.parse("+06-076")
    assert not t.cache_path.exists()
    assert t.cache.exists()  # the writing accessor creates it


def test_pack_and_studio_paths_are_separate(sandbox):
    from forest import paths
    t = Tile.parse("+06-076")
    assert paths.PACK_NAME == "forest_studio_xp"
    dsf = paths.dsf_path(t)
    assert dsf == sandbox.root / "out" / "forest_studio_xp" / "Earth nav data" / "+00-080" / "+06-076.dsf"
    st = paths.studio_path(t, ".stats.json")
    assert st.parent == sandbox.root / "out" / "studio"
    assert "Earth nav data" not in str(st)  # app files never go into the X-Plane pack
