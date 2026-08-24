"""v2.11.0 Engine API + client tests."""
import json
import queue as q
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import daemon as D
import client as C


class FakeEngine:
    """execute_build stand-in driven by keywords in the input string."""

    def __init__(self, artifacts_dir):
        self.artifacts_dir = artifacts_dir
        self.delay = 0.25
        self.calls = []

    def __call__(self, raw_build, branch=None, target_override=None,
                 all_targets=False, lang="en"):
        self.calls.append(raw_build)
        time.sleep(self.delay)
        s = str(raw_build)
        if "badinput" in s:
            status = "invalid_input"
        elif "failme" in s:
            status = "build_failed"
        else:
            status = "success"
        return {"exit_code": {"success": 0}.get(status, 1),
                "status": status,
                "summary": {"status": status, "artifacts": ["app.deb"],
                            "target": target_override or "auto"},
                "artifacts_dir": str(self.artifacts_dir),
                "build_folder": None}


@pytest.fixture()
def api(tmp_path):
    D._jobs.clear()
    D._LOG_BUF.clear()
    D._cancel_requested.clear()
    D._subscribers.clear()

    adir = tmp_path / "EXECUTABLE" / "proj_20260824_120000"
    adir.mkdir(parents=True)
    (adir / "app.deb").write_bytes(b"DEBFILE" * 10)
    (adir / "notes.txt").write_text("hello")
    (adir / "build.log").write_text("internal noise")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")

    eng = FakeEngine(adir)
    tok_file = tmp_path / "api_token"
    tok_file.write_text("")
    D._sem = threading.BoundedSemaphore(2)

    handler = D._handler_class(
        lambda: "2.11.0-test", eng,
        lambda: (tok_file.read_text().strip() or None))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, eng, tok_file, tmp_path
    srv.shutdown()
    srv.server_close()


def _req(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        payload = e.read().decode() or "{}"
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {"raw": payload}
        return e.code, payload


# ------------------------------------------------------------ health/cors

def test_health_and_cors(api):
    base, *_ = api
    req = urllib.request.Request(base + "/api/v1/health")
    with urllib.request.urlopen(req, timeout=5) as r:
        body = json.loads(r.read())
        assert r.headers.get("Access-Control-Allow-Origin") == "*"
    assert body["ok"] is True and body["version"] == "2.11.0-test"


def test_options_preflight(api):
    base, *_ = api
    st, _ = _req(base, "OPTIONS", "/api/v1/build")
    assert st == 204


# ------------------------------------------------------------ build flow

def test_full_flow_submit_wait_artifacts_download(api):
    base, eng, *_ = api
    st, r = _req(base, "POST", "/api/v1/build",
                 {"url": "https://github.com/x/repo"})
    assert st == 202 and r["status"] == "queued" and r["id"]
    bid = r["id"]
    deadline = time.time() + 10
    final = None
    while time.time() < deadline:
        st, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        assert st == 200
        if snap["status"] in D.TERMINAL:
            final = snap
            break
        time.sleep(0.1)
    assert final and final["status"] == "success"
    assert final["summary"]["artifacts"] == ["app.deb"]
    assert isinstance(final["log_tail"], list)

    st, arts = _req(base, "GET", f"/api/v1/build/{bid}/artifacts")
    names = {f["name"] for f in arts["files"]}
    assert names == {"app.deb", "notes.txt"}          # build.log filtered
    dl = [f for f in arts["files"] if f["name"] == "app.deb"][0]["download"]
    with urllib.request.urlopen(base + dl, timeout=5) as resp:
        assert resp.read() == b"DEBFILE" * 10


def test_failed_status_still_lists_artifacts(api):
    base, eng, *_ = api
    _, r = _req(base, "POST", "/api/v1/build", {"url": "failme-project"})
    bid = r["id"]
    for _ in range(100):
        st, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap["status"] in D.TERMINAL:
            break
        time.sleep(0.05)
    assert snap["status"] == "build_failed"
    st, arts = _req(base, "GET", f"/api/v1/build/{bid}/artifacts")
    assert st == 200


def test_invalid_input_terminal(api):
    base, *_ = api
    _, r = _req(base, "POST", "/api/v1/build", {"url": "badinput-x"})
    bid = r["id"]
    for _ in range(100):
        _, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap["status"] in D.TERMINAL:
            break
        time.sleep(0.05)
    assert snap["status"] == "invalid_input"


def test_params_forwarded(api):
    base, eng, *_ = api
    _, r = _req(base, "POST", "/api/v1/build",
                {"url": "https://x/y", "branch": "dev",
                 "target": "cli", "all_targets": True})
    bid = r["id"]
    for _ in range(100):
        _, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap["status"] in D.TERMINAL:
            break
        time.sleep(0.05)
    assert eng.calls[-1].endswith("/y")


# -------------------------------------------------------------- validation

def test_missing_url_400(api):
    base, *_ = api
    st, r = _req(base, "POST", "/api/v1/build", {})
    assert st == 400


def test_unknown_id_404(api):
    base, *_ = api
    st, _ = _req(base, "GET", "/api/v1/build/nope")
    assert st == 404


def test_traversal_blocked(api):
    base, eng, *_tmp = api
    _, r = _req(base, "POST", "/api/v1/build",
                {"url": "https://x/trav"})
    bid = r["id"]
    for _ in range(100):
        _, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap["status"] in D.TERMINAL:
            break
        time.sleep(0.05)
    st, _r = _req(base, "GET",
                  f"/api/v1/build/{bid}/artifacts/../secret.txt")
    assert st == 404


# ------------------------------------------------------------------ legacy

def test_legacy_endpoints(api):
    base, eng, *_ = api
    st, r = _req(base, "POST", "/build", {"url": "legacy-repo"})
    assert st == 202 and r["id"]
    bid = r["id"]
    for _ in range(100):
        st, snap = _req(base, "GET", f"/status/{bid}")
        if snap.get("status") in D.TERMINAL:
            break
        time.sleep(0.05)
    assert snap["status"] == "success"
    st, arts = _req(base, "GET", f"/artifacts/{bid}")
    assert st == 200 and arts["files"]


# ------------------------------------------------------- cancel & delete

def test_cancel_queued_job(api):
    base, eng, *_ = api
    D._sem = threading.BoundedSemaphore(1)          # force strict queueing
    _, blocker = _req(base, "POST", "/api/v1/build", {"url": "blocker"})
    _, second = _req(base, "POST", "/api/v1/build", {"url": "queued-one"})
    st, r = _req(base, "POST",
                 f"/api/v1/build/{second['id']}/cancel")
    assert st == 200
    st, snap = _req(base, "GET", f"/api/v1/build/{second['id']}")
    assert snap["status"] == "cancelled"


def test_delete_job_and_purge_guard(api):
    base, eng, tokf, tmp_path = api
    _, r = _req(base, "POST", "/api/v1/build", {"url": "deleteme"})
    bid = r["id"]
    for _ in range(100):
        _, snap = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap["status"] in D.TERMINAL:
            break
        time.sleep(0.05)
    st, r = _req(base, "DELETE", f"/api/v1/build/{bid}")
    assert st == 200 and r["deleted"] is True
    st, _r = _req(base, "GET", f"/api/v1/build/{bid}")
    assert st == 404


# -------------------------------------------------------------------- auth

def test_token_auth(api):
    base, eng, tok_file, *_ = api
    tok_file.write_text("sekrit123")
    st, r = _req(base, "POST", "/api/v1/build", {"url": "authchk"})
    assert st == 401
    st, r = _req(base, "POST", "/api/v1/build", {"url": "authchk"},
                 headers={"X-Auth-Token": "wrong"})
    assert st == 401
    st, r = _req(base, "POST", "/api/v1/build", {"url": "authchk"},
                 headers={"X-Auth-Token": "sekrit123"})
    assert st == 202
    st, r2 = _req(base, "GET", "/api/v1/builds",
                  headers={"Authorization": "Bearer sekrit123"})
    assert st == 200 and any(j["id"] == r["id"] for j in r2["jobs"])
    # health stays open for liveness probes
    st, _h = _req(base, "GET", "/api/v1/health")
    assert st == 200


# --------------------------------------------------------------------- SSE

def test_sse_backlog_replay(api):
    base, *_ = api
    D._LOG_BUF.append({"t": time.time(), "line": "hello-sse"})
    with urllib.request.urlopen(base + "/api/v1/events", timeout=5) as r:
        assert r.headers.get("Content-Type").startswith("text/event-stream")
        line = r.readline().decode().strip()
    assert line.startswith("data: ") and "hello-sse" in line


# ------------------------------------------------------------------ client

def test_client_build_wait_download(api):
    base, eng, *_ = api
    jc = C.JustCompiler(base_url=base)
    assert jc.health()["ok"] is True
    lines = []
    job = jc.build("https://github.com/client/demo", target="cli",
                   on_log=lines.append, poll_interval=0.2, wait=True)
    assert job.done() and job.status == "success"
    arts = job.artifacts()
    assert {a["name"] for a in arts} == {"app.deb", "notes.txt"}
    out = job.download_artifact("app.deb")
    assert out.read_bytes() == b"DEBFILE" * 10


def test_client_cancel_and_delete(api):
    base, eng, *_ = api
    jc = C.JustCompiler(base_url=base)
    D._sem = threading.BoundedSemaphore(1)
    blocker = jc.build("blocker2")
    queued = jc.build("queued2")
    r = queued.cancel()
    assert r.get("message")
    assert queued.refresh()["status"] == "cancelled"
    blocker.wait(poll_interval=0.1, timeout=15)
    r = blocker.delete(purge_artifacts=True)
    assert r["deleted"] is True
    with pytest.raises(C.NotFoundError):
        blocker.refresh()


def test_safe_join_rejects_escape(tmp_path):
    base_dir = tmp_path / "a"
    base_dir.mkdir()
    assert D._safe_join(str(base_dir), "../evil") is None
    assert D._safe_join(str(base_dir), "ok.txt") == \
        (base_dir / "ok.txt").resolve()
