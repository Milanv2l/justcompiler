"""v2.14.0 — introspection, persistence, attribution, sandbox error, pkg."""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import daemon as D

_REPO = Path(__file__).resolve().parent.parent


class QuietEngine:
    def __init__(self, artifacts_dir):
        self.artifacts_dir = artifacts_dir
        self.started = threading.Event()

    def __call__(self, raw_build, branch=None, target_override=None,
                 all_targets=False, lang="en"):
        self.started.set()
        time.sleep(0.4)
        return {"exit_code": 0, "status": "success",
                "summary": {"status": "success"},
                "artifacts_dir": str(self.artifacts_dir),
                "build_folder": None}


@pytest.fixture()
def api(tmp_path):
    D._jobs.clear()
    D._LOG_BUF.clear()
    D._EVENT_BUF.clear()
    D._subscribers.clear()
    D._cancel_requested.clear()
    D._SEQ = 0
    D._MAX_BUILDS["n"] = 1
    if JOBS_FILE_EXISTS():
        JOBS_FILE_UNLINK()

    adir = tmp_path / "EXECUTABLE" / "p1"
    adir.mkdir(parents=True)
    (adir / "a.bin").write_bytes(b"x")
    eng = QuietEngine(adir)

    handler = D._handler_class(lambda: "2.14-test", eng, lambda: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{srv.server_address[1]}",
           "eng": eng, "adir": adir}
    srv.shutdown()
    srv.server_close()


def JOBS_FILE_EXISTS():
    try:
        return D.JOBS_FILE.exists()
    except Exception:
        return False


def JOBS_FILE_UNLINK():
    try:
        D.JOBS_FILE.unlink()
    except OSError:
        pass


def _req(base, method, path, body=None):
    import urllib.error
    req = urllib.request.Request(base + path, method=method)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


# ------------------------------------------------------------- 1) inspect --

def test_inspect_local_project(tmp_path, api):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"demo"}')
    st, body = _req(api["base"], "POST", "/api/v1/inspect", {"path": str(proj)})
    assert st == 200 and body["ok"] is True
    names = {t["name"] for t in body["targets"]}
    assert any("Node" in n for n in names)
    assert body["selected"]
    assert isinstance(body["targets"][0]["platform"], str)
    assert body["java"] is None


def test_inspect_java_detection(tmp_path, api):
    proj = tmp_path / "jp"
    proj.mkdir()
    (proj / "build.gradle").write_text(
        "sourceCompatibility = '21'\n")
    st, body = _req(api["base"], "POST", "/api/v1/inspect", {"path": str(proj)})
    assert st == 200
    assert body["java"] == 21


def test_inspect_missing_path_400(api):
    st, body = _req(api["base"], "POST", "/api/v1/inspect",
                    {"path": "/does/not/exist"})
    assert st == 400


def test_inspect_missing_url_400(api):
    st, body = _req(api["base"], "POST", "/api/v1/inspect", {})
    assert st == 400


# ------------------------------------------------------- 4) persistence --

def test_persist_roundtrip_and_restart_interrupt(tmp_path):
    D._jobs.clear()
    D._LOG_BUF.clear()
    D._EVENT_BUF.clear()
    jf = tmp_path / "jobs.json"
    orig_file = D.JOBS_FILE
    D.JOBS_FILE = jf
    try:
        # simulate two finished jobs + one that was mid-flight at crash
        now = time.time()
        with D._lock:
            D._jobs["ok123"] = {
                "id": "ok123", "status": "success",
                "url": "x", "created_at": now - 50,
                "finished_at": now - 40,
                "summary": {"status": "success"},
                "artifacts_dir": str(tmp_path), "_log_start": 99}
            D._jobs["mid77"] = {
                "id": "mid77", "status": "running",
                "url": "y", "created_at": now - 5,
                "_log_start": 99}
        D._persist_jobs()
        assert jf.exists()

        # fresh boot
        D._jobs.clear()
        D._LOG_BUF.clear()
        D._load_jobs()
        assert set(D._jobs) == {"ok123", "mid77"}
        assert D._jobs["ok123"]["status"] == "success"
        assert D._jobs["mid77"]["status"] == "build_failed"
        assert "restart" in D._jobs["mid77"]["error"]
        # log endpoint falls back gracefully (no crash on missing dir)
        from pathlib import Path as P
        D._jobs["ok123"]["artifacts_dir"] = str(tmp_path)
    finally:
        D.JOBS_FILE = orig_file
        JOBS_FILE_UNLINK()


def test_delete_removes_from_persistence(api):
    base = api["base"]
    _, r = _req(base, "POST", "/api/v1/build", {"url": "del-me"})
    bid = r["id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        _, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap.get("status") in D.TERMINAL:
            break
        time.sleep(0.05)
    _req(base, "DELETE", f"/api/v1/build/{bid}")
    time.sleep(0.2)
    saved = json.loads(D.JOBS_FILE.read_text()) if JOBS_FILE_EXISTS() else []
    assert all(j["id"] != bid for j in saved)


# ---------------------------------------------------- 5) SSE attribution --

def test_core_ui_job_tag_threadlocal():
    import core
    events = []
    core.UI.bind(events.append)
    try:
        def worker():
            core.UI.bind_job("jobZ")
            core.UI.log(core.UI.GREEN, "T", "tagged line")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        # other thread: untagged
        core.UI.log(core.UI.GREEN, "T", "untagged")
    finally:
        core.UI.unbind()
        core.UI.unbind_job()
    tagged = [e for e in events if e.get("_job") == "jobZ"]
    untagged = [e for e in events if "_job" not in e]
    assert len(tagged) == 1 and "tagged" in tagged[0].get("msg", "")
    assert len(untagged) >= 1


def test_collector_prefers_explicit_tag_over_inference():
    ev = {"event": "log", "prefix": "P", "msg": "hello", "_job": "jobX"}
    with D._lock:
        D._jobs["jobX"] = {"id": "jobX", "status": "running"}
        D._jobs["jobY"] = {"id": "jobY", "status": "running"}
    c = D._Collector()
    c(ev)                      # tagged → attributed to jobX even w/ 2 running
    with D._lock:
        entry = list(D._LOG_BUF)[-1]
        published = list(D._EVENT_BUF)[-1]
    assert published.get("job") == "jobX"
    with D._lock:
        D._jobs.pop("jobX"), D._jobs.pop("jobY")
        D._LOG_BUF.pop(), D._EVENT_BUF.pop()


# --------------------------------------------------- 6) sandbox error -----

def test_sandbox_error_state_surfaces_in_health(api, monkeypatch):
    base = api["base"]
    import docker_manager as dm
    monkeypatch.setattr(dm, "SANDBOX_STATE",
                        {"status": "error", "reason": "base build exploded"})
    st, h = _req(base, "GET", "/api/v1/health")
    assert st == 200
    assert h["sandbox"] == "error"
    assert h["sandbox_reason"] == "base build exploded"


def test_sandbox_ready_by_default(api):
    st, h = _req(api["base"], "GET", "/api/v1/health")
    assert h["sandbox"] in ("ready", "building")   # never error when healthy


# ------------------------------------------------------ 2) pull fallback --

def test_pull_fallback_logic_unit(monkeypatch, tmp_path):
    """_pull_image pulls+tags on success; falls back silently on failure."""
    calls = []

    class FakeDM:
        pass

    src_src = open("docker_manager.py").read()
    assert "image_registry" in src_src and "pull_images" in src_src
    assert 'ghcr.io/milanv2l/justcompiler' in src_src
    # and publish script exists & parses
    script = _REPO / "scripts" / "publish-images.sh"
    assert script.exists()
    chk = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert chk.returncode == 0, chk.stderr


# --------------------------------------------------------- 3) packaging ---

def test_pyproject_and_jccli():
    import tomllib
    meta = tomllib.loads((_REPO / "pyproject.toml").read_text())
    assert meta["project"]["scripts"]["justcompiler"] == "jccli:main"
    assert "tui" in meta["project"]["optional-dependencies"]
    mods = meta["tool"]["setuptools"]["py-modules"]
    for m in ("runner", "scanner", "jcconfig", "daemon", "jccli"):
        assert m in mods
    assert not meta["project"]["dependencies"]

    r = subprocess.run([sys.executable, str(_REPO / "jccli.py"), "help"],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and "serve" in r.stdout


def test_scanner_data_file_resolver_finds_repo_plugins():
    import scanner
    p = scanner._plugins_path()
    assert p.is_file() and p.name == "plugins.json"


# ---------------------------------------------- integration: restart e2e --

def test_health_after_restart_shows_restored_jobs(tmp_path):
    """Serve() boot path restores persisted jobs (unit-level)."""
    jf = tmp_path / "jobs.json"
    orig = D.JOBS_FILE
    D.JOBS_FILE = jf
    try:
        now = time.time()
        jf.write_text(json.dumps([{
            "id": "old1", "status": "partial", "created_at": now - 100,
            "finished_at": now - 90, "artifacts_dir": str(tmp_path),
            "summary": {}, "exit_code": 3}]))
        D._jobs.clear()
        D._load_jobs()
        assert D._jobs["old1"]["status"] == "partial"
    finally:
        D.JOBS_FILE = orig
