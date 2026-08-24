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
GET    /api/v1/build/<id>/artifacts       list downloadable artifacts
GET    /api/v1/build/<id>/artifacts/<f>   download one artifact (binary)
POST   /api/v1/build/<id>/cancel          cancel queued/running job
DELETE /api/v1/build/<id>[?purge_artifacts=1]
GET    /api/v1/events                     SSE stream (job + log events)

Auth: if ~/.justcompiler/api_token exists and is non-empty, every endpoint
except OPTIONS and /health requires "X-Auth-Token: <token>" (or a Bearer
header). Server binds 127.0.0.1 only — never exposed to the network.
CORS: enabled for all origins so browser-based shells (Electron) work.
"""
import http.server
import json
import os
import queue as _queue
import shutil
import threading
import time
import uuid
from collections import deque
from pathlib import Path

_lock = threading.RLock()
_jobs = {}            # id -> job dict
_LOG_BUF = deque(maxlen=4000)   # global captured log lines
_subscribers = set()  # SSE subscriber queues
_sem = None           # concurrency limiter (set in serve)
_cancel_requested = set()

TERMINAL = {"success", "partial", "build_failed", "invalid_input", "cancelled"}


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
        if not line:
            return
        stamp = time.time()
        entry = {"t": stamp, "line": line}
        with _lock:
            _LOG_BUF.append(entry)
        _publish({"type": "log", **entry})


def _publish(evt: dict):
    dead = []
    for q in list(_subscribers):
        try:
            q.put_nowait(evt)
        except Exception:
            dead.append(q)
    for q in dead:
        with _lock:
            _subscribers.discard(q)


def _set_job(jid: str, **fields):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return
        j.update(fields)
        snap = public_job(j)
    _publish({"type": "job", "id": jid,
              "status": snap.get("status"), "phase": snap.get("phase")})


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
            _set_job(jid, status=status,
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
                    active = sum(1 for j in _jobs.values()
                                 if j.get("status") in ("queued", "running"))
                self._json(200, {"ok": True, "version": get_version(),
                                 "active_builds": active})
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            if clean == "/api/v1/builds":
                with _lock:
                    jobs = [public_job(j, log_tail=5)
                            for j in sorted(_jobs.values(),
                                            key=lambda x: x.get("created_at", 0))]
                self._json(200, {"jobs": jobs})
            elif clean.startswith("/api/v1/build/"):
                rest = clean[len("/api/v1/build/"):]
                if rest.endswith("/artifacts"):
                    self._artifacts(rest[:-len("/artifacts")])
                else:
                    self._status(rest)
            elif clean.startswith("/status/"):            # legacy alias
                self._status(clean[len("/status/"):])
            elif clean.startswith("/artifacts/"):         # legacy alias
                self._artifacts(clean[len("/artifacts/"):])
            elif clean == "/api/v1/events":
                self._events()
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
            for f in files:
                f["download"] = f"/api/v1/build/{bid}/artifacts/{f['name']}"
            self._json(200, {"id": bid, "status": j.get("status"),
                             "artifacts_dir": adir,
                             "files": files})

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
                _publish({"type": "job", "id": bid,
                          "status": snap.get("status"),
                          "phase": snap.get("phase")})
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

        def _safe_dl_route(self, clean):
            m = "/api/v1/build/"
            if not clean.startswith(m) or "/artifacts/" not in clean:
                return None
            bid, name = clean[len(m):].split("/artifacts/", 1)
            return bid, name

        def _events(self):
            """SSE stream: job state changes + live log lines."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()
            q: _queue.SimpleQueue = _queue.SimpleQueue()
            with _lock:
                _subscribers.add(q)
            try:
                # replay recent log context so late joiners catch up
                with _lock:
                    backlog = [dict(e) for e in list(_LOG_BUF)[-30:]]
                for e in backlog:
                    self.wfile.write(
                        f"data: {json.dumps({'type': 'log', **e})}\n\n"
                        .encode())
                self.wfile.flush()
                while True:
                    try:
                        evt = q.get(timeout=15)
                        self.wfile.write(
                            f"data: {json.dumps(evt)}\n\n".encode())
                        self.wfile.flush()
                    except _queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _lock:
                    _subscribers.discard(q)

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

    _sem = threading.BoundedSemaphore(max(1, int(max_concurrent)))
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
    print(f"  GET  /api/v1/events                 live SSE stream")
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
