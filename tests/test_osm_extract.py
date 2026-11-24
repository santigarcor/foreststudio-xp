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
