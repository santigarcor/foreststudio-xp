"""One pass over an OSM file feeds many tiles (real pyosmium run on a tiny .osm file)."""

import json

from forest.fetch import osm_pbf
from forest.fetch.osm_pbf import extract_tiles, is_current, is_plantation, wanted_way
from forest.tiles import Tile

OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="6.5" lon="-75.9" version="1"><tag k="natural" v="tree"/></node>
  <node id="2" lat="6.5" lon="-76.2" version="1"/>
  <node id="3" lat="6.5" lon="-75.8" version="1"/>
  <node id="10" lat="6.1" lon="-75.5" version="1"/>
  <node id="11" lat="6.1" lon="-75.49" version="1"/>
  <node id="12" lat="6.11" lon="-75.49" version="1"/>
  <node id="13" lat="6.11" lon="-75.5" version="1"/>
  <node id="20" lat="6.2" lon="-75.3" version="1"/>
  <node id="21" lat="6.21" lon="-75.3" version="1"/>
  <way id="100" version="1"><nd ref="2"/><nd ref="3"/><tag k="highway" v="primary"/></way>
  <way id="101" version="1"><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="10"/>
    <tag k="landuse" v="orchard"/><tag k="trees" v="oil_palms"/></way>
  <way id="102" version="1"><nd ref="20"/><nd ref="21"/><tag k="name" v="ignored"/></way>
</osm>
"""


def test_filters():
    assert wanted_way({"highway": "track"})
    assert wanted_way({"landuse": "farmland", "crop": "coffee"})
    assert not wanted_way({"landuse": "farmland"})
    assert not wanted_way({"name": "x"})
    assert wanted_way({"natural": "wetland", "wetland": "mangrove"})
    assert wanted_way({"natural": "wetland", "wetland": "marsh"})       # wetland bushes
    assert wanted_way({"natural": "wetland", "wetland": "swamp"})       # swamp forest
    assert is_plantation({"landuse": "orchard"}) and not is_plantation({"landuse": "forest"})
    assert wanted_way({"amenity": "parking"}) and not wanted_way({"amenity": "bench"})
    assert wanted_way({"landuse": "construction"}) and wanted_way({"natural": "sand"})
    assert osm_pbf.wanted_area_relation({"amenity": "parking"})


def test_one_pass_feeds_every_touching_tile(sandbox):
    pbf = sandbox.tmp / "test.osm"
    pbf.write_text(OSM)
    a, b = Tile.parse("+06-076"), Tile.parse("+06-077")
    counts = extract_tiles([a, b], pbf)
    assert set(counts) == {"+06-076", "+06-077"}
    da = json.loads((a.cache_path / "osm.json").read_text())
    db = json.loads((b.cache_path / "osm.json").read_text())
    assert da["version"] == osm_pbf.VERSION
    ids_a = {(e["type"], e["id"]) for e in da["elements"]}
    ids_b = {(e["type"], e["id"]) for e in db["elements"]}
    assert ("way", 100) in ids_a and ("way", 100) in ids_b   # road crosses both tiles
    assert ("node", 1) in ids_a and ("node", 1) not in ids_b  # tree only in its own tile
    assert ("way", 101) in ids_a                              # plantation kept with tags
    assert all(e["id"] != 102 for e in da["elements"])        # untagged-for-us way dropped
    assert is_current(a) and is_current(b)
    assert extract_tiles([a, b], pbf) == {}                   # already current: no second pass


def test_old_cache_version_is_reextracted(sandbox):
    pbf = sandbox.tmp / "test.osm"
    pbf.write_text(OSM)
    t = Tile.parse("+06-076")
    (t.cache / "osm.json").write_text('{"version": 1, "elements": []}')
    assert not is_current(t)
    assert set(extract_tiles([t], pbf)) == {"+06-076"}


class _Seen(dict):
    """Tags that record every key a test looks at."""

    def __init__(self):
        super().__init__()
        self.keys = set()

    def get(self, k, default=None):
        self.keys.add(k)
        return super().get(k, default)

    def __contains__(self, k):
        self.keys.add(k)
        return super().__contains__(k)


def test_prefilter_keeps_every_key_the_python_tests_use():
    # With no tags every test is false, so every primary key is looked at (secondary keys such as
    # crop= or wetland= only matter after landuse= / natural=, which are listed).
    for test in (osm_pbf.wanted_way, osm_pbf.wanted_area_relation):
        t = _Seen()
        assert not test(t)
        assert t.keys <= set(osm_pbf.PREFILTER_KEYS), t.keys - set(osm_pbf.PREFILTER_KEYS)


AREAS = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="6.5" lon="-75.9" version="1"><tag k="natural" v="tree"/></node>
  <node id="2" lat="6.5" lon="-75.95" version="1"><tag k="amenity" v="bench"/></node>
  <node id="3" lat="6.51" lon="-75.95" version="1"><tag k="natural" v="peak"/></node>
  <node id="10" lat="6.1" lon="-75.5" version="1"/>
  <node id="11" lat="6.1" lon="-75.4" version="1"/>
  <node id="12" lat="6.2" lon="-75.4" version="1"/>
  <node id="13" lat="6.2" lon="-75.5" version="1"/>
  <node id="20" lat="6.12" lon="-75.48" version="1"/>
  <node id="21" lat="6.12" lon="-75.42" version="1"/>
  <node id="22" lat="6.18" lon="-75.42" version="1"/>
  <node id="23" lat="6.18" lon="-75.48" version="1"/>
  <node id="30" lat="6.3" lon="-75.3" version="1"/>
  <node id="31" lat="6.3" lon="-75.29" version="1"/>
  <node id="32" lat="6.31" lon="-75.29" version="1"/>
  <way id="200" version="1"><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="10"/></way>
  <way id="201" version="1"><nd ref="20"/><nd ref="21"/><nd ref="22"/><nd ref="23"/><nd ref="20"/></way>
  <way id="202" version="1"><nd ref="30"/><nd ref="31"/><nd ref="32"/><nd ref="30"/>
    <tag k="building" v="yes"/></way>
  <way id="203" version="1"><nd ref="30"/><nd ref="31"/><tag k="barrier" v="fence"/></way>
  <relation id="300" version="1"><member type="way" ref="200" role="outer"/>
    <member type="way" ref="201" role="inner"/>
    <tag k="type" v="multipolygon"/><tag k="landuse" v="forest"/></relation>
  <relation id="301" version="1"><member type="way" ref="201" role="outer"/>
    <tag k="type" v="multipolygon"/><tag k="place" v="islet"/></relation>
</osm>
"""


def test_prefilter_gives_the_same_file(sandbox, monkeypatch):
    """With and without the osmium filters the osm.json is byte for byte the same (trees, other
    tagged points, untagged member ways, a forest multipolygon, a relation we don't use)."""
    pbf = sandbox.tmp / "areas.osm"
    pbf.write_text(AREAS)
    t = Tile.parse("+06-076")
    out = {}
    for on in (False, True):
        monkeypatch.setattr(osm_pbf, "PREFILTER", on)
        extract_tiles([t], pbf, force=True)
        out[on] = (t.cache_path / "osm.json").read_bytes()
    assert out[True] == out[False]
    ids = {(e["type"], e["id"]) for e in json.loads(out[True])["elements"]}
    assert ids == {("node", 1), ("way", 202), ("relation", 300)}


ODD = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="6.5" lon="-75.9" version="1"/>
  <node id="2" lat="6.5000001" lon="-75.8999999" version="1"/>
  <node id="3" lat="6.6" lon="-75.7" version="1"/>
  <node id="4" lat="6.9999999" lon="-76.0000001" version="1"/>
  <way id="1" version="1"><nd ref="1"/><nd ref="2"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="path"/></way>
  <way id="2" version="1"><nd ref="1"/><nd ref="999"/><nd ref="3"/><tag k="highway" v="track"/></way>
  <way id="3" version="1"><nd ref="1"/><tag k="highway" v="service"/></way>
  <way id="4" version="1"><nd ref="999"/><nd ref="3"/><tag k="building" v="yes"/></way>
  <way id="5" version="1"><nd ref="3"/><nd ref="4"/><tag k="power" v="line"/></way>
</osm>
"""


def test_fast_way_path_writes_what_the_per_node_path_writes(sandbox, monkeypatch):
    from forest import config

    assert str(sandbox.root) in str(config.CACHE_ROOT)
    """Lines go through osmium's C++ geometry + numpy; repeated points, a missing point, a
    one-point line and a line across tiles must come out exactly as with the per-node code."""
    tiles = [Tile.parse("+06-076"), Tile.parse("+06-077")]
    out = {}
    for name, text in (("odd", ODD), ("osm", OSM), ("areas", AREAS)):
        pbf = sandbox.tmp / f"{name}.osm"
        pbf.write_text(text)
        for slow in (False, True):
            # context(): only this patch is undone (monkeypatch.undo() would also undo the sandbox
            # and write into the real data folder)
            with monkeypatch.context() as m:
                if slow:
                    m.setattr(osm_pbf._Handler, "way", osm_pbf._Handler._way_slow)
                extract_tiles(tiles, pbf, force=True)
            assert all(str(sandbox.root) in str(t.cache_path) for t in tiles)
            out[name, slow] = [(t.cache_path / "osm.json").read_bytes() for t in tiles]
        assert out[name, False] == out[name, True], name
    odd = json.loads(out["odd", False][0])["elements"]
    assert [e["id"] for e in odd] == [1, 2, 5]           # 3: one point; 4: one valid point left
    assert len(odd[0]["geometry"]) == 4                  # repeated node kept
