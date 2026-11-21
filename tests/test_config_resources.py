"""Local config (X-Plane / DSFTool location), parallel-build sizing, history, hidden processes."""

import sys

import pytest

from forest import config, history, resources


def test_check_xplane(sandbox, tmp_path):
    assert config.check_xplane(sandbox.xplane)["ok"]
    bad = config.check_xplane(tmp_path / "nope")
    assert not bad["ok"] and "folder exists" in bad["problems"]
    assert not config.check_xplane(None)["ok"]


def test_save_and_load_relative_dsftool(sandbox, monkeypatch, tmp_path):
    sandbox.dsftool.parent.mkdir(parents=True)
    sandbox.dsftool.write_bytes(b"x")
    monkeypatch.setattr(config, "BUNDLED_DSFTOOL", tmp_path / "elsewhere" / "DSFTool.exe")  # => custom
    config.save(sandbox.xplane, sandbox.dsftool)
    text = (sandbox.root / "config" / "local.toml").read_text()
    assert "tools\\xptools" in text or "tools/xptools" in text     # stored relative to the project
    c = config.load()
    assert c.from_file and c.xplane_root == sandbox.xplane and c.dsftool == sandbox.dsftool
    assert config.check_dsftool(c.dsftool)["ok"]


def test_bundled_dsftool_is_not_written_and_missing_custom_falls_back(sandbox, monkeypatch, tmp_path):
    bundled = tmp_path / "bundled" / "DSFTool.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"x")
    monkeypatch.setattr(config, "BUNDLED_DSFTOOL", bundled)
    monkeypatch.setattr(config, "dsftool_candidates", lambda: [bundled])
    toml = sandbox.root / "config" / "local.toml"
    config.save(sandbox.xplane, bundled)                 # the bundled one needs no setting
    assert "[tools]" not in toml.read_text() and config.load().dsftool == bundled
    config.save(sandbox.xplane)
    assert "[tools]" not in toml.read_text()
    config.save(sandbox.xplane, tmp_path / "gone" / "DSFTool.exe")   # hand-set, but missing
    assert "[tools]" in toml.read_text() and config.load().dsftool == bundled


def test_load_without_file_detects(sandbox, monkeypatch):
    (sandbox.root / "config" / "local.toml").unlink()
    monkeypatch.setattr(config, "xplane_candidates", lambda: [sandbox.xplane])
    c = config.load()
    assert not c.from_file and c.xplane_root == sandbox.xplane


def test_auto_workers_limited_by_memory(monkeypatch):
    monkeypatch.setattr(resources, "system", lambda: {"cores_physical": 8, "cores_logical": 16,
                                                      "ram_total_gb": 32.0, "ram_available_gb": 16.0})
    monkeypatch.setattr(resources, "peak_gb", lambda: 4.6)
    w = resources.auto_workers()
    assert w["workers"] == 3 and w["limited_by"] == "memory"     # (16-2)/4.6 = 3


def test_auto_workers_limited_by_cpu_and_never_zero(monkeypatch):
    monkeypatch.setattr(resources, "system", lambda: {"cores_physical": 2, "cores_logical": 4,
                                                      "ram_total_gb": 128.0, "ram_available_gb": 120.0})
    monkeypatch.setattr(resources, "peak_gb", lambda: 4.0)
    assert resources.auto_workers()["workers"] == 1
    monkeypatch.setattr(resources, "system", lambda: {"cores_physical": 8, "cores_logical": 8,
                                                      "ram_total_gb": 4.0, "ram_available_gb": 1.0})
    assert resources.auto_workers()["workers"] == 1


def test_history_medians_and_defaults(sandbox):
    assert history.averages()["build_s"] == history.DEFAULTS["build_s"]
    for s in (10, 20, 30):
        history.record("build", tile="+06-076", seconds=s, dsf_mb=5, peak_gb=3.0 + s / 10)
    avg = history.averages()
    assert avg["build_s"] == 20 and avg["dsf_mb"] == 5 and avg["samples"] == 3
    assert resources.peak_gb() == pytest.approx(6.0)  # 90th percentile of 4, 5, 6


def test_hidden_process_runs_and_captures_output():
    from forest import proc
    r = proc.run([sys.executable, "-c", "print('hi')"])
    assert r.returncode == 0 and r.stdout.strip() == "hi"
    if sys.platform == "win32":
        assert proc.HIDDEN != 0          # CREATE_NO_WINDOW is used on Windows
