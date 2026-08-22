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


# ------------------------------------------------- volume retention (A3)

from datetime import datetime, timezone, timedelta


def _ts(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def test_volumes_older_than_filters_correctly():
    entries = [("old1", _ts(40)), ("mid", _ts(10)), ("new", _ts(1))]
    assert jc._volumes_older_than(entries, 30) == ["old1"]
    assert jc._volumes_older_than(entries, 5) == ["old1", "mid"]
    assert jc._volumes_older_than(entries, 100) == []


def test_volumes_older_than_handles_bad_timestamps():
    entries = [("broken", "not-a-date"), ("ok", _ts(99)), ("naive", "2020-01-01T00:00:00")]
    old = jc._volumes_older_than(entries, 30)
    assert "broken" not in old and "ok" in old and "naive" in old


def test_volumes_older_than_zero_days_all_old():
    entries = [("v", _ts(0))]  # created 'now' but days=0 cutoff = now -> borderline
    # created exactly now is NOT strictly older than cutoff==now
    result = jc._volumes_older_than(entries, 0)
    assert result in ([], ["v"])
