"""Shared fixtures. Every test runs in a sandbox: the project folder, cache, outputs, job
history, settings files and the X-Plane folder are redirected to a temporary directory, so
tests never touch real data, the real X-Plane install, or the network."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A fake code folder, data folder (ns.root) and X-Plane folder. Returns a namespace with useful paths."""
    import forest.assets.catalog as catalog
    import forest.config as config
    import forest.diskusage as diskusage
    import forest.fetch.ecoregions as eco
    import forest.fetch.osm_pbf as osm_pbf
    import forest.history as history
    import forest.jobs as jobs
    import forest.tiles as tiles

    repo = tmp_path / "repo"                 # the code folder (shipped defaults, bundled tools)
    (repo / "config" / "profiles").mkdir(parents=True)
    (repo / "config" / "rules").mkdir(parents=True)
    proj = tmp_path / "data"                 # the data folder (~/.foreststudio)
    (proj / "config" / "profiles").mkdir(parents=True)
    (proj / "config" / "rules").mkdir(parents=True)
    cache = proj / "cache"
    (cache / "tiles").mkdir(parents=True)
    out = proj / "out"
    out.mkdir()

    xp = tmp_path / "X-Plane 12"
    (xp / "Custom Scenery").mkdir(parents=True)
    (xp / "Resources" / "default scenery" / "1200 forests").mkdir(parents=True)
    (xp / "Resources" / "default scenery" / "1200 forests" / "library.txt").write_text("A\n1200\nLIBRARY\n")
    (xp / "Global Scenery" / "X-Plane 12 Global Scenery" / "Earth nav data").mkdir(parents=True)
    dsftool = repo / "tools" / "xptools" / "windows" / "DSFTool.exe"

    monkeypatch.setattr(config, "REPO_ROOT", repo)
    monkeypatch.setattr(config, "DEFAULTS_DIR", repo / "config")
    monkeypatch.setattr(config, "DATA_ROOT", proj)
    monkeypatch.setattr(config, "DATA_SOURCE", "custom")
    monkeypatch.setattr(config, "DEFAULT_DATA_ROOT", tmp_path / "home" / ".foreststudio")
    monkeypatch.setattr(config, "LOCATION_FILE", tmp_path / "home" / ".foreststudio" / "location.toml")
    monkeypatch.delenv("FORESTSTUDIO_HOME", raising=False)
    monkeypatch.setattr(config, "CACHE_ROOT", cache)
    monkeypatch.setattr(config, "OUT_ROOT", out)
    monkeypatch.setattr(config, "USER_CONFIG", proj / "config")
    monkeypatch.setattr(config, "LOCAL_TOML", proj / "config" / "local.toml")
    monkeypatch.setattr(config, "BUNDLED_DSFTOOL", dsftool)
    monkeypatch.setattr(tiles, "CACHE", cache / "tiles")
    monkeypatch.setattr(jobs, "JOBS_DIR", cache / "jobs")
    monkeypatch.setattr(history, "HISTORY", cache / "history.jsonl")
    monkeypatch.setattr(diskusage, "CACHE", cache)
    monkeypatch.setattr(diskusage, "OUT", out)
    monkeypatch.setattr(osm_pbf, "PBF_DIR", cache / "osm")
    monkeypatch.setattr(osm_pbf, "DEFAULT_PBF", cache / "osm" / "none.osm.pbf")
    monkeypatch.setattr(eco, "ECO_DIR", cache / "ecoregions")
    monkeypatch.setattr(eco, "ZIP", cache / "ecoregions" / "raw" / "Ecoregions2017.zip")
    monkeypatch.setattr(catalog, "DEFAULT_CATALOG", proj / "config" / "catalog_xp12.json")
    try:
        import forest.app.api as api
        monkeypatch.setattr(api, "REPO_ROOT", repo)
        monkeypatch.setattr(api, "CACHE", cache / "tiles")
    except Exception:  # noqa: BLE001 - api imports pywebview-free modules only; keep tests running
        pass
    config.save(xp, dsftool, proj / "config" / "local.toml")

    class NS:
        pass

    ns = NS()
    ns.root, ns.cache, ns.out, ns.xplane, ns.dsftool = proj, cache, out, xp, dsftool
    ns.tmp, ns.repo = tmp_path, repo
    return ns


@pytest.fixture
def real_rules():
    """The real Colombia rules shipped in the repository (forest types per biome)."""
    import tomllib
    return tomllib.loads((ROOT / "config" / "rules" / "colombia.toml").read_text(encoding="utf-8"))


@pytest.fixture
def real_profile():
    import tomllib
    return tomllib.loads((ROOT / "config" / "profiles" / "medium.toml").read_text(encoding="utf-8"))


def fake_catalog_entries(assets, seasonal=()) -> list[dict]:
    """Minimal catalog entries for the given virtual paths (evergreen unless listed)."""
    return [{
        "virtual": a, "visibility": "PUBLIC", "regions": ["all"],
        "seasons": ["fal", "spr", "sum", "win"] if a in seasonal else [],
        "evergreen_export": a not in seasonal, "files": {"all": "x.for"},
        "spacing_m": [10.0, 10.0], "random_m": [4.0, 4.0], "lod_m": 15000.0,
        "max_trees_per_km2": 10000, "seasonal_texture": False, "skip_surfaces": ["water"],
        "mesh_count": 20, "species": ["kapok", "palm1"], "avg_tree_tris": 700, "tris_per_km2_m": 7.0,
    } for a in assets]


def rules_assets(rules: dict) -> set[str]:
    out = {rules.get("single_tree")} if rules.get("single_tree") else set()
    for bm in rules.get("biome", []):
        for b in bm["band"]:
            out |= {b["dense"], b["sparse"]}
    for pr in rules.get("plantation", []):
        if pr.get("asset"):
            out.add(pr["asset"])
    return out


@pytest.fixture
def copy_repo_file():
    def _copy(rel: str, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
        return dst
    return _copy


def write_json(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p
