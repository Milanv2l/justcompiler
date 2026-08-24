"""Tests for v2.3.0+ features: autonomous mode, packaging, smart routing, notifications."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

import justcompiler as jc
import docker_manager as dm


# ------------------------------------------------- smart packaging router

def test_smart_route_jar_gets_zip(tmp_path, make_engine):
    e = make_engine(tmp_path / "src", tmp_path / "out")
    (e.out_root / "mod.jar").write_bytes(b"PK\x03\x04")
    rc = e._smart_route_packages(["zip"])
    assert rc == 0
    zips = list((e.out_root / "packages").glob("*.zip"))
    assert len(zips) == 1 and zips[0].suffix == ".zip"


def test_smart_route_web_assets_get_targz(tmp_path, make_engine):
    e = make_engine(tmp_path / "src", tmp_path / "out")
    dist = e.out_root / "dist" / "site"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    rc = e._smart_route_packages(["tar.gz"])
    targz = list((e.out_root / "packages").glob("*.tar.gz"))
    assert len(targz) == 1


def test_smart_route_empty_returns_error(tmp_path, make_engine):
    e = make_engine(tmp_path / "src", tmp_path / "out")
    assert e._smart_route_packages(["deb"]) == 1


def test_detect_version_sources(tmp_path):
    import engine as eng
    probes = [
        ("Cargo.toml", '[package]\nname="x"\nversion = "1.2.3"', "1.2.3"),
        ("meson.build", "project(\n    'app',\n    version: '4.5.6',\n)", "4.5.6"),
        ("package.json", '{"version": "7.8.9"}', "7.8.9"),
    ]
    for fname, content, expected in probes:
        f = tmp_path / fname
        f.write_text(content)
        assert eng.detect_project_version(tmp_path) == expected
        f.unlink()
    assert eng.detect_project_version(tmp_path) == "0.0.0"


def test_windows_exe_support_map():
    from engine import windows_exe_supported
    assert windows_exe_supported("go") is True
    assert windows_exe_supported("cargo") is True
    assert windows_exe_supported("python3") is False


# ------------------------------------------------------- packaging helpers

def test_detect_version_cargo_and_meson_and_json(tmp_path):
    import engine as eng
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\nversion = "1.2.3"')
    assert eng.detect_project_version(tmp_path) == "1.2.3"
    (tmp_path / "Cargo.toml").unlink()
    (tmp_path / "meson.build").write_text("project(\n    'app',\n    version: '4.5.6',\n)")
    assert eng.detect_project_version(tmp_path) == "4.5.6"
    (tmp_path / "meson.build").unlink()
    (tmp_path / "package.json").write_text('{"version": "7.8.9"}')
    assert eng.detect_project_version(tmp_path) == "7.8.9"
    (tmp_path / "package.json").unlink()
    assert eng.detect_project_version(tmp_path) == "0.0.0"


# ------------------------------------------------------- all-targets mode

def test_all_targets_uses_empty_filter(tmp_path, monkeypatch):
    calls = {}

    def fake_bootstrap(**kw):
        calls.update(kw)
        folder = kw["artifacts_path"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "build_manifest.json").write_text(json.dumps(
            {"projects": [{"name": "app", "items": [{"name": "a"}]}]}))
        return True

    monkeypatch.setattr(jc.docker_manager, "bootstrap_sandbox", fake_bootstrap)
    proj = tmp_path / "src" / "app"
    proj.mkdir(parents=True)
    (proj / "requirements.txt").write_text("flask\n")
    res = jc.execute_build(str(proj), all_targets=True)
    assert calls.get("target_filter") in ("", None)
    assert res["status"] == "success"


def test_all_targets_no_targets_is_input_error(tmp_path, monkeypatch):
    calls = {}

    def fake_bootstrap(**kw):
        calls.update(kw)
        return True

    monkeypatch.setattr(jc.docker_manager, "bootstrap_sandbox", fake_bootstrap)
    proj = tmp_path / "src" / "empty"
    proj.mkdir(parents=True)
    res = jc.execute_build(str(proj), all_targets=True)
    assert res["exit_code"] == 2 and not calls


# ------------------------------------------------------- retention

def test_retention_keeps_newest_n(tmp_path):
    base = tmp_path / "EXECUTABLE"
    now = time.time()
    for i in range(4):
        d = base / f"old_{i}"
        d.mkdir(parents=True)
        os.utime(d, (100000 + i, 100000 + i))
    removed = jc._clean_executables(base, keep=2)
    assert len(removed) == 2


# ------------------------------------------------------- notifications

def test_notify_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("no display")

    monkeypatch.setattr(jc.subprocess, "Popen", boom)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(jc.shutil, "which", lambda x: None)
    jc._notify("t", "b")


def test_notify_linux_uses_notify_send(monkeypatch):
    sent = []

    class FakePopen:
        def __init__(self, argv, **kw):
            sent.append(argv)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(jc.shutil, "which",
                        lambda x: "/usr/bin/notify-send" if x == "notify-send" else None)
    monkeypatch.setattr(jc.subprocess, "Popen", FakePopen)
    jc._notify("Build ✅ success", 'proj says "hi"')
    argv = sent[0]
    assert argv[0] == "notify-send"
    joined = " ".join(argv)
    assert "success" in joined and '"' not in joined.replace("'", "")
