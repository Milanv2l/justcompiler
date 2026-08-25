# JustCompiler Engine API

Local HTTP service so **desktop apps and scripts** can use the JustCompiler
engine: *repo in → artifact out*. The server binds `127.0.0.1` only — it is
never exposed to your network.

## Quickstart

**1. Get the engine** (requires Python 3.10+ and
[Docker](https://docs.docker.com/get-docker/)):

```bash
git clone https://github.com/Milanv2l/justcompiler.git
cd justcompiler
```

**2. Start the API server:**

```bash
python3 justcompiler.py serve              # default port 7400
# options: --port 8080   --max-builds 2    (parallel sandbox builds)
```

**3. Check it's alive:**

```bash
curl localhost:7400/api/v1/health
# {"ok": true, "version": "2.11.0", "active_builds": 0}
```

That first launch may build its Docker sandbox image once (~5–10 min);
after that builds start immediately. Build output lands in `./EXECUTABLE/`
relative to where you started the server — run it from a workspace dir you
control.

---

## Concepts

- Every build is a **job**: `queued → running → success | partial |
  build_failed | invalid_input | cancelled`. Poll or subscribe (SSE) until
  the status is terminal, then fetch artifacts.
- `partial` means the sandbox run failed but artifacts were still produced.
- `invalid_input` means the URL/path could not be resolved at all.
- Artifacts are files inside the job's `artifacts_dir`; internal
  bookkeeping (`build.log`, `build_manifest.json`, `summary.json`,
  `_bundle.tar.gz`, `failure_report.txt`) is never listed.

### Auth (optional)

If the file `~/.justcompiler/api_token` exists and is non-empty, every
endpoint except `OPTIONS` and `/health` requires:

```
X-Auth-Token: <token>
# or
Authorization: Bearer <token>
```

Delete/empty the file to disable auth. Unauthorized requests get
`401 {"error": "unauthorized"}`.

### CORS

Every response carries `Access-Control-Allow-Origin: *` (+ methods/headers),
so browser-based shells (Electron/Tauri webviews) can call the API directly.
`OPTIONS` preflight returns `204`.

---

## Endpoints

### GET /api/v1/health
```json
{"ok": true, "version": "2.11.0", "active_builds": 0}
```

### POST /api/v1/build
```json
{"url": "https://github.com/user/repo",
 "branch": "main",
 "target": "tauri.conf.json",
 "all_targets": false}
```
- `url` may also be a local filesystem path (key `"path"` works too).
- `branch`, `target`, `all_targets` are optional; omit them for defaults.
- Response `202`:
```json
{"id": "a1b2c3d4e5f6", "status": "queued",
 "status_url": "/api/v1/build/a1b2c3d4e5f6",
 "events_url": "/api/v1/events"}
```
- Missing body/URL → `400 {"error": "missing 'url' or 'path'"}`.

### GET /api/v1/build/<id>
A real success response:

```json
{
  "id": "a1b2c3d4e5f6",
  "status": "success",
  "url": "https://github.com/user/repo",
  "branch": null,
  "target": null,
  "all_targets": false,
  "created_at": 1776000000.1,
  "started_at": 1776000000.4,
  "finished_at": 1776000063.9,
  "exit_code": 0,
  "summary": {
    "status": "success",
    "error_class": "",
    "target": "meson.build",
    "toolchain": {"java": null},
    "commit": "9f2c1ab",
    "duration_s": 63.5,
    "artifacts_dir": "/home/me/proj/EXECUTABLE/app_20260824_120000",
    "artifacts": ["mangojuice", "mangojuice.deb"],
    "logs": [".../EXECUTABLE/app_20260824_120000/build.log",
             ".../EXECUTABLE/app_20260824_120000/build_log.txt"],
    "possible_runtime_deps": []
  },
  "artifacts_dir": "/home/me/proj/EXECUTABLE/app_20260824_120000",
  "log_tail": ["Cloned (main @ 9f2c1ab)", "Build selected: meson.build"]
}
```

Failed jobs include `"error"` when the engine itself raised, and
`summary.failure_report` with the path to the collected crash report.
Unknown id → `404`.

### GET /api/v1/builds
List all known jobs (newest last), each with a 5-line log tail:
```json
{"jobs": [ {...}, ... ]}
```

### GET /api/v1/build/<id>/artifacts
```json
{"id": "...", "status": "success",
 "artifacts_dir": "/home/me/proj/EXECUTABLE/app_20260824_120000",
 "files": [{"name": "app.deb", "size": 8123456,
            "download": "/api/v1/build/<id>/artifacts/app.deb"}]}
```

### GET /api/v1/build/<id>/artifacts/<name>
Binary download. Path traversal outside the artifacts folder is rejected
with `404`. Nonexistent name → `404 {"error": "no such artifact"}`.

### POST /api/v1/build/<id>/cancel
Cancels a queued job immediately; requests cancellation of a running build
(kills the active sandbox container). Returns `200` with a `message`, or
`409 {"message": "job already success"}` if already finished.

### DELETE /api/v1/build/<id>?force=1&purge_artifacts=1
Forgets the job → `200 {"deleted": true, ...}`. Refuses while running with
`409 {"error": "still running — cancel first or use ?force=1"}` unless
`force=1`; with `purge_artifacts=1` also removes the build folder under
`./EXECUTABLE/`. Unknown id → `404`.

### GET /api/v1/events  (Server-Sent Events)
Live stream of job-state changes and log lines:
```
data: {"type": "log", "t": 1776000001.0, "line": "Cloned (...)"}
data: {"type": "job", "id": "...", "status": "running"}
data: {"type": "log", "t": 1776000002.3, "line": "[2/5] Compiling..."}
```
New connections replay the last ~30 log lines for context; a keepalive
comment is sent every 15s. With `--max-builds > 1` log lines from
concurrent builds interleave — match on job events, or read per-job
`log_tail` from the status endpoint instead.

### Error responses (all endpoints)

| Code | Body | Meaning |
|------|------|---------|
| 400 | `{"error": "missing 'url' or 'path'"}` / `{"error": "invalid JSON"}` | bad request |
| 401 | `{"error": "unauthorized"}` | token required/wrong |
| 404 | `{"error": "unknown build id"}` / `{"error": "no such artifact"}` / `{"error": "not found"}` | nothing there |
| 409 | `{"message": ...}` / `{"error": "still running ..."}` | conflicting state |

## Legacy aliases (v2.10)

`POST /build`, `GET /status/<id>`, `GET /artifacts/<id>` still work but new
integrations should use `/api/v1/*`.

---

## Building an app against it

### Python in-process (no HTTP)

Prefer a native embed? The engine imports cleanly without any TUI code:

```python
import core
from runner import execute_build          # scanner/jcconfig also importable
core.UI.bind(lambda ev: print(ev))        # optional event sink
result = execute_build("/path/or/repo-url")   # same dict as POST /build
```

### Python over HTTP (30 seconds, stdlib only)

Copy **one file** — `client.py` from the repo root — next to your app (or
add the repo dir to `sys.path`); it has zero dependencies:

```python
from client import JustCompiler

jc = JustCompiler()                       # http://127.0.0.1:7400
job = jc.build("https://github.com/user/repo")
final = job.wait(on_log=print)            # blocks until terminal state

if final["status"] == "success":
    path = job.download_artifact("app.deb", dest_dir="./out")
    print("got:", path)

# non-blocking alternative — live updates via SSE:
for evt in jc.events():
    if evt["type"] == "log":
        print(evt["line"])

job.cancel()                              # or job.delete(purge_artifacts=True)
```

Errors raise `client.EngineError` (`.code`) / `client.NotFoundError`;
`wait(timeout=...))` raises `TimeoutError`.

### JavaScript / Electron / Tauri webview

```js
const BASE = "http://127.0.0.1:7400";
// optional: headers: {"X-Auth-Token": token}

async function buildRepo(url, onLog) {
  const { id } = await (await fetch(`${BASE}/api/v1/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  })).json();

  const es = new EventSource(`${BASE}/api/v1/events`);
  es.onmessage = (m) => {
    const evt = JSON.parse(m.data);
    if (evt.type === "log") onLog?.(evt.line);
    if (evt.type === "job" && evt.id === id && isTerminal(evt.status)) {
      es.close();
      return pollArtifacts(id);
    }
  };
}
const isTerminal = (s) => !["queued", "running"].includes(s);

async function pollArtifacts(id) {
  const { files } = await (await fetch(
    `${BASE}/api/v1/build/${id}/artifacts`)).json();
  // each file: {name, size, download: "/api/v1/build/<id>/artifacts/<name>"}
  return files;
}
// binary save in Electron main process:
// const buf = await (await fetch(BASE + file.download)).arrayBuffer();
```

### curl walkthrough

```bash
ID=$(curl -s -XPOST localhost:7400/api/v1/build \
     -d '{"url":"https://github.com/user/repo"}' | jq -r .id)

curl -s localhost:7400/api/v1/build/$ID | jq .status       # repeat while queued/running
curl -s localhost:7400/api/v1/build/$ID/artifacts | jq .   # list files
curl -sOJ localhost:7400/api/v1/build/$ID/artifacts/app.deb

curl -s -N localhost:7400/api/v1/events                    # live logs
```

## Notes & limits

- Builds take as long as they take (real compiles, minutes not ms); the
  queue serialises them by default (`--max-builds`).
- The daemon never touches your host besides writing build output into
  `./EXECUTABLE/` relative to where you started it.
- The engine ships its own updater; `python3 justcompiler.py` (no args)
  opens the interactive TUI on the same install.
