"""Profile and rules validation / saving (what the app's editors write)."""

import tomllib

import pytest

from forest.app import settings as st
from tests.conftest import fake_catalog_entries, rules_assets


def test_profile_roundtrip(sandbox, real_profile):
    st.write_profile("mine", real_profile)
    back = tomllib.loads((sandbox.root / "config" / "profiles" / "mine.toml").read_text(encoding="utf-8"))
    assert st.validate_profile(back) == st.validate_profile(real_profile)
    assert st.list_profiles() == ["mine"]


@pytest.mark.parametrize("key,value", [("global_density", 1.5), ("grid_cells", 100), ("tile_tree_budget", 0)])
def test_profile_out_of_range(real_profile, key, value):
    with pytest.raises(ValueError):
        st.validate_profile({**real_profile, key: value})


def test_profile_missing_key(real_profile):
    p = dict(real_profile)
    p.pop("simplify_m")
    with pytest.raises(ValueError, match="simplify_m"):
        st.validate_profile(p)


@pytest.mark.parametrize("levels", [[], [0, 0.5], [1.2], [0.1] * 9])
def test_density_levels_validated(real_profile, levels):
    with pytest.raises(ValueError):
        st.validate_profile({**real_profile, "density_levels": levels})


@pytest.mark.parametrize("name", ["Bad Name", "../evil", "x" * 41, ""])
def test_profile_name_rules(real_profile, name):
    with pytest.raises(ValueError):
        st.write_profile(name, real_profile)


def test_every_profile_setting_has_help_and_fps_mark():
    for key, spec in st.PROFILE_SCHEMA.items():
        assert spec[6] and len(spec[6]) > 20, f"{key} needs a plain-language help text"
        assert key in st.FPS_IMPACT, f"{key} needs an FPS impact mark"


def test_rules_roundtrip_with_biomes_and_plantations(sandbox, real_rules):
    evergreen = rules_assets(real_rules)
    st.write_rules("colombia", real_rules, evergreen)
    back = tomllib.loads((sandbox.root / "config" / "rules" / "colombia.toml").read_text(encoding="utf-8"))
    assert st.validate_rules(back, evergreen) == st.validate_rules(real_rules, evergreen)


def test_rules_reject_seasonal_assets(real_rules):
    evergreen = rules_assets(real_rules) - {"lib/vegetation/forests/mixed/very_hot.for"}
    with pytest.raises(ValueError, match="not an allowed"):
        st.validate_rules(real_rules, evergreen)


def test_rules_require_default_biome(real_rules):
    r = {**real_rules, "biome": [b for b in real_rules["biome"] if b["name"] != "default"]}
    with pytest.raises(ValueError, match="default"):
        st.validate_rules(r, rules_assets(real_rules))


def test_rules_band_order(real_rules):
    bm = dict(real_rules["biome"][0])
    bm["band"] = list(reversed(bm["band"]))
    with pytest.raises(ValueError, match="increase"):
        st.validate_rules({**real_rules, "biome": [bm] + real_rules["biome"][1:]}, rules_assets(real_rules))


def test_plantation_match_syntax(real_rules):
    bad = {**real_rules, "plantation": [{"name": "x", "match": ["oil palms"], "asset": "", "density": 0.5}]}
    with pytest.raises(ValueError, match="key=value"):
        st.validate_rules(bad, rules_assets(real_rules))


def test_fake_catalog_helper_marks_seasons():
    e = fake_catalog_entries(["a.for", "b.for"], seasonal={"b.for"})
    assert e[0]["seasons"] == [] and e[1]["seasons"]


def test_reset_to_defaults_reads_the_shipped_files(sandbox, copy_repo_file):
    """'Reset to defaults' loads the shipped profile / forest types, never the user's copies."""
    from forest import config
    from forest.app.api import Api
    copy_repo_file("config/profiles/medium.toml", config.DEFAULTS_DIR / "profiles" / "medium.toml")
    copy_repo_file("config/rules/colombia.toml", config.DEFAULTS_DIR / "rules" / "colombia.toml")
    (sandbox.root / "config" / "profiles").mkdir(parents=True, exist_ok=True)
    (sandbox.root / "config" / "profiles" / "medium.toml").write_text("tile_tree_budget = 5\n")
    a = Api.__new__(Api)
    d = a.default_profile("medium")
    assert d["base"] == "medium" and d["values"]["tile_tree_budget"] > 1_000_000
    assert "town_trees_per_km2" in d["values"]
    assert a.default_profile("medium-copy")["base"] == "medium"        # own profiles reset to medium
    r = a.default_rules("colombia")
    assert r["ok"] and any(b["name"] == "xeric" and b["scrub"] for b in r["rules"]["biome"])
    assert not a.default_rules("nowhere")["ok"]
