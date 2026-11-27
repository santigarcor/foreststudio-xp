"""The tile list of each downloaded OSM file: the map enables only the tiles in one."""

import io

from forest.tiles import Tile

POLY = """colombia
1
   0.5   0.5
   2.5   0.5
   2.5   1.5
   0.5   1.5
END
!2
   1.1   0.6
   1.9   0.6
   1.9   0.9
   1.1   0.9
END
END
"""


def test_poly_url_and_parse():
    from forest.fetch import osm_tiles

    assert (osm_tiles.poly_url("https://download.geofabrik.de/south-america/colombia-latest.osm.pbf")
            == "https://download.geofabrik.de/south-america/colombia.poly")
    assert osm_tiles.poly_url("https://example.com/x.pbf") is None
    g = osm_tiles.parse_poly(POLY)
    assert abs(g.area - (2.0 - 0.24)) < 1e-9                       # the '!' ring is a hole
    shares = osm_tiles.shares_of(g)
    assert set(shares) == {"+00+000", "+00+001", "+00+002", "+01+000", "+01+001", "+01+002"}
    assert shares["+00+001"] == round(0.5 - 0.24, 6)


def test_write_from_poly_and_read(sandbox, monkeypatch):
    from forest.fetch import osm_tiles

    pbf = sandbox.cache / "osm" / "x-latest.osm.pbf"
    asked = []

    def fake_open(req, timeout=0):
        asked.append(req.full_url)
        return io.BytesIO(POLY.encode())

    monkeypatch.setattr(osm_tiles.urllib.request, "urlopen", fake_open)
    osm_tiles.write(pbf, "https://download.geofabrik.de/x-latest.osm.pbf")
    assert asked == ["https://download.geofabrik.de/x.poly"]
    assert set(osm_tiles.read(pbf)) >= {"+00+000", "+01+002"}
    osm_tiles.db_path(pbf).write_text("{bad", encoding="utf-8")
    assert osm_tiles.read(pbf) is None


def _region(sandbox, name: str, box: str) -> None:
    d = sandbox.root / "config" / "regions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(f'bounds = {box}\nosm_url = "https://x/{name}-latest.osm.pbf"\n', encoding="utf-8")


def test_map_enables_only_listed_tiles(sandbox):
    """MPTO: Panama tiles in Colombia's box aren't in Colombia's tile list → not enabled."""
    from forest import regions
    from forest.fetch import osm_tiles

    _region(sandbox, "panama", "[0, 0, 3, 2]")
    p = regions.get("panama")
    assert "+01+002" not in regions.covered_tiles()                    # not downloaded: nothing
    assert regions.waiting_for_download(Tile(1, 2)) == ["panama"]
    p.osm_file.parent.mkdir(parents=True, exist_ok=True)
    p.osm_file.write_bytes(b"")                                        # downloaded, list not made yet:
    assert "+01+002" in regions.covered_tiles()                        # the box until it is listed
    osm_tiles.write_tiles(p.osm_file, {"+00+000": 1.0}, "test")
    assert "+00+000" in regions.covered_tiles() and "+01+002" not in regions.covered_tiles()
    assert regions.osm_files(Tile(1, 2)) == [] and regions.waiting_for_download(Tile(1, 2)) == []
    p.osm_file.unlink()                                                # deleted to save space:
    assert "+00+000" in regions.covered_tiles()                        # the list still counts
    gone = regions.remove("panama")
    assert "panama-latest.osm.pbf.tiles.json" in gone and not osm_tiles.db_path(p.osm_file).exists()


def test_owner_uses_the_tile_lists(sandbox):
    from forest import regions
    from forest.fetch import osm_tiles

    for n in ("aaa", "bbb"):
        _region(sandbox, n, "[0, 0, 2, 1]")
    osm_tiles.write_tiles(regions.get("aaa").osm_file, {"+00+000": 0.2, "+00+001": 0.9}, "test")
    osm_tiles.write_tiles(regions.get("bbb").osm_file, {"+00+000": 0.8, "+00+001": 0.1}, "test")
    assert regions.owner(Tile(0, 0)) == "bbb" and regions.owner(Tile(0, 1)) == "aaa"


def test_ensure_all_lists_older_downloads_once(sandbox, monkeypatch):
    from forest import regions
    from forest.fetch import osm_tiles

    _region(sandbox, "aaa", "[0, 0, 1, 1]")
    a = regions.get("aaa").osm_file
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b"")
    made = []
    monkeypatch.setattr(osm_tiles, "write", lambda pbf, url, timeout=30: made.append(pbf.name)
                        or osm_tiles.write_tiles(pbf, {"+00+000": 1.0}, "test"))
    notes = osm_tiles.ensure_all()
    assert made == ["aaa-latest.osm.pbf"] and len(notes) == 1       # Colombia's list is already there
    assert osm_tiles.ensure_all() == []

    def offline(pbf, url, timeout=30):
        raise OSError("offline")
    osm_tiles.db_path(a).unlink()
    monkeypatch.setattr(osm_tiles, "write", offline)
    assert "could not list" in osm_tiles.ensure_all()[0]               # tried again next start


def test_known_outline_beats_a_region_box(sandbox):
    """Without Geofabrik's index, an undownloaded region's box must not take a tile from a region
    whose file lists it (Colombia's box vs Panama's tile list)."""
    from forest import regions
    from forest.fetch import osm_tiles

    for n in ("aaa", "zzz"):
        _region(sandbox, n, "[0, 0, 1, 1]")
    osm_tiles.write_tiles(regions.get("zzz").osm_file, {"+00+000": 0.3}, "test")
    assert regions.owner(Tile(0, 0)) == "zzz"
