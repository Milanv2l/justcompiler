"""Tests for v2.3.0: --all-targets, retention, desktop notifications, help overlay."""
import json
from pathlib import Path

import pytest

import justcompiler as jc


# ------------------------------------------------------------ all-targets

def _stub_bootstrap(monkeypatch, manifest_projects, rc=0):
    calls = {}

    def fake_bootstrap(**kw):
        calls.update(kw)
        folder = kw["artifacts_path"]
        (folder / "build_manifest.json").write_text(json.dumps(
            {"projects": manifest_projects}))
        (folder / "build_log.txt").write_text("")
        return rc == 0
    monkeypatch.setattr(jc.docker_manager, "bootstrap_sandbox", fake_bootstrap)
    return calls


def test_all_targets_uses_empty_filter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "src" / "app"; proj.mkdir(parents=True)
    (proj / "requirements.txt").write_text("flask\n")
    calls = _stub_bootstrap(monkeypatch,
                            [{"name": "app", "items": [{"name": "a"}]}])
    res = jc.execute_build(str(proj), all_targets=True)
    assert calls.get("target_filter") in ("", None)
    assert res["status"] == "success"


def test_all_targets_no_targets_is_input_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "src" / "empty"; proj.mkdir(parents=True)
    calls = _stub_bootstrap(monkeypatch, [])
    res = jc.execute_build(str(proj), all_targets=True)
    assert res["exit_code"] == 2 and res["status"] == "invalid_input"
    assert not calls          # bootstrap never invoked


def test_single_target_mode_keeps_filter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "src" / "app"; proj.mkdir(parents=True)
    (proj / "requirements.txt").write_text("flask\n")
    calls = _stub_bootstrap(monkeypatch,
                            [{"name": "app", "items": [{"name": "a"}]}])
    jc.execute_build(str(proj))
    filt = calls.get("target_filter")
    assert filt and "Python" in filt


# ------------------------------------------------------------- retention

def test_retention_keeps_newest_n(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "EXECUTABLE"
    import os, time
    for i in range(4):                      # 4 pre-existing old folders
        d = base / f"old_{i}"; d.mkdir(parents=True)
        os.utime(d, (100000 + i, 100000 + i))
    proj = tmp_path / "src" / "app"; proj.mkdir(parents=True)
    (proj / "requirements.txt").write_text("f\n")
    monkeypatch.setattr(jc, "_notify", lambda *a, **k: None)
    _stub_bootstrap(monkeypatch, [{"name": "app", "items": []}], rc=1)  # fail ok
    monkeypatch.setattr(jc, "load_config",
                        lambda: {"keep_builds": 2})
    jc.execute_build(str(proj))
    remaining = sorted(p.name for p in base.iterdir())
    # newest 2 of the 5 total folders survive (the fresh build + old_3)
    assert len(remaining) == 2 and any(n.startswith("app_") for n in remaining)


# ---------------------------------------------------------- notifications

def test_notify_linux_uses_notify_send(monkeypatch):
    sent = []
    class FakePopen:
        def __init__(self, argv, **kw): sent.append(argv)
    monkeypatch.setattr(jc.sys, "platform", "linux")
    monkeypatch.setattr(jc.shutil, "which", lambda x: "/usr/bin/notify-send" if x == "notify-send" else None)
    monkeypatch.setattr(jc.subprocess, "Popen", FakePopen)
    jc._notify('Build ✅ success', 'proj says "hi"')
    argv = sent[0]
    assert argv[0] == "notify-send"
    joined = " ".join(argv)
    assert "success" in joined and '"hi"' not in joined   # quotes neutralised


def test_notify_never_raises(monkeypatch):
    def boom(*a, **k): raise OSError("no display")
    monkeypatch.setattr(jc.subprocess, "Popen", boom)
    monkeypatch.setattr(jc.sys, "platform", "linux")
    monkeypatch.setattr(jc.shutil, "which", lambda x: None)
    jc._notify("t", "b")            # must not raise


# ------------------------------------------------------- packaging helpers

def test_detect_version_sources(tmp_path):
    from engine import detect_project_version
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\nversion = "1.2.3"')
    assert detect_project_version(tmp_path) == "1.2.3"
    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "meson.build").write_text("project(\n    'app',\n    version: '4.5.6',\n)")
    assert detect_project_version(tmp_path) == "4.5.6"
    (tmp_path / "package.json").write_text('{"version": "7.8.9"}')
    assert detect_project_version(tmp_path) == "7.8.9"
    (tmp_path / "package.json").unlink()
    (tmp_path / "meson.build").unlink()
    assert detect_project_version(tmp_path) == "0.0.0"


def test_sanitize_names():
    from engine import sanitize_deb_name, sanitize_app_id
    assert sanitize_deb_name("My Cool App!") == "my-cool-app"
    assert sanitize_app_id("syncthing").startswith("com.justcompiler.")
    assert "." in sanitize_app_id("castle siege")


def test_desktop_entry_fields():
    from engine import desktop_entry
    d = desktop_entry("Foo", "foo-bin", "does things")
    assert "[Desktop Entry]" in d and "Exec=foo-bin" in d and "Name=Foo" in d


def test_rpm_spec_structure():
    from engine import rpm_spec
    spec = rpm_spec("app", "1.0", "desc", "payload.tar.gz")
    for token in ("Name:           app", "Version:        1.0",
                  "%install", "%files", "payload.tar.gz"):
        assert token in spec, token


def test_windows_exe_support_map():
    import importlib, os
    os.environ["JC_TEST"] = "1"
    # reimport-free: use the function directly from engine namespace via module
    import importlib.util as iu
    spec = iu.spec_from_file_location(
        "eng_pkg", Path(__file__).resolve().parent.parent / "engine.py")
    m = importlib.util.module_from_spec(spec)
    prev = sys.modules.get("engine")
    try:
        m.__dict__.update(sys.modules["engine"].__dict__)
        assert m.windows_exe_supported("go") is True
        assert m.windows_exe_supported("cargo") is True
        assert m.windows_exe_supported("python3") is False
    finally:
        if prev: sys.modules["engine"] = prev


import sys  # noqa: E402  (used by windows-support test)
