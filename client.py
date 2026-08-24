"""JustCompiler client — tiny stdlib-only wrapper around the Engine API.

Usage (repo in, file out):

    from client import JustCompiler

    jc = JustCompiler()                       # http://127.0.0.1:7400
    job = jc.build("https://github.com/user/repo")
    final = job.wait()                        # polls until terminal state
    if final["status"] == "success":
        path = job.download_artifact(final["summary"]["artifacts"][0])
        print("got:", path)

Works with any desktop app: import it, or talk plain HTTP per docs/API.md.
"""
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path


class EngineError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"[{code}] {message}")
        self.code = code


class NotFoundError(EngineError):
    def __init__(self, message="not found"):
        super().__init__(404, message)


class JustCompiler:
    """HTTP client for a running `justcompiler.py serve` instance."""

    def __init__(self, base_url: str = "http://127.0.0.1:7400",
                 token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------- plumbing
    def _req(self, method: str, path: str, body: dict | None = None,
             stream: bool = False):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Auth-Token", self.token)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode() or "{}")
            except Exception:
                payload = {"error": str(e)}
            msg = payload.get("error") or str(payload)
            raise NotFoundError(msg) if e.code == 404 else \
                EngineError(e.code, msg)
        if stream:
            return resp
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}

    # ------------------------------------------------------------- api
    def health(self) -> dict:
        return self._req("GET", "/api/v1/health")

    def build(self, url_or_path: str, branch: str | None = None,
              target: str | None = None, all_targets: bool = False,
              wait: bool = False, poll_interval: float = 1.5,
              timeout: float | None = None, on_log=None) -> "BuildJob":
        payload = {"url": url_or_path}
        if branch:
            payload["branch"] = branch
        if target:
            payload["target"] = target
        if all_targets:
            payload["all_targets"] = True
        r = self._req("POST", "/api/v1/build", payload)
        job = BuildJob(self, r["id"])
        if wait:
            job.wait(poll_interval=poll_interval, timeout=timeout,
                     on_log=on_log)
        return job

    def get_build(self, build_id: str) -> dict:
        return self._req("GET", f"/api/v1/build/{build_id}")

    def list_builds(self) -> list[dict]:
        return self._req("GET", "/api/v1/builds").get("jobs", [])

    def events(self):
        """Yield SSE event dicts until the connection closes."""
        resp = self._req("GET", "/api/v1/events", stream=True)
        try:
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
        finally:
            resp.close()


class BuildJob:
    def __init__(self, engine: JustCompiler, build_id: str):
        self.engine = engine
        self.id = build_id
        self.last: dict = {}

    @property
    def status(self) -> str | None:
        return self.last.get("status")

    def refresh(self) -> dict:
        self.last = self.engine.get_build(self.id)
        return self.last

    def done(self) -> bool:
        return self.status in ("success", "partial", "build_failed",
                               "invalid_input", "cancelled")

    def wait(self, poll_interval: float = 1.5, timeout: float | None = None,
             on_log=None) -> dict:
        """Block until the job reaches a terminal state."""
        t0 = time.time()
        seen = 0
        while True:
            snap = self.refresh()
            logs = snap.get("log_tail") or []
            if on_log:
                for line in logs[seen:]:
                    on_log(line)
            seen = max(seen, len(logs)) if logs else 0
            if self.done():
                return snap
            if timeout is not None and time.time() - t0 > timeout:
                raise TimeoutError(f"build {self.id} not finished in {timeout}s")
            time.sleep(max(0.2, poll_interval))

    def artifacts(self) -> list[dict]:
        r = self.engine._req("GET", f"/api/v1/build/{self.id}/artifacts")
        return r.get("files", [])

    def download_artifact(self, name: str, dest_dir: str | Path = ".") -> Path:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        safe = name.replace("\\", "/")
        out = dest / Path(safe).name
        tmp = out.with_suffix(out.suffix + ".part")
        url = (f"{self.engine.base_url}/api/v1/build/{self.id}"
               f"/artifacts/{urllib.request.quote(safe)}")
        req = urllib.request.Request(url)
        if self.engine.token:
            req.add_header("X-Auth-Token", self.engine.token)
        with urllib.request.urlopen(req,
                                    timeout=self.engine.timeout) as resp, \
                open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        tmp.replace(out)
        return out

    def cancel(self) -> dict:
        return self.engine._req("POST", f"/api/v1/build/{self.id}/cancel")

    def delete(self, force: bool = False,
               purge_artifacts: bool = False) -> dict:
        qs = []
        if force:
            qs.append("force=1")
        if purge_artifacts:
            qs.append("purge_artifacts=1")
        suffix = ("?" + "&".join(qs)) if qs else ""
        return self.engine._req("DELETE",
                                f"/api/v1/build/{self.id}{suffix}")
