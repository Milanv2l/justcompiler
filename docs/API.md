# JustCompiler Engine API

Local HTTP service so **desktop apps and scripts** can use the JustCompiler
engine: *repo in → artifact out*. The server binds `127.0.0.1` only — it is
never exposed to your network.

```
python3 justcompiler.py serve [--port 7400] [--max-builds 1]
```

- Base URL: `http://127.0.0.1:7400`
- All responses are JSON unless noted; every response carries permissive CORS
  headers (`Access-Control-Allow-Origin: *`), so Electron/Tauri shells work.
- Builds run in a queue (`--max-builds`, default 1) because the Docker
  salvage layer tracks one active sandbox at a time.

## Auth (optional)

If the file `~/.justcompiler/api_token` exists and is non-empty, every
endpoint except `OPTIONS` and `/health` requires:

```
X-Auth-Token: <token>
# or
Authorization: Bearer <token>
```

Delete/empty the file to disable auth.

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
- Response `202`:
```json
{"id": "a1b2c3d4e5f6", "status": "queued",
 "status_url": "/api/v1/build/a1b2c3d4e5f6",
 "events_url": "/api/v1/events"}
```

Job statuses: `queued → running → success | partial | build_failed |
invalid_input | cancelled`.

### GET /api/v1/builds
List all known jobs (newest last), each with a 5-line log tail.

### GET /api/v1/build/<id>
```json
{
  "id": "a1b2c3d4e5f6", "status": "success",
  "url": "...", "branch": null, "target": null,
  "created_at": 1756000000.1, "started_at": 1756000000.4,
  "finished_at": 1756000063.9, "exit_code": 0,
  "summary": {"status": "success", "artifacts": ["app.deb"], "...": "..."},
  "artifacts_dir": "/home/me/proj/EXECUTABLE/app_20260824_120000",
  "log_tail": ["Cloned (...)", "Build selected: ..."]
}
```

### GET /api/v1/build/<id>/artifacts
Downloadable files only — internal bookkeeping files (`build.log`,
`build_manifest.json`, `summary.json`, `_bundle.tar.gz`,
`failure_report.txt`) are excluded.
```json
{"id": "...", "status": "success",
 "files": [{"name": "app.deb", "size": 8123456,
            "download": "/api/v1/build/<id>/artifacts/app.deb"}]}
```

### GET /api/v1/build/<id>/artifacts/<name>
Binary download. Path traversal outside the artifacts folder is rejected.

### POST /api/v1/build/<id>/cancel
Cancels a queued job immediately; requests cancellation of a running build
(kills the active sandbox container). Returns `409` if already finished.

### DELETE /api/v1/build/<id>?force=1&purge_artifacts=1
Forgets the job. Refuses while running unless `force=1`; with
`purge_artifacts=1` also removes the build folder under `./EXECUTABLE/`.

### GET /api/v1/events  (Server-Sent Events)
Live stream of job-state changes and log lines:
```
data: {"type": "log", "t": 1756000001.0, "line": "Cloned (...)"}
data: {"type": "job", "id": "...", "status": "running"}
```
New connections replay the last ~30 log lines for context; a keepalive
comment is sent every 15s.

## Legacy aliases (v2.10)

`POST /build`, `GET /status/<id>`, `GET /artifacts/<id>` still work but new
integrations should use `/api/v1/*`.

## Python client

`client.py` ships next to the engine (stdlib-only, no dependencies):

```python
from client import JustCompiler

jc = JustCompiler()                       # http://127.0.0.1:7400
job = jc.build("https://github.com/user/repo")
final = job.wait(on_log=print)            # blocks until terminal state

if final["status"] == "success":
    path = job.download_artifact("app.deb", dest_dir="./out")
    print("got:", path)

# live updates instead of polling:
for evt in jc.events():
    if evt["type"] == "log":
        print(evt["line"])

job.cancel()                              # or job.delete(purge_artifacts=True)
```

## curl walkthrough

```bash
ID=$(curl -s -XPOST localhost:7400/api/v1/build \
     -d '{"url":"https://github.com/user/repo"}' | jq -r .id)

curl -s localhost:7400/api/v1/build/$ID | jq .status      # poll
curl -s localhost:7400/api/v1/build/$ID/artifacts | jq .  # list
curl -sOJ localhost:7400/api/v1/build/$ID/artifacts/app.deb

curl -s -N localhost:7400/api/v1/events                   # live logs
```

## Notes & limits

- One shared log buffer: with `--max-builds > 1` log lines from concurrent
  builds interleave in each job's tail; use SSE + artifact folders to split.
- The daemon never touches your host besides writing build output into
  `./EXECUTABLE/` relative to where you started it — run it from a workspace
  directory you control.
