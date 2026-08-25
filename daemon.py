"""JustCompiler Engine API — local HTTP service for desktop apps & scripts.

Repo in, artifact out. Start with:

    python3 justcompiler.py serve [--port 7400] [--max-builds N]

Canonical API lives under /api/v1/ (legacy v2.10 paths still work).

Endpoints
---------
GET    /api/v1/health                     liveness + version
POST   /api/v1/build                      start build {url|path, branch?, target?, all_targets?}
GET    /api/v1/builds                     list known jobs
GET    /api/v1/build/<id>                 job status + summary + log tail
GET    /api/v1/build/<id>/log?offset=N    full per-job log (line paging)
GET    /api/v1/build/<id>/artifacts       list downloadable artifacts (+sha256)
GET    /api/v1/build/<id>/artifacts/<f>   download one artifact (binary,
                                          Content-Disposition filename)
POST   /api/v1/build/<id>/package         {"formats":["deb","flatpak",...]}
POST   /api/v1/build/<id>/cancel          cancel queued/running job
POST   /api/v1/inspect                    dry-run detection {url|path, branch?}
                                          (targets/platforms/java/branches)
DELETE /api/v1/build/<id>[?purge_artifacts=1]
GET    /api/v1/builds?status=&limit=&before=   filtered/paginated history
GET    /api/v1/events[?job=<id>&since=<seq>]   SSE with Last-Event-ID resume

Auth: if ~/.justcompiler/api_token exists and is non-empty, every endpoint
except OPTIONS and /health requires "X-Auth-Token: <token>" (or a Bearer
header). Server binds 127.0.0.1 only — never exposed to the network.
CORS: enabled for all origins so browser-based shells (Electron) work.
"""
import hashlib
import http.server
import json
import os
import queue as _queue
import re as _re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import quote as _urlquote

_lock = threading.RLock()
_jobs = {}            # id -> job dict
_LOG_BUF = deque(maxlen=4000)   # global captured log lines
_subscribers = set()  # SSE subscriber queues as (queue, job_filter|None)
_sem = None           # concurrency limiter (set in serve)
_cancel_requested = set()
_SEQ = 0              # monotonically increasing SSE/event id
_EVENT_BUF = deque(maxlen=3000)  # every published event, for SSE resume
_MAX_BUILDS = {"n": 1}
JOBS_FILE = Path.home() / ".justcompiler" / "jobs.json"

TERMINAL = {"success", "partial", "build_failed", "invalid_input", "cancelled"}


def _persist_jobs():
    """Best-effort durable registry so finished jobs survive restarts."""
    try:
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            payload = [public_job(j, log_tail=0)
                       for j in sorted(_jobs.values(),
                                       key=lambda x: x.get("created_at", 0))]
        tmp = JOBS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=str))
        tmp.replace(JOBS_FILE)
    except Exception:
        pass


def _load_jobs():
    """Restore persisted terminal jobs at boot; interrupt half-done ones."""
    try:
        raw = json.loads(JOBS_FILE.read_text())
    except Exception:
        return
    now = time.time()
    restored = 0
    with _lock:
        for saved in raw if isinstance(raw, list) else []:
            jid = str(saved.get("id") or "")
            if not jid or jid in _jobs:
                continue
            job = {
                "id": jid,
                "status": saved.get("status"),
                "url": saved.get("url"),
                "branch": saved.get("branch"),
                "target": saved.get("target"),
                "all_targets": bool(saved.get("all_targets")),
                "created_at": saved.get("created_at", now),
                "started_at": saved.get("started_at"),
                "finished_at": saved.get("finished_at"),
                "exit_code": saved.get("exit_code"),
                "summary": saved.get("summary"),
                "artifacts_dir": saved.get("artifacts_dir"),
                "packaging": saved.get("packaging"),
                "_log_start": len(_LOG_BUF),
            }
            if job["status"] not in TERMINAL or job["status"] == "cancelled":
                job.update(status="build_failed",
                           error="interrupted by daemon restart",
                           finished_at=now)
            _jobs[jid] = job
            restored += 1
    if restored:
        print(f"Restored {restored} previous job(s) from {JOBS_FILE}")

KNOWN_FORMATS = ("deb", "rpm", "appimage", "flatpak", "windows-exe")

_STAGE_KEYWORDS = (
    ("cloning",     ("clon",)),
    ("packaging",   ("packag", "deb:", "rpm:", "appimage", "flatpak",
                     "bundle", "extracting artifacts")),
    ("configuring", ("scanning", "selected", "java", "heap",
                     "sandbox image", "base environment", "engine",
                     "synchron", "test")),
)


def _classify_stage(line):
    """Map a free-form status/log line to a documented pipeline stage."""
    l = (line or "").lower()
    for stage, keys in _STAGE_KEYWORDS:
        if any(k in l for k in keys):
            return stage
    return None


def _sandbox_state(running):
    """'building' while a sandbox/container is being prepared, else 'ready'.
    Never raises - health must stay cheap and reliable."""
    try:
        import docker_manager as dm
        st = getattr(dm, "SANDBOX_STATE", None)
    except Exception:
        st = None
    if st and st.get("status") == "error":
        return "error"
    if running <= 0:
        return "ready"
    try:
        import docker_manager as dm
        name = dm.ACTIVE_RUN_NAME.get("name")
        if not name:
            return "building"          # image/bootstrap phase
        out = subprocess.run(
            dm.get_docker_cmd() + ["ps", "--filter", "name=^%s$" % name,
                                   "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return "ready" if out else "building"
    except Exception:
        return "ready"


def _sha256_file(fp):
    h = hashlib.sha256()
    try:
        with open(fp, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


# ---------------------------------------------------------------- sinks ----

class _Collector:
    """core.UI sink: capture logs globally + fan out to SSE clients."""

    def __call__(self, ev):
        line = ""
        if ev.get("event") == "log":
            p, m = ev.get("prefix") or "", ev.get("msg") or ""
            line = f"{p}: {m}" if p and m else (m or p)
        elif ev.get("event") == "panel":
            line = f"[{ev.get('title','')}] " + " | ".join(ev.get("lines", []))
        elif ev.get("event") == "success":
            line = f"OK: {ev.get('msg','')}"
        elif ev.get("event") == "error":
            line = f"ERROR: {ev.get('msg','')}"
        elif ev.get("event") == "warn":
            line = f"WARN: {ev.get('msg','')}"
        elif ev.get("event") == "info":
            line = str(ev.get("msg", ""))
        elif ev.get("event") == "progress":
            pct, txt = ev.get("pct"), ev.get("text") or ""
            line = f"{txt} ({pct:.0f}%)" if isinstance(pct, (int, float)) else txt
        if not line:
            return
        stamp = time.time()
        entry = {"t": stamp, "line": line}
        with _lock:
            _LOG_BUF.append(entry)
            running = [jid for jid, x in _jobs.items()
                       if x.get("status") == "running"]
            jtag = ev.get("_job")
            if jtag and jtag not in _jobs:
                jtag = None
            if not jtag:
                jtag = running[0] if len(running) == 1 else None
            target_jid = jtag or (running[0] if len(running) == 1 else None)
            if target_jid and target_jid in _jobs:
                st = _classify_stage(line)
                if st:
                    _jobs[target_jid]["_stage"] = st
        _publish({"type": "log", "job": jtag, **entry})


def _publish(evt: dict):
    global _SEQ
    with _lock:
        _SEQ += 1
        evt = {"seq": _SEQ, **evt}
        _EVENT_BUF.append(evt)
    dead = []
    for sub in list(_subscribers):
        q, filt = sub
        if filt and not (
                (evt.get("type") == "job" and evt.get("id") == filt) or
                (evt.get("type") == "log" and evt.get("job") == filt)):
            continue
        try:
            q.put_nowait(evt)
        except Exception:
            dead.append(sub)
    for sub in dead:
        with _lock:
            _subscribers.discard(sub)


def _set_job(jid: str, **fields):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return
        j.update(fields)
        snap = public_job(j)
    _publish({"type": "job", "id": jid,
              "status": snap.get("status"), "phase": snap.get("phase"),
              "stage": snap.get("stage"),
              "packaging": snap.get("packaging")})
    _persist_jobs()


def public_job(j: dict, log_tail: int = 40) -> dict:
    """Job dict -> client-safe view."""
    out = {
        "id": j["id"],
        "status": j.get("status"),
        "url": j.get("url"),
        "branch": j.get("branch"),
        "target": j.get("target"),
        "all_targets": j.get("all_targets", False),
        "created_at": j.get("created_at"),
        "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
        "exit_code": j.get("exit_code"),
        "summary": j.get("summary"),
        "artifacts_dir": j.get("artifacts_dir"),
    }
    if j.get("cancelled"):
        out["cancelled"] = True
    if j.get("_stage"):
        out["stage"] = j["_stage"]
    if j.get("packaging"):
        out["packaging"] = {k: v for k, v in j["packaging"].items()
                            if not str(k).startswith("_")}
    if j.get("status") == "queued":
        try:                                  # caller holds _lock
            order = sorted((x["id"] for x in _jobs.values()
                            if x.get("status") == "queued"),
                           key=lambda i: _jobs[i].get("created_at", 0))
            out["queue_position"] = order.index(j["id"]) + 1
        except Exception:
            pass
    start = j.get("_log_start", 0)
    try:
        tail = [e["line"] for e in list(_LOG_BUF)[start:]][-log_tail:]
    except Exception:
        tail = []
    out["log_tail"] = tail
    if j.get("error"):
        out["error"] = j["error"]
    return out


# ---------------------------------------------------------------- runner ---

def _run_job(jid: str, params: dict, execute_fn):
    """Worker: wait for slot, run execute_build, record result."""
    assert _sem is not None
    with _sem:
        with _lock:
            j = _jobs.get(jid)
            if not j or j.get("status") not in ("queued",):
                return
            if jid in _cancel_requested:
                _cancel_requested.discard(jid)
                j.update(status="cancelled", finished_at=time.time())
                snap = public_job(j)
            else:
                j["status"] = "running"
                j["_log_start"] = len(_LOG_BUF)
                j["started_at"] = time.time()
                j["_stage"] = "configuring"
                snap = public_job(j)
        _publish({"type": "job", "id": jid, "status": snap.get("status"),
                  "phase": snap.get("phase")})
        if snap.get("status") == "cancelled":
            return
        try:
            res = execute_fn(
                params.get("url") or params.get("path"),
                branch=params.get("branch"),
                target_override=params.get("target"),
                all_targets=bool(params.get("all_targets", False)))
            status = res.get("status", "build_failed")
            _set_job(jid, status=status, _stage="finished",
                     summary=res.get("summary"),
                     artifacts_dir=res.get("artifacts_dir"),
                     exit_code=res.get("exit_code"),
                     finished_at=time.time())
        except Exception as e:                       # engine blew up mid-run
            _set_job(jid, status="build_failed",
                     error=str(e), finished_at=time.time())


def _submit(params: dict, execute_fn) -> str:
    jid = uuid.uuid4().hex[:12]
    now = time.time()
    job = {
        "id": jid,
        "status": "queued",
        "url": params.get("url") or params.get("path"),
        "branch": params.get("branch"),
        "target": params.get("target"),
        "all_targets": bool(params.get("all_targets", False)),
        "created_at": now,
        "_log_start": len(_LOG_BUF),
    }
    with _lock:
        _jobs[jid] = job
    _persist_jobs()
    _publish({"type": "job", "id": jid, "status": "queued", "phase": None})
    threading.Thread(target=_run_job, args=(jid, params, execute_fn),
                     daemon=True, name=f"jc-build-{jid}").start()
    return jid


def _artifact_files(artifacts_dir: str | None) -> list[dict]:
    if not artifacts_dir:
        return []
    base = Path(artifacts_dir)
    if not base.exists():
        return []
    skip = {"_bundle.tar.gz", "build_manifest.json", "summary.json",
            "build.log", "build_log.txt", "failure_report.txt"}
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f in skip:
                continue
            fp = Path(root) / f
            try:
                st = fp.stat()
            except OSError:
                continue
            out.append({
                "name": str(fp.relative_to(base)),
                "size": st.st_size,
                "download": None,   # filled by handler with full URL path
            })
    out.sort(key=lambda x: x["name"])
    return out


# ---------------------------------------------------------------- handler --

def _handler_class(get_version, execute_fn, token_getter):

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "JustCompiler"

        def log_message(self, fmt, *args):
            pass

        # -- helpers -----------------------------------------------
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, X-Auth-Token, Authorization")

        def _json(self, code, data):
            body = json.dumps(data, indent=2, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def _authorized(self) -> bool:
            tok = token_getter() if token_getter else None
            if not tok:
                return True
            supplied = self.headers.get("X-Auth-Token") or ""
            if not supplied:
                auth = self.headers.get("Authorization") or ""
                if auth.startswith("Bearer "):
                    supplied = auth[7:]
            return supplied == tok

        def _route(self):
            path = self.path.split("?", 1)
            clean = path[0].rstrip("/") or "/"
            query = path[1] if len(path) > 1 else ""
            qs = {}
            for part in query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    qs[k] = v
            return clean, qs

        # -- verbs -------------------------------------------------
        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            clean, qs = self._route()
            if clean in ("/health", "/api/v1/health"):
                with _lock:
                    queued = sum(1 for j in _jobs.values()
                                 if j.get("status") == "queued")
                    running = sum(1 for j in _jobs.values()
                                  if j.get("status") == "running")
                sandbox = _sandbox_state(running)
                reason = ""
                if sandbox == "error":
                    try:
                        import docker_manager as dm
                        reason = getattr(dm, "SANDBOX_STATE", {}).get(
                            "reason", "")
                    except Exception:
                        pass
                payload = {
                    "ok": True,
                    "version": get_version(),
                    "active_builds": queued + running,
                    "queue_length": queued,
                    "max_builds": _MAX_BUILDS["n"],
                    "sandbox": sandbox,
                }
                if reason:
                    payload["sandbox_reason"] = reason
                self._json(200, payload)
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            if clean == "/api/v1/builds":
                want = {s for s in qs.get("status", "").split(",") if s}
                try:
                    limit = max(0, int(qs.get("limit", "")))
                except ValueError:
                    limit = 0
                before = qs.get("before")
                with _lock:
                    ordered = sorted(_jobs.values(),
                                     key=lambda x: x.get("created_at", 0))
                    if before and before in _jobs:
                        cut = _jobs[before].get("created_at", 0)
                        ordered = [j for j in ordered
                                   if j.get("created_at", 0) < cut]
                    if want:
                        ordered = [j for j in ordered
                                   if j.get("status") in want]
                    if limit:
                        ordered = ordered[-limit:]
                    jobs = [public_job(j, log_tail=5) for j in ordered]
                self._json(200, {"jobs": jobs})
            elif clean.startswith("/api/v1/build/"):
                rest = clean[len("/api/v1/build/"):]
                if rest.endswith("/artifacts"):
                    self._artifacts(rest[:-len("/artifacts")])
                elif rest.endswith("/log"):
                    self._log(rest[:-len("/log")], qs)
                else:
                    self._status(rest)
            elif clean.startswith("/status/"):            # legacy alias
                self._status(clean[len("/status/"):])
            elif clean.startswith("/artifacts/"):         # legacy alias
                self._artifacts(clean[len("/artifacts/"):])
            elif clean == "/api/v1/events":
                self._events(qs)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            clean, _qs = self._route()
            if clean in ("/health", "/api/v1/health"):
                self.do_GET()
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            m_cancel = "/cancel"
            if clean.startswith("/api/v1/build/") and clean.endswith(m_cancel):
                self._cancel(clean[len("/api/v1/build/"):-len(m_cancel)])
                return
            m_pkg = "/package"
            if clean.startswith("/api/v1/build/") and clean.endswith(m_pkg):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._json(400, {"error": "invalid JSON"})
                    return
                self._package(clean[len("/api/v1/build/"):-len(m_pkg)],
                              data if isinstance(data, dict) else {})
                return
            if clean == "/api/v1/inspect":
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._json(400, {"error": "invalid JSON"})
                    return
                self._inspect(data if isinstance(data, dict) else {})
                return
            if clean in ("/api/v1/build", "/build"):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._json(400, {"error": "invalid JSON"})
                    return
                if not isinstance(data, dict) or \
                        not (data.get("url") or data.get("path")):
                    self._json(400,
                               {"error": "missing 'url' or 'path'"})
                    return
                jid = _submit(data, execute_fn)
                self._json(202, {"id": jid, "status": "queued",
                                 "status_url": f"/api/v1/build/{jid}",
                                 "events_url": "/api/v1/events"})
            else:
                self._json(404, {"error": "not found"})

        def do_DELETE(self):
            clean, qs = self._route()
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            if clean.startswith("/api/v1/build/"):
                self._delete(clean[len("/api/v1/build/"):], qs)
            else:
                self._json(404, {"error": "not found"})

        # -- endpoint bodies ---------------------------------------
        def _status(self, bid):
            with _lock:
                j = _jobs.get(bid)
                payload = public_job(j) if j else None
            if payload:
                self._json(200, payload)
            else:
                self._json(404, {"error": "unknown build id"})

        def _artifacts(self, bid):
            with _lock:
                j = _jobs.get(bid)
                adir = j.get("artifacts_dir") if j else None
                ready = bool(j and j.get("status") in TERMINAL
                             and j.get("status") != "cancelled")
            if not j:
                self._json(404, {"error": "unknown build id"})
                return
            files = _artifact_files(adir if ready else None)
            cache = None
            if ready and files and adir:
                with _lock:
                    cache = j.setdefault("_sha_cache", {})
            for f in files:
                f["download"] = f"/api/v1/build/{bid}/artifacts/{f['name']}"
                if cache is not None:
                    if f["name"] not in cache:
                        fp = _safe_join(adir, f["name"])
                        cache[f["name"]] = _sha256_file(fp) if fp else ""
                    f["sha256"] = cache.get(f["name"], "")
            self._json(200, {"id": bid, "status": j.get("status"),
                             "artifacts_dir": adir,
                             "files": files})

        def _log(self, bid, qs):
            """Full per-job log; line-based offset paging.
            Running jobs stream from the captured buffer; finished jobs read
            build_log.txt from the artifacts folder when present."""
            try:
                offset = max(0, int(qs.get("offset", "") or 0))
            except ValueError:
                offset = 0
            try:
                limit = min(5000, max(1, int(qs.get("limit", "") or 1000)))
            except ValueError:
                limit = 1000
            with _lock:
                j = _jobs.get(bid)
                if not j:
                    self._json(404, {"error": "unknown build id"})
                    return
                start = j.get("_log_start", 0)
                st = j.get("status")
                adir = j.get("artifacts_dir")
                buf_lines = [e["line"] for e in list(_LOG_BUF)[start:]]
            lines_all = buf_lines
            complete = st in TERMINAL
            if complete and adir:
                fp = Path(adir) / "build_log.txt"
                if fp.is_file():
                    try:
                        lines_all = fp.read_text(errors="replace").splitlines()
                    except OSError:
                        pass
                pkg = j.get("packaging") or {}
                pkg_from = pkg.get("_log_from")
                if isinstance(pkg_from, int):
                    # packaging ran after the build log was written: its
                    # output lives in the ring buffer from that point on
                    lines_all = lines_all + [
                        e["line"] for e in list(_LOG_BUF)[max(0, pkg_from):]]
            page = lines_all[offset:offset + limit]
            nxt = offset + len(page)
            self._json(200, {
                "id": bid, "status": st, "offset": offset,
                "lines": page, "next_offset": nxt,
                "total": len(lines_all), "complete": nxt >= len(lines_all),
            })

        def do_HEAD(self):
            clean, _qs = self._route()
            m = "/api/v1/build/"
            if clean.startswith(m) and "/artifacts/" in clean:
                bid, name = clean[len(m):].split("/artifacts/", 1)
                with _lock:
                    j = _jobs.get(bid)
                    adir = j.get("artifacts_dir") if j else None
                fp = _safe_join(adir, name) if adir else None
                if fp and fp.is_file():
                    self.send_response(200)
                    self.send_header("Content-Length",
                                     str(fp.stat().st_size))
                    fname = Path(name).name or "artifact"
                    ascii_safe = _re.sub(r'[^A-Za-z0-9._-]', "_", fname)
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="{ascii_safe}"; '
                        f"filename*=UTF-8''{_urlquote(fname)}")
                    self._cors()
                    self.end_headers()
                    return
            self.send_response(404)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _download(self, bid, name):
            with _lock:
                j = _jobs.get(bid)
                adir = j.get("artifacts_dir") if j else None
            fp = _safe_join(adir, name) if adir else None
            if not fp or not fp.is_file():
                self._json(404, {"error": "no such artifact"})
                return
            try:
                size = fp.stat().st_size
                self.send_response(200)
                ctype = ("application/octet-stream"
                         if fp.suffix not in (".json", ".txt")
                         else "text/plain; charset=utf-8")
                self.send_header("Content-Type", ctype)
                self._cors()
                self.send_header("Content-Length", str(size))
                fname = Path(name).name or "artifact"
                ascii_safe = _re.sub(r'[^A-Za-z0-9._-]', "_", fname)
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{ascii_safe}"; '
                    f"filename*=UTF-8''{_urlquote(fname)}")
                self.end_headers()
                with open(fp, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _cancel(self, bid):
            with _lock:
                j = _jobs.get(bid)
                if not j:
                    self._json(404, {"error": "unknown build id"})
                    return
                st = j.get("status")
                if st == "queued":
                    _cancel_requested.add(bid)
                    j["status"] = "cancelled"
                    j["finished_at"] = time.time()
                    ok, msg = True, "queued job cancelled"
                    snap = public_job(j)
                elif st == "running":
                    _cancel_requested.add(bid)
                    ok, msg = True, "cancellation requested"
                    snap = public_job(j)
                else:
                    ok, msg = False, f"job already {st}"
                    snap = public_job(j)
            if ok:
                _persist_jobs()
                _publish({"type": "job", "id": bid,
                          "status": snap.get("status"),
                          "phase": snap.get("phase"),
                          "stage": snap.get("stage"),
                          "packaging": snap.get("packaging")})
                if st == "running":
                    import docker_manager as dm
                    dm.cancel_active_run()
            self._json(200 if ok else 409, {"id": bid, "message": msg})

        def _delete(self, bid, qs):
            with _lock:
                j = _jobs.pop(bid, None)
            if not j:
                self._json(404, {"error": "unknown build id"})
                return
            if j.get("status") == "running" and \
                    qs.get("force") not in ("1", "true"):
                with _lock:
                    _jobs[bid] = j
                self._json(409, {"error": "still running — cancel first "
                                         "or use ?force=1"})
                return
            if j.get("status") == "running":
                import docker_manager as dm
                dm.cancel_active_run()
            _persist_jobs()
            removed = False
            if qs.get("purge_artifacts") in ("1", "true") and \
                    j.get("artifacts_dir"):
                base = Path(j["artifacts_dir"])
                # only ever remove inside ./EXECUTABLE
                if base.exists() and base.resolve().parent.name == "EXECUTABLE":
                    shutil.rmtree(base, ignore_errors=True)
                    removed = True
            self._json(200, {"id": bid, "deleted": True,
                             "purged_artifacts": removed})

        def _package(self, bid, data):
            """Trigger packaging (deb/rpm/appimage/flatpak/windows-exe) on a
            finished job's artifacts. Async: progress lands in
            job["packaging"] and produced files join the artifacts listing."""
            fmts = data.get("formats") or []
            if not isinstance(fmts, list) or not fmts or \
                    not all(f in KNOWN_FORMATS for f in fmts):
                self._json(400,
                           {"error": "formats must be a non-empty subset "
                                     f"of {list(KNOWN_FORMATS)}"})
                return
            with _lock:
                j = _jobs.get(bid)
                if not j:
                    self._json(404, {"error": "unknown build id"})
                    return
                st = j.get("status")
                adir = j.get("artifacts_dir")
                if st not in TERMINAL or st == "cancelled":
                    self._json(409, {"error": f"job is {st}; package a "
                                              f"finished build"})
                    return
                if not adir or not Path(adir).is_dir():
                    self._json(409, {"error": "no artifacts to package"})
                    return
                prev = j.get("packaging") or {}
                if prev.get("status") in ("queued", "running"):
                    self._json(409, {"error": "packaging already in progress"})
                    return
                j["packaging"] = {"status": "queued", "formats": fmts}
                snap = public_job(j)
            _persist_jobs()
            _publish({"type": "job", "id": bid, "status": snap.get("status"),
                      "stage": snap.get("stage"),
                      "packaging": snap.get("packaging")})

            def worker():
                with _lock:
                    j = _jobs.get(bid)
                    if not j:
                        return
                    j["packaging"]["status"] = "running"
                    j["packaging"]["_log_from"] = len(_LOG_BUF)
                    j["_stage"] = "packaging"
                    start = len(_LOG_BUF)
                import docker_manager as dm
                ok = False
                err = ""
                try:
                    ok = dm.run_packaging_container(
                        Path(adir), ",".join(fmts),
                        name=f"jc_pkg_{uuid.uuid4().hex[:8]}",
                        on_line=lambda l: self._pkg_log(bid, start, l))
                except Exception as e:
                    err = str(e)
                with _lock:
                    j = _jobs.get(bid)
                    if j:
                        p = j.get("packaging") or {}
                        p["status"] = "done" if ok else "failed"
                        if err:
                            p["error"] = err
                        if j.get("_stage") == "packaging":
                            j["_stage"] = "finished"
                        snap = public_job(j)
                _persist_jobs()
                _publish({"type": "job", "id": bid,
                          "status": snap.get("status"),
                          "stage": snap.get("stage"),
                          "packaging": snap.get("packaging")})

            threading.Thread(target=worker, daemon=True,
                             name=f"jc-pkg-{bid}").start()
            self._json(202, {"id": bid, "packaging": j["packaging"],
                             "status_url": f"/api/v1/build/{bid}"})

        def _pkg_log(self, bid, start, line):
            """Route packaging output into the log buffer + SSE as job logs."""
            if not line:
                return
            entry = {"t": time.time(), "line": str(line).rstrip()}
            with _lock:
                _LOG_BUF.append(entry)
            _publish({"type": "log", "job": bid, **entry})

        def _inspect(self, data):
            """Dry-run project detection: same logic as the build path,
            stopping before compiling. Synchronous (a fresh clone may take
            seconds); repeat calls hit the shared clone cache."""
            raw = data.get("url") or data.get("path")
            if not raw:
                self._json(400, {"error": "missing 'url' or 'path'"})
                return
            import runner as rn
            import scanner as sc
            commit = ""
            try:
                if rn._is_git_url(raw):
                    target, _used, commit = rn._clone_to_cache(
                        raw, data.get("branch"))
                    branches = None
                    default_branch = None
                    try:
                        import justcompiler as jc
                        default_branch, others = jc.fetch_remote_git_info(raw)
                        branches = ([default_branch] + others) if others \
                            else [default_branch]
                    except Exception:
                        branches = None
                else:
                    target = Path(raw)
                    branches = None
                    default_branch = None
                if not target.exists():
                    self._json(400, {"error": f"path not found: {raw}"})
                    return
                targets = sc._scan_targets(target)
                overrides = sc.load_project_config(target)
                selected = overrides.get("target") or (
                    sc._auto_select_target(target, targets)
                    if targets else "")
                java = overrides.get("java_version") or \
                    sc._detect_java_version(target)
                payload = {
                    "ok": True,
                    "url": str(raw),
                    "commit": commit,
                    "targets": [
                        {"name": t["name"], "platform": t["platform"],
                         "tool": t.get("tool", ""), "dir": t["dir"]}
                        for t in targets],
                    "selected": selected,
                    "java": java,
                    "overrides": overrides,
                }
                if branches:
                    payload["branches"] = branches
                    payload["default_branch"] = default_branch
                self._json(200, payload)
            except Exception as e:
                self._json(400, {"error": f"inspect failed: {e}"})

        def _safe_dl_route(self, clean):
            m = "/api/v1/build/"
            if not clean.startswith(m) or "/artifacts/" not in clean:
                return None
            bid, name = clean[len(m):].split("/artifacts/", 1)
            return bid, name

        def _events(self, qs):
            """SSE stream with resume support.

            Every event carries an incrementing `seq` and is written with an
            `id:` line. Clients reconnecting send Last-Event-ID (or ?since=)
            and receive exactly the events after that point. ?job=<id> limits
            both replay and live flow to one job.
            """
            try:
                since = int(qs.get("since") or
                            self.headers.get("Last-Event-ID") or 0)
            except ValueError:
                since = 0
            with _lock:
                since = min(since, max(_SEQ, 0))   # restart-safe clamp
            job_filter = qs.get("job") or None

            def frame(evt):
                return (f"id: {evt['seq']}\n"
                        f"data: {json.dumps(evt)}\n\n").encode()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()
            q: _queue.SimpleQueue = _queue.SimpleQueue()
            with _lock:
                if since > 0:
                    backlog = [dict(e) for e in list(_EVENT_BUF)
                               if e["seq"] > since]
                else:
                    # first connect: some context, not the whole buffer
                    backlog = [dict(e) for e in list(_EVENT_BUF)[-50:]]
                _subscribers.add((q, job_filter))
            try:
                for e in backlog:
                    if job_filter and not (
                            (e.get("type") == "job" and e.get("id") == job_filter) or
                            (e.get("type") == "log" and e.get("job") == job_filter)):
                        continue
                    self.wfile.write(frame(e))
                self.wfile.flush()
                while True:
                    try:
                        evt = q.get(timeout=15)
                        self.wfile.write(frame(evt))
                        self.wfile.flush()
                    except _queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _lock:
                    _subscribers.discard((q, job_filter))

        def do_GET_dispatch(self):     # pragma: no cover (kept explicit)
            pass

    # route binary downloads inside GET dispatch cleanly
    orig_do_GET = Handler.do_GET

    def do_GET(self):
        clean, _qs = self._route()
        dl = self._safe_dl_route(clean)
        if dl and not clean.rstrip("/").endswith("/artifacts"):
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            self._download(*dl)
            return
        orig_do_GET(self)

    Handler.do_GET = do_GET
    return Handler


def _safe_join(base_dir: str | None, name: str) -> Path | None:
    """Resolve name inside base_dir; block traversal."""
    if not base_dir:
        return None
    base = Path(base_dir).resolve()
    try:
        fp = (base / name).resolve()
    except Exception:
        return None
    if base == fp or base not in fp.parents:
        return None
    return fp


def _token_from_file(path: Path) -> str | None:
    try:
        tok = path.read_text().strip()
        return tok or None
    except OSError:
        return None


def serve(port: int = 7400, execute_fn=None, version_fn=None,
          max_concurrent: int = 1, token_file: str | None = None,
          bind_host: str = "127.0.0.1"):
    """Blocking entry point for the Engine API."""
    global _sem
    from http.server import ThreadingHTTPServer
    version_str = version_fn or (lambda: "unknown")
    exec_fn = execute_fn or (lambda *a, **k: {})

    tdir = Path(token_file) if token_file else \
        Path.home() / ".justcompiler" / "api_token"
    _tok_holder = {"path": tdir}

    def token_getter():
        return _token_from_file(_tok_holder["path"])

    core_ui = None
    try:
        import core as core_mod
        core_ui = _Collector()
        core_mod.UI.bind(core_ui)
    except Exception:
        pass

    _load_jobs()
    _persist_jobs()
    _MAX_BUILDS["n"] = max(1, int(max_concurrent))
    _sem = threading.BoundedSemaphore(_MAX_BUILDS["n"])
    handler = _handler_class(version_str, exec_fn, token_getter)
    server = ThreadingHTTPServer((bind_host, port), handler)
    server.daemon_threads = True

    tok_note = ("  auth: token REQUIRED (~/.justcompiler/api_token)"
                if token_getter() else "  auth: none (localhost only)")
    print(f"JustCompiler Engine API listening on http://{bind_host}:{port}")
    print(f"  POST /api/v1/build                  repo in → job id out")
    print(f"  GET  /api/v1/build/<id>             status + summary + logs")
    print(f"  GET  /api/v1/build/<id>/artifacts      file list")
    print(f"  GET  /api/v1/build/<id>/artifacts/<f>  download artifact")
    print(f"  GET  /api/v1/events                 live SSE stream (resume)")
    print(f"  GET  /api/v1/build/<id>/log?offset=N   full per-job log")
    print(f"  POST /api/v1/build/<id>/package     deb/rpm/appimage/flatpak")
    print(tok_note)
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            import core as core_mod
            core_mod.UI.unbind()
        except Exception:
            pass
