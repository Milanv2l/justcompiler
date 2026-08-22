"""Tests for the `clean` command helpers (retention + volume listing)."""
import os
import time

import justcompiler as jc


def _mk(path, mtime=None):
    path.mkdir(parents=True)
    (path / "artifact.bin").write_text("x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_clean_keeps_newest_n(tmp_path):
    base = tmp_path / "EXECUTABLE"
    now = time.time()
    for i in range(15):
        _mk(base / f"proj_{i:02d}", now - (i * 3600))  # 0 newest
    removed = jc._clean_executables(base, keep=10)
    assert len(removed) == 5
    remaining = sorted(p.name for p in base.iterdir())
    assert remaining == [f"proj_{i:02d}" for i in range(10)]


def test_clean_under_limit_noop(tmp_path):
    base = tmp_path / "EXEC"
    _mk(base / "only", time.time())
    assert jc._clean_executables(base, keep=10) == []
    assert (base / "only").exists()


def test_clean_missing_dir_returns_empty(tmp_path):
    assert jc._clean_executables(tmp_path / "nope") == []


def test_clean_keep_zero_removes_all(tmp_path):
    base = tmp_path / "E2"
    now = time.time()
    for i in range(3):
        _mk(base / f"p{i}", now - i)
    assert len(jc._clean_executables(base, keep=0)) == 3
    assert list(base.iterdir()) == []


def test_clean_also_handles_loose_files(tmp_path):
    base = tmp_path / "E3"
    base.mkdir()
    f1 = base / "a.log"; f1.write_text("old")
    os.utime(f1, (1000, 1000))
    f2 = base / "b.log"; f2.write_text("new")
    removed = jc._clean_executables(base, keep=1)
    assert [p.name for p in removed] == ["a.log"]
    assert f2.exists()


def test_list_volumes_filters_prefix(monkeypatch):
    class R:
        returncode = 0
        stdout = "justcompiler-abc123\njustcompiler-def456\nother-vol\n\n"
    monkeypatch.setattr(jc.subprocess, "run",
                        lambda argv, **k: R() if "volume" in argv else type("X", (), {"stdout": ""})())
    vols = jc._list_docker_jc_volumes(["docker"])
    assert vols == ["justcompiler-abc123", "justcompiler-def456"]


def test_list_volumes_empty_on_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no docker")
    monkeypatch.setattr(jc.subprocess, "run", boom)
    assert jc._list_docker_jc_volumes(["docker"]) == []
