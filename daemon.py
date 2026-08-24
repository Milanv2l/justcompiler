"""HTTP daemon for zero-intervention builds (v2.10.0).

Usage: python3 justcompiler.py serve [--port 7400]
Endpoints:
  GET  /health           → {"ok":true,"version":"..."}
  POST /build            → start a build {"url":"...","branch":"...","target":"..."}
                         → 202 {"id":"abc123","status_url":"/status/abc123"}
  GET  /status/<id>      → JSON summary of the run
  GET  /artifacts/<id>   → list artifact files
"""
import http.server
import json
import threading
import uuid

_lock = threading.Lock()
_results = {}   # id -> result dict


def _handler_class(get_version, execute_fn):
    """Create a handler class bound to the version string and execute_build."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, code, data):
            body = json.dumps(data, indent=2, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"ok": True,
                                 "version": get_version(),
                                 "active_builds": len(_results)})
            elif self.path.startswith("/status/"):
                bid = self.path.rsplit("/", 1)[-1]
                with _lock:
                    r = _results.get(bid)
                if r:
                    self._json(200, r.get("summary", r))
                else:
                    self._json(404, {"error": "unknown build id"})
            elif self.path.startswith("/artifacts/"):
                bid = self.path.rsplit("/", 1)[-1]
                with _lock:
                    r = _results.get(bid)
                if r and r.get("artifacts_dir"):
                    import os
                    d = r["artifacts_dir"]
                    files = []
                    for root, dirs, fnames in os.walk(d):
                        for f in fnames:
                            fp = os.path.join(root, f)
                            files.append(os.path.relpath(fp, d))
                    self._json(200, {"artifacts_dir": d, "files": files})
                else:
                    self._json(404, {"error": "no artifacts"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/build":
                self._json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except Exception:
                self._json(400, {"error": "invalid JSON"})
                return
            url_or_path = data.get("url") or data.get("path")
            if not url_or_path:
                self._json(400, {"error": "missing 'url' or 'path'"})
                return
            bid = uuid.uuid4().hex[:12]
            with _lock:
                _results[bid] = {"status": "queued", "id": bid}

            def worker():
                res = execute_fn(
                    url_or_path,
                    branch=data.get("branch"),
                    target_override=data.get("target"),
                    all_targets=data.get("all_targets", False))
                with _lock:
                    res["id"] = bid
                    _results[bid] = res
            threading.Thread(target=worker, daemon=True).start()

            self._json(202, {"id": bid, "status": "queued",
                             "status_url": f"/status/{bid}"})

    return Handler


def serve(port: int = 7400, execute_fn=None, version_fn=None):
    """Blocking entry point for the HTTP daemon."""
    from http.server import HTTPServer
    version_str = version_fn or (lambda: "unknown")
    exec_fn = execute_fn or (lambda *a, **k: {})
    handler = _handler_class(version_str, exec_fn)
    server = HTTPServer(("127.0.0.1", port), handler)
    print(f"JustCompiler daemon listening on http://localhost:{port}")
    print(f"  POST /build          Start a new build")
    print(f"  GET  /health         Health check")
    print(f"  GET  /status/<id>    Build status")
    print(f"Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
