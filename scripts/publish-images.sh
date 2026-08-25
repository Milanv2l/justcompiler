#!/usr/bin/env bash
# Publish the JustCompiler BASE sandbox image to a container registry.
#
# Images are tagged by CONTENT HASH, so any image pushed here is pulled
# automatically by installs whose hash matches (config keys `image_registry`
# and `pull_images`; see docker_manager.bootstrap_sandbox).
#
# The engine layer is intentionally NOT published: it is a tiny COPY of a
# handful of source files and rebuilds in under a second on every machine.
#
# Usage:
#   ./scripts/publish-images.sh
#
# Requires: docker login <registry>  (e.g. ghcr.io with a write:packages PAT)

set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRY="${IMAGE_REGISTRY:-ghcr.io/milanv2l/justcompiler}"
command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

read -r BASE_HASH BASE_DOCKERFILE < <(python3 - <<'PY'
import hashlib, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import docker_manager as dm
content = dm._base_dockerfile("ubuntu:24.04", "full")
h = hashlib.sha256(("ubuntu:24.04" + content).encode()).hexdigest()[:12]
f = tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False)
f.write(content)
f.close()
print(h, f.name)
PY
)
trap 'rm -f "$BASE_DOCKERFILE"' EXIT

LOCAL_BASE="justcompiler-base:${BASE_HASH}"
REMOTE_BASE="${REGISTRY}/justcompiler-base:${BASE_HASH}"

echo "== Building base image (${BASE_HASH}) =="
docker build -q -f "$BASE_DOCKERFILE" -t "$LOCAL_BASE" .
docker tag "$LOCAL_BASE" "$REMOTE_BASE"
docker push "$REMOTE_BASE"
echo "✓ pushed $REMOTE_BASE"
echo "Done. Installs with matching content hashes will pull this automatically."
