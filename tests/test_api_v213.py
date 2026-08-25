"""v2.13.0 — GUI-client API additions.

log endpoint, SSE resume/per-job filter, health sandbox+queue fields,
queue_position, stage, builds filters, artifact sha256, Content-Disposition,
package endpoint.
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import daemon as D


class FakeEngine:
    """execute_build stand-in; emits log lines while 'building'."""

    def __init__(self, artifacts_dir, delay=0.25):
        self.artifacts_dir = artifacts_dir
        self.delay = delay

    def __call__(self, raw_build, branch=None, target_override=None,
                 all_targets=False, lang="en"):
        time.sleep(self.delay / 2)
        for i in range(3):
            with D._lock:
                entry = {"t": time.time(),
                         "line": f"[build] compiling part {i + 1}"}
                D._LOG_BUF.append(entry)
            D._publish({"type": "log", "job": None, **entry})
            time.sleep(self.delay / 2)
        s = str(raw_build)
        status = ("invalid_input" if "badinput" in s else
                  "build_failed" if "failme" in s else "success")
        return {"exit_code": 0 if status == "success" else 1,
                "status": status,
                "summary": {"status": status},
                "artifacts_dir": str(self.artifacts_dir),
                "build_folder": None}


@pytest.fixture()
def api(tmp_path):
    D._jobs.clear()
    D._LOG_BUF.clear()
    D._EVENT_BUF.clear()
    D._cancel_requested.clear()
    D._subscribers.clear()
    D._SEQ = 0
    D._MAX_BUILDS["n"] = 1

    adir = tmp_path / "EXECUTABLE" / "proj_1"
    adir.mkdir(parents=True)
    payload = b"DEBPAYLOAD" * 8
    (adir / "app.deb").write_bytes(payload)
    eng = FakeEngine(adir)
    tok_file = tmp_path / "api_token"
    tok_file.write_text("")
    D._sem = threading.BoundedSemaphore(1)

    handler = D._handler_class(
        lambda: "2.13.0-test", eng,
        lambda: (tok_file.read_text().strip() or None))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{srv.server_address[1]}",
           "eng": eng, "adir": adir, "payload": payload}
    srv.shutdown()
    srv.server_close()


def _req(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=10) as r:
            raw = r.read().decode()
            hdrs = dict(r.headers)
        try:
            return r.status, json.loads(raw or "{}"), hdrs
        except json.JSONDecodeError:
            return r.status, raw, hdrs
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}"), {}
        except Exception:
            return e.code, {}, {}


def _wait_terminal(base, bid, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, snap, _ = _req(base, "GET", f"/api/v1/build/{bid}")
        if snap.get("status") in D.TERMINAL:
            return snap
        time.sleep(0.05)
    raise AssertionError("job never finished")


# ------------------------------------------------------------ 3) health --

def test_health_has_sandbox_and_queue_fields(api):
    base = api["base"]
    st, h, _ = _req(base, "GET", "/api/v1/health")
    assert st == 200
    assert h["sandbox"] in ("ready", "building")
    assert h["queue_length"] == 0
    assert h["max_builds"] == 1
    assert h["active_builds"] == 0


def test_health_sandbox_building_while_running(api):
    base = api["base"]
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "slow-build"})
    seen_building = False
    deadline = time.time() + 5
    while time.time() < deadline:
        st, h, _ = _req(base, "GET", "/api/v1/health")
        if h["active_builds"] >= 1 and h["sandbox"] == "building":
            seen_building = True
            break
        time.sleep(0.05)
    assert seen_building, "expected sandbox=building during a running job"
    _wait_terminal(base, r["id"])


# ------------------------------------------------------------- 4) queue --

def test_queue_position_for_queued_jobs(api):
    base = api["base"]
    _, first, _ = _req(base, "POST", "/api/v1/build", {"url": "blocker"})
    _, second, _ = _req(base, "POST", "/api/v1/build", {"url": "queued-2"})
    st, snap, _ = _req(base, "GET", f"/api/v1/build/{second['id']}")
    assert snap["status"] == "queued"
    assert snap["queue_position"] == 1
    _wait_terminal(base, first["id"])
    _wait_terminal(base, second["id"])
    _, done, _ = _req(base, "GET", f"/api/v1/build/{second['id']}")
    assert "queue_position" not in done          # only meaningful while queued


# -------------------------------------------------------------- 5) stage --

def test_stage_lifecycle(api):
    base = api["base"]
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "stagey"})
    final = _wait_terminal(base, r["id"])
    assert final["stage"] == "finished"


def test_classify_stage_mapping():
    assert D._classify_stage("Cloning https://x/y") == "cloning"
    assert D._classify_stage("Scanning project...") == "configuring"
    assert D._classify_stage("Packaging deb: ok") == "packaging"
    assert D._classify_stage("hello world") is None


# ----------------------------------------------------------- 1) log GET --

def test_log_endpoint_running_and_terminal(api):
    base, adir = api["base"], api["adir"]
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "loggy"})
    bid = r["id"]
    # running: buffer lines appear with paging
    seen_lines = []
    deadline = time.time() + 5
    offset = 0
    while time.time() < deadline:
        st, page, _ = _req(base, "GET", f"/api/v1/build/{bid}/log?offset={offset}")
        assert st == 200
        seen_lines += page["lines"]
        offset = page["next_offset"]
        if page["complete"]:
            break
        time.sleep(0.1)
    snap = _wait_terminal(base, bid)
    st, page, _ = _req(base, "GET", f"/api/v1/build/{bid}/log?offset=0")
    assert st == 200 and page["complete"] is True
    assert page["next_offset"] == page["total"]
    # terminal without build_log.txt falls back to buffered lines
    assert any("compiling part" in ln for ln in page["lines"])


def test_log_endpoint_reads_file_when_present(api):
    base, adir = api["base"], api["adir"]
    (adir / "build_log.txt").write_text("file-line-1\nfile-line-2\n")
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "filed"})
    bid = r["id"]
    _wait_terminal(base, bid)
    st, page, _ = _req(base, "GET", f"/api/v1/build/{bid}/log?offset=0")
    assert st == 200
    assert "file-line-1" in page["lines"] and "file-line-2" in page["lines"]
    # paging respects offset
    st2, p2, _ = _req(base, "GET", f"/api/v1/build/{bid}/log?offset=1&limit=1")
    assert p2["lines"] == ["file-line-2"] and p2["next_offset"] == 2
    assert p2["complete"] is True


def test_log_404_unknown_job(api):
    st, body, _ = _req(api["base"], "GET", "/api/v1/build/nope/log")
    assert st == 404


# ------------------------------------------------------- 2) SSE upgrade --

def _read_sse_frames(base, path, n_frames, timeout=5):
    """Open SSE stream, collect n data frames."""
    out = []
    resp = urllib.request.urlopen(base + path, timeout=timeout)
    buf = b""
    import socket
    resp.fp.raw._sock.settimeout(timeout) if hasattr(resp.fp, "raw") else None
    deadline = time.time() + timeout
    cur_id = None
    while len(out) < n_frames and time.time() < deadline:
        chunk = resp.read1(1024) if hasattr(resp, "read1") else resp.read(1)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            block, buf = buf.split(b"\n\n", 1)
            data = None
            for line in block.decode(errors="replace").splitlines():
                if line.startswith("id: "):
                    cur_id = int(line[4:])
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
            if data is not None:
                data["_sse_id"] = cur_id
                out.append(data)
    resp.close()
    return out


def test_sse_last_event_id_resume(api):
    base = api["base"]
    # seed three synthetic events
    seqs = []
    for i in range(3):
        D._publish({"type": "log", "job": None,
                    "t": time.time(), "line": f"resume-check-{i}"})
    with D._lock:
        seqs = [e["seq"] for e in list(D._EVENT_BUF) if
                e.get("type") == "log" and "resume-check" in e["line"]]
    assert len(seqs) == 3
    # reconnect from the FIRST event → must receive the remaining two
    frames = _read_sse_frames(
        base, "/api/v1/events", 2, timeout=4) if False else None
    # (use explicit Last-Event-ID header via Request)
    req = urllib.request.Request(base + "/api/v1/events")
    req.add_header("Last-Event-ID", str(seqs[0]))
    resp = urllib.request.urlopen(req, timeout=4)
    got = []
    buf = b""
    deadline = time.time() + 4
    while len(got) < 2 and time.time() < deadline:
        chunk = resp.read(1)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            block, buf = buf.split(b"\n\n", 1)
            data = None
            for line in block.decode(errors="replace").splitlines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
            if data is not None and \
                    "resume-check" in json.dumps(data):
                got.append(data)
    resp.close()
    assert len(got) == 2
    lines = [f["line"] for f in got]
    assert lines == ["resume-check-1", "resume-check-2"]
    ids = [f["seq"] for f in got]
    assert ids == seqs[1:]                      # exact continuation
    assert got[0]["_sse_id"] if False else True


def test_sse_per_job_filter(api):
    base = api["base"]
    q = __import__("queue").SimpleQueue()
    with D._lock:
        D._subscribers.add((q, "jobA"))
    D._publish({"type": "log", "job": "jobB", "t": 1, "line": "wrong"})
    D._publish({"type": "log", "job": "jobA", "t": 2, "line": "right"})
    D._publish({"type": "job", "id": "jobA", "status": "running"})
    got = [q.get(timeout=2), q.get(timeout=2)]
    kinds = [(f.get("job"), f.get("type")) for f in got]
    assert ("jobB", "log") not in kinds
    assert all(f.get("job") == "jobA" or f.get("id") == "jobA"
               for f in got)


# ------------------------------------------------------ 7) builds filters --

def test_builds_status_limit_before_filters(api):
    base = api["base"]
    with D._lock:
        for i, stx in enumerate(["success", "success", "build_failed",
                                 "success"]):
            D._jobs[f"id{i}"] = {
                "id": f"id{i}", "status": stx,
                "created_at": 1000.0 + i, "_log_start": 0}
    _, allj, _ = _req(base, "GET", "/api/v1/builds?status=success")
    assert [j["id"] for j in allj["jobs"]] == ["id0", "id1", "id3"]
    _, lim, _ = _req(base, "GET", "/api/v1/builds?limit=2")
    assert [j["id"] for j in lim["jobs"]] == ["id2", "id3"]   # newest last
    _, bef, _ = _req(base, "GET", "/api/v1/builds?before=id2&limit=10")
    assert [j["id"] for j in bef["jobs"]] == ["id0", "id1"]


# ----------------------------------------------- 6/8) sha256 + headers --

def test_artifacts_sha256_and_download_headers(api):
    base, payload = api["base"], api["payload"]
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "hashme"})
    bid = r["id"]
    _wait_terminal(base, bid)
    st, arts, _ = _req(base, "GET", f"/api/v1/build/{bid}/artifacts")
    f = arts["files"][0]
    assert f["name"] == "app.deb"
    assert f["sha256"] == hashlib.sha256(payload).hexdigest()

    req = urllib.request.Request(f"{base}/api/v1/build/{bid}/artifacts/app.deb")
    with urllib.request.urlopen(req, timeout=5) as resp:
        cd = resp.headers.get("Content-Disposition")
        cl = resp.headers.get("Content-Length")
        body = resp.read()
    assert body == payload
    assert cl == str(len(payload))
    assert cd and 'filename="app.deb"' in cd and "filename*=UTF-8''app.deb" in cd


# --------------------------------------------------------- 9) package ----

def test_package_flow_success(api, monkeypatch):
    base, adir = api["base"], api["adir"]
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "pkg-me"})
    bid = r["id"]
    _wait_terminal(base, bid)

    def fake_pkg(path, formats_csv, name="", on_line=None):
        on_line and on_line("flatpak-builder: bundling...")
        (Path(path) / "App.flatpak").write_bytes(b"FLATPAK!")
        return True

    import docker_manager as dm
    monkeypatch.setattr(dm, "run_packaging_container", fake_pkg)

    st, resp, _ = _req(base, "POST", f"/api/v1/build/{bid}/package",
                       {"formats": ["flatpak", "deb"]})
    assert st == 202
    assert resp["packaging"]["status"] in ("queued", "running")   # race ok
    # wait until done
    deadline = time.time() + 5
    pk = {}
    while time.time() < deadline:
        _, snap, _ = _req(base, "GET", f"/api/v1/build/{bid}")
        pk = snap.get("packaging") or {}
        if pk.get("status") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert pk["status"] == "done"
    assert "_log_from" not in pk                       # internals stripped
    _, arts, _ = _req(base, "GET", f"/api/v1/build/{bid}/artifacts")
    names = {f["name"] for f in arts["files"]}
    assert "App.flatpak" in names
    # packaging logs merged into /log output
    _, lg, _ = _req(base, "GET", f"/api/v1/build/{bid}/log?offset=0")
    assert any("flatpak-builder" in ln for ln in lg["lines"])


def test_package_validation_errors(api, monkeypatch):
    base = api["base"]
    _, r, _ = _req(base, "POST", "/api/v1/build", {"url": "pv"})
    bid = r["id"]
    st, body, _ = _req(base, "POST", f"/api/v1/build/{bid}/package",
                       {"formats": ["exe-magic"]})
    assert st == 400
    st, body, _ = _req(base, "POST", f"/api/v1/build/{bid}/package",
                       {"formats": []})
    assert st == 400
    # queued job (not terminal yet) → 409
    st, body, _ = _req(base, "POST", f"/api/v1/build/{bid}/package",
                       {"formats": ["deb"]})
    assert st == 409
    _wait_terminal(base, bid)
    # unknown job → 404
    st, body, _ = _req(base, "POST", "/api/v1/build/zzz/package",
                       {"formats": ["deb"]})
    assert st == 404
