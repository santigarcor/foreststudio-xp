"""Reading X-Plane's forest library (library.txt + .for files) into the catalog."""

from pathlib import Path

from forest.assets.catalog import build_catalog
from forest.xplane.forest import read_forest
from forest.xplane.library import parse_library

LIB = """A
1200
LIBRARY
REGION_DEFINE all
REGION_RECT -180 -90 179 89
REGION all
PUBLIC
EXPORT_SEASON sum lib/vegetation/forests/mixed/warm.for sum/mixed_wrm.for
EXPORT_SEASON win lib/vegetation/forests/mixed/warm.for win/mixed_wrm.for
EXPORT lib/vegetation/forests/mixed/very_hot.for sum/mixed_vhot.for
SEMI_DEPRECATED
EXPORT lib/g8/rainforest_vhot_rain.for sum/rainforest.for
EXPORT lib/g8/not_a_forest.obj sum/thing.obj
"""


def forest_file(spacing=10, seasonal=False, meshes=(("3D_kapok_02", 600), ("3D_palm1_03", 300), ("lod_kapok_02", 30))):
    lines = ["A", "1200", "FOREST", "SHADER_3D", "\tTEXTURE ../textures/trees_3D1_ALB.png"]
    if seasonal:
        lines.append("\tSEASONAL ../textures/trees_3D1_ALB_fa.png")
    lines += ["LOD 15000", f"SPACING {spacing} {spacing}", "RANDOM 4 4", "SKIP_SURFACE water"]
    for name, idx in meshes:
        lines.append(f"MESH {name} 0 500 100 {idx} 1.0 1.0 10.0")
        lines += ["VERTEX 0 0 0 0 1 0 0.5 0.5 0 0 0"] * 3
        lines.append("IDX 0 1 2")
    return "\n".join(lines) + "\n"


def make_library(root: Path):
    for sub in ("sum", "win"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "library.txt").write_text(LIB)
    (root / "sum" / "mixed_wrm.for").write_text(forest_file(12))
    (root / "win" / "mixed_wrm.for").write_text(forest_file(12))
    (root / "sum" / "mixed_vhot.for").write_text(forest_file(10))
    (root / "sum" / "rainforest.for").write_text(forest_file(20, seasonal=True))
    return root


def test_parse_library_visibility_and_seasons(tmp_path):
    lib = parse_library(make_library(tmp_path / "1200 forests") / "library.txt")
    by = lib.by_virtual()
    assert {e.season for e in by["lib/vegetation/forests/mixed/warm.for"]} == {"sum", "win"}
    assert by["lib/g8/rainforest_vhot_rain.for"][0].visibility == "SEMI_DEPRECATED"
    assert by["lib/vegetation/forests/mixed/very_hot.for"][0].kind == "EXPORT"


def test_read_forest_skips_mesh_data_but_counts_triangles(tmp_path):
    p = tmp_path / "f.for"
    p.write_text(forest_file(10))
    fi = read_forest(p)
    assert fi.spacing == (10.0, 10.0)
    assert fi.max_trees_per_km2 == 10000
    assert fi.mesh_tris == {"3D_kapok_02": 200, "3D_palm1_03": 100, "lod_kapok_02": 10}
    assert fi.avg_tree_tris == 150            # LOD stand-ins excluded
    assert fi.species == ["kapok", "palm1"]
    assert fi.skip_surfaces == ["water"]


def test_build_catalog(tmp_path):
    cat = build_catalog(make_library(tmp_path / "1200 forests"))
    by = {f["virtual"]: f for f in cat["forests"]}
    assert "lib/g8/not_a_forest.obj" not in by
    warm = by["lib/vegetation/forests/mixed/warm.for"]
    assert warm["seasons"] == ["sum", "win"] and not warm["evergreen_export"]
    hot = by["lib/vegetation/forests/mixed/very_hot.for"]
    assert hot["seasons"] == [] and hot["max_trees_per_km2"] == 10000
    assert hot["tris_per_km2_m"] == 1.5      # 10 000 trees × 150 triangles
    rain = by["lib/g8/rainforest_vhot_rain.for"]
    assert rain["seasonal_texture"] is True and rain["visibility"] == "SEMI_DEPRECATED"


def test_ensure_catalog_needs_xplane(sandbox, monkeypatch):
    import forest.assets.catalog as catalog
    monkeypatch.setattr(catalog, "DEFAULT_CATALOG", sandbox.root / "config" / "catalog_xp12.json")
    # the sandbox X-Plane has an empty forest library → catalog with no forests, still created
    assert catalog.ensure_catalog() is True
    assert (sandbox.root / "config" / "catalog_xp12.json").exists()
