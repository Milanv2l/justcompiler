"""Regression tests for docker_manager.py — pure logic only, Docker never invoked."""
from pathlib import Path

import docker_manager as dm


# ------------------------------------------------------------ sandbox flags

def test_sandbox_flags_env_always_present():
    flags = dm._sandbox_flags(None, {})
    assert "PYTHONUNBUFFERED=1" in flags
    assert "JC_JAVA_VERSION" not in " ".join(flags)


def test_sandbox_flags_java_version_sets_env():
    flags = dm._sandbox_flags(17, {})
    i = flags.index("JC_JAVA_VERSION=17")
    assert flags[i - 1] == "-e"
    assert "PYTHONUNBUFFERED=1" in flags


def test_sandbox_flags_ordering_before_image():
    # REGRESSION: docker flags were once appended AFTER the image name,
    # turning them into container argv. Flags must never contain the image.
    for jv in (None, 8, 17, 21, 25):
        flags = dm._sandbox_flags(jv, {})
        assert not any("justcompiler" in f or f.startswith("/") for f in flags)
        if jv:
            assert flags.index("-e") < len(flags)


def test_sandbox_flags_network_off():
    flags = dm._sandbox_flags(None, {"sandbox_network": False})
    assert flags[flags.index("--network") + 1] == "none"


def test_sandbox_flags_network_default_on():
    assert "--network" not in dm._sandbox_flags(None, {})


def test_sandbox_flags_limits():
    flags = dm._sandbox_flags(None, {"memory_limit": "4g", "cpu_limit": 2})
    assert flags[flags.index("--memory") + 1] == "4g"
    assert flags[flags.index("--cpus") + 1] == "2"
    combined = dm._sandbox_flags(21, {"memory_limit": "4g", "cpu_limit": 2})
    assert "JC_JAVA_VERSION=21" in combined and "--memory" in combined


# --------------------------------------------------------------- image hash

def test_engine_hash_changes_with_input(tmp_path):
    (tmp_path / "core.py").write_text("a = 1")
    h1 = dm._compute_engine_hash(tmp_path)
    (tmp_path / "core.py").write_text("a = 2")
    h2 = dm._compute_engine_hash(tmp_path)
    assert h1 != h2 and len(h1) == 16


def test_engine_hash_stable_when_untouched(tmp_path):
    (tmp_path / "plugins.json").write_text("[]")
    assert dm._compute_engine_hash(tmp_path) == dm._compute_engine_hash(tmp_path)


def test_volume_name_derives_from_path(tmp_path):
    v1 = dm._volume_name(tmp_path)
    v2 = dm._volume_name(tmp_path / "sub")
    assert v1.startswith("justcompiler-") and v1 != v2


# ------------------------------------------------------------- image prune

class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _run_prune(monkeypatch, listing_stdout, keep_tag=""):
    removed = []

    def fake_run(argv, **kw):
        args = list(argv)
        if "images" in args:
            return FakeProc(stdout=listing_stdout)
        if "rmi" in args:
            removed.append(args[args.index("-f") + 1])
        return FakeProc()

    monkeypatch.setattr(dm.subprocess, "run", fake_run)
    dm._prune_old_images(["docker"], "justcompiler-base",
                         keep_tag=keep_tag, keep=2)
    return removed


def test_prune_keeps_two_newest(monkeypatch):
    # Real `docker images --format {{.CreatedAt}}` emits sortable timestamps
    listing = (
        "id-old|justcompiler-base:aaa|2026-08-19 10:00:00 +0000 UTC\n"
        "id-mid|justcompiler-base:bbb|2026-08-20 10:00:00 +0000 UTC\n"
        "id-new|justcompiler-base:ccc|2026-08-21 10:00:00 +0000 UTC\n"
        "id-newest|justcompiler-base:ddd|2026-08-21 18:00:00 +0000 UTC\n"
    )
    removed = _run_prune(monkeypatch, listing)
    # newest two (ddd, ccc) survive; older two removed
    assert sorted(removed) == ["id-mid", "id-old"]


def test_prune_never_touches_keep_tag(monkeypatch):
    listing = (
        "id-a|justcompiler-engine:x1|2 hours ago\n"
        "id-b|justcompiler-engine:x2|1 hour ago\n"
        "id-c|justcompiler-engine:x3|30 minutes ago\n"
    )
    removed = _run_prune(monkeypatch, listing, keep_tag="justcompiler-engine:x3")
    assert "id-c" not in removed


def test_prune_noop_on_empty_listing(monkeypatch):
    assert _run_prune(monkeypatch, "") == []


def test_prune_skips_dangling_entries(monkeypatch):
    listing = (
        "<none>|<none>|1 day ago\n"
        "id-a|justcompiler-base:a1|3 hours ago\n"
    )
    removed = _run_prune(monkeypatch, listing)
    assert removed == []  # a1 is within keep=2; dangling entry ignored


def test_base_and_engine_hashes_diverge(tmp_path):
    # engine hash includes docker_manager.py too (regression: base rebuild trigger)
    (tmp_path / "core.py").write_text("x")
    (tmp_path / "engine.py").write_text("y")
    (tmp_path / "plugins.json").write_text("[]")
    (tmp_path / "docker_manager.py").write_text("z")
    h1 = dm._compute_engine_hash(tmp_path)
    (tmp_path / "docker_manager.py").write_text("z2")
    assert dm._compute_engine_hash(tmp_path) != h1
