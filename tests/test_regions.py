"""Regions: the shipped Colombia file, validation, user overrides, seasonal rule per region."""

from __future__ import annotations

import pytest

from forest.tiles import Tile


def test_colombia_matches_the_old_hardcoded_values(sandbox):
    from forest import regions

    r = regions.get("colombia")
    assert regions.default_name() == "colombia" and "colombia" in regions.names()
    # The map used to offer lat -5..12 × lon -82..-67 (288 tiles), the cut-out box was fixed.
    assert len(r.tiles()) == 18 * 16
    assert r.contains(Tile(12, -82)) and r.contains(Tile(-5, -67))
    assert not r.contains(Tile(13, -75)) and not r.contains(Tile(6, -66))
    assert r.clip_bounds == (-83.0, -5.0, -66.0, 14.0)
    assert r.osm_file.name == "colombia-latest.osm.pbf"
    assert r.evergreen_only


def test_user_region_file_adds_or_replaces(sandbox):
    from forest import regions

    d = sandbox.root / "config" / "regions"
    d.mkdir(parents=True)
    (d / "panama.toml").write_text(
        'bounds = [-83, 7, -77, 10]\n'
        'osm_url = "https://download.geofabrik.de/central-america/panama-latest.osm.pbf"\n'
        'evergreen_only = false\n', encoding="utf-8")
    p = regions.get("panama")
    assert p.label == "Panama" and not p.evergreen_only
    assert p.clip_bounds == (-84, 6, -76, 11)          # default: bounds + 1°
    assert "panama" in regions.names()


@pytest.mark.parametrize("data, err", [
    ({"bounds": [-82, -5, -66, 13], "osm_url": "http://x/colombia.osm.pbf"}, "https://"),
    ({"bounds": [-82, -5, -66], "osm_url": "https://x/c.osm.pbf"}, "west, south"),
    ({"bounds": [-82.5, -5, -66, 13], "osm_url": "https://x/c.osm.pbf"}, "whole degrees"),
    ({"bounds": [-66, -5, -82, 13], "osm_url": "https://x/c.osm.pbf"}, "west < east"),
])
def test_bad_region_files_are_refused(data, err):
    from forest import regions

    with pytest.raises(ValueError, match=err):
        regions.parse("x", data)


def test_unknown_region(sandbox):
    from forest import regions

    with pytest.raises(ValueError, match="unknown region"):
        regions.get("atlantis")


def test_seasonal_types_refused_only_in_evergreen_regions(sandbox, monkeypatch):
    import forest.build as build
    from forest.dsf.text import ForestPolygon
    from tests.conftest import fake_catalog_entries

    cat = {"forests": fake_catalog_entries(["warm.for"], seasonal={"warm.for"})}
    monkeypatch.setattr(build, "load_catalog", lambda *a, **k: cat)
    sq = [(-75.9, 6.1), (-75.8, 6.1), (-75.8, 6.2), (-75.9, 6.2)]
    polys = [ForestPolygon("warm.for", sq, density=200)]
    s = build.Settings(profile=dict(build.PROFILE_DEFAULTS, tile_tree_budget=1e9, global_density=1.0),
                       rules={}, region="colombia")
    with pytest.raises(ValueError, match="seasonal"):
        build.budget(polys, Tile(6, -76), s)

    d = sandbox.root / "config" / "regions"
    d.mkdir(parents=True)
    (d / "seasonal.toml").write_text('bounds = [-76, 6, -75, 7]\nosm_url = "https://x/s.osm.pbf"\n'
                                     'evergreen_only = false\n', encoding="utf-8")
    s.region = "seasonal"
    build.budget(polys, Tile(6, -76), s)     # allowed there
