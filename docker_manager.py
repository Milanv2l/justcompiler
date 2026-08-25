import os
import sys
import subprocess
import shutil
import threading
import platform
import json
import time
import hashlib
import secrets
import re
from pathlib import Path
from core import UI, t

def _build_with_spinner(title: str, cmd_args: list) -> bool:
    """Run a docker build with a background spinner showing step progress."""
    cmd_args = list(cmd_args)
    spinner = UI.spinner(title)
    with spinner:
        proc = subprocess.Popen(
            cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            m = re.search(r"\[(\d+)/(\d+)\]", line)
            if m:
                step, total = int(m.group(1)), int(m.group(2))
                spinner.set_progress(step / total * 100)
                status = re.sub(r"^.*?\[(\d+/\d+)\]\s*", "", line).strip()[:50]
                spinner.text = f"{title}  [{step}/{total}] {status}"
            elif "ERROR" in line or "failed" in line.lower():
                spinner.fail()
        proc.wait()
        if proc.returncode != 0:
            spinner.fail()
    return proc.returncode == 0

def get_docker_cmd() -> list:
    docker_cmd = ["docker"]
    if platform.system() != "Windows":
        if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            if subprocess.run(["sudo", "-v"]).returncode == 0:
                docker_cmd = ["sudo", "docker"]
    return docker_cmd

def _compute_engine_hash(host_dir: Path) -> str:
    hasher = hashlib.sha256()
    for fname in ["core.py", "engine.py", "plugins.json", "docker_manager.py"]:
        fpath = host_dir / fname
        if fpath.exists():
            hasher.update(fpath.read_bytes())
    return hasher.hexdigest()[:16]

def _volume_name(target_path: Path) -> str:
    h = hashlib.sha256(str(target_path.resolve()).encode()).hexdigest()[:12]
    return f"justcompiler-{h}"

def _prune_old_images(docker_cmd: list, repo: str, keep_tag: str = "", keep: int = 2):
    """Remove old images of a repo by creation date, keeping the newest `keep` (plus keep_tag)."""
    try:
        listing = subprocess.run(
            docker_cmd + ["images", "--format", "{{.ID}}|{{.Repository}}:{{.Tag}}|{{.CreatedAt}}", repo],
            capture_output=True, text=True
        )
        if listing.returncode != 0 or not listing.stdout.strip():
            return
        entries = []
        for line in listing.stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3 and parts[1] != "<none>":
                entries.append((parts[0], parts[1], parts[2]))
        # Newest first by CreatedAt string (docker format is sortable enough for same-host images)
        entries.sort(key=lambda e: e[2], reverse=True)
        protected = {e[1] for e in entries[:keep]}
        if keep_tag:
            protected.add(keep_tag)
        for img_id, full_tag, _ in entries:
            if full_tag not in protected:
                subprocess.run(docker_cmd + ["rmi", "-f", img_id],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _sandbox_flags(java_version: int | None = None, cfg: dict | None = None, extra_env: dict | None = None) -> list:
    """Docker flags for env + sandbox hardening. Must appear BEFORE the image name."""
    flags = []
    # CI=1 keeps every toolchain non-interactive (pnpm 10 build-script
    # approvals otherwise block forever on a stdin-less container)
    flags += ["-e", "CI=true", "-e", "PYTHONUNBUFFERED=1",
              "-e", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1",
              "-e", "PUPPETEER_SKIP_DOWNLOAD=1"]
    if java_version:
        flags += ["-e", f"JC_JAVA_VERSION={java_version}"]
    for k, v in (extra_env or {}).items():
        flags += ["-e", f"{k}={v}"]
    cfg = cfg or {}
    if cfg.get("sandbox_network") is False:
        flags += ["--network", "none"]
    if cfg.get("memory_limit"):
        flags += ["--memory", str(cfg["memory_limit"])]
    if cfg.get("cpu_limit"):
        flags += ["--cpus", str(cfg["cpu_limit"])]
    return flags

ACTIVE_RUN_NAME = {"name": None}
SANDBOX_STATE = {"status": "ready", "reason": ""}   # ready | building | error

def run_packaging_container(artifacts_path: Path, formats_csv: str,
                            name: str, on_line=None) -> bool:
    """Run the engine image in --packaging mode against an existing
    artifacts folder (mounted read-write). Auto-rebuilds if stale."""
    host_dir = Path(__file__).resolve().parent
    image_tag = f"justcompiler-engine:{_compute_engine_hash(host_dir)}"

    # auto-rebuild engine image if it doesn't exist for current code
    check = subprocess.run(get_docker_cmd() + ["images", "-q", image_tag],
                           capture_output=True, text=True)
    if not check.stdout.strip():
        from core import UI as _UI
        _UI.info("Rebuilding sandbox image for packaging...")
        bootstrap_sandbox(
            target_path=Path("/tmp"), artifacts_path=Path("/tmp/jc_bootstrap"),
            run_tests=False, lang="en",
            set_status_fn=lambda s: None)

    tools = Path.home() / ".cache" / "justcompiler"
    tools.mkdir(parents=True, exist_ok=True)
    pkg_name = f"jc_pkg_{secrets.token_hex(4)}"
    cmd = get_docker_cmd() + [
        "run", "--rm", "--name", pkg_name,
        "-e", "CI=true", "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{Path(artifacts_path).resolve()}:/workspace/artifacts:z",
        "-v", f"{tools}:/root/.cache/justcompiler:z",
        image_tag, "--packaging", "--formats", formats_csv,
        "--name", name,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            bufsize=1, errors="replace")
    for line in proc.stdout:
        if on_line:
            on_line(line.rstrip("\n"))
        else:
            print(line, end="")
    proc.wait()
    return proc.returncode == 0


def cancel_active_run() -> bool:
    """Kill the currently running sandbox container, if any (TUI Cancel)."""
    name = ACTIVE_RUN_NAME.get("name")
    if not name:
        return False
    try:
        docker_cmd = ["docker"] if platform.system() == "Windows" else ["sudo", "docker"]
        subprocess.run(docker_cmd + ["rm", "-f", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def _gc_stale_runs(docker_cmd: list, max_age_h: float = 6.0):
    """Best-effort removal of leftover justcompiler_run_* containers from
    crashed sessions older than max_age_h hours."""
    try:
        res = subprocess.run(docker_cmd + ["ps", "-a", "--format",
                                           "{{.Names}}|{{.RunningFor}}",
                                           "--filter", "name=justcompiler_run_"],
                             capture_output=True, text=True)
        for line in res.stdout.splitlines():
            name, _, age = line.partition("|")
            if not name.startswith("justcompiler_run_"):
                continue
            if re.search(r"(\d+) hours?", age):
                try:
                    if float(re.search(r"(\d+) hours?", age).group(1)) >= max_age_h:
                        subprocess.run(docker_cmd + ["rm", "-f", name],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            elif "days" in age:
                subprocess.run(docker_cmd + ["rm", "-f", name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _base_dockerfile(base_image: str, profile: str = "full") -> str:
    """Dockerfile content for the sandbox base image.
    'full' preinstalls every supported toolchain; 'slim' ships only the
    bootstrap essentials and lets the engine's AI-RESCUE apt-install whatever
    the detected project actually needs (much faster first build)."""
    head = f"""FROM {base_image}
ARG DEBIAN_FRONTEND=noninteractive
"""
    venv = """
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel pyinstaller cx_Freeze
"""
    # rustup provides a current Rust/Cargo (distro cargo is years out of date
    # and fails on modern editions like edition2024)
    rustup = """RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"
"""
    if profile == "slim":
        body = """RUN apt-get update && apt-get install -y \\
    ca-certificates curl wget unzip zip jq git rsync python3 python3-pip python3-venv \\
    build-essential g++ pkg-config openjdk-21-jdk \\
    && rm -rf /var/lib/apt/lists/*
"""
        jdk = """RUN ln -sfn /usr/lib/jvm/java-21-openjdk-$(dpkg --print-architecture) /opt/jdk21
ENV JAVA_HOME=/opt/jdk21
ENV PATH="$JAVA_HOME/bin:$PATH"
"""
        return head + body + venv + rustup + jdk
    else:
        body = """RUN apt-get update && apt-get install -y \\
    curl wget unzip zip jq git rsync python3 python3-pip python3-venv build-essential g++ cmake \\
    qt6-base-dev qt6-tools-dev-tools openjdk-8-jdk openjdk-17-jdk openjdk-21-jdk openjdk-25-jdk maven gradle golang \\
    php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal apt-file \\
    libgtk-3-dev libgtk-4-dev libadwaita-1-dev libgee-0.8-dev blueprint-compiler \\
    rpm flatpak file desktop-file-utils xdg-utils \
    libgbm-dev libegl1-mesa-dev libgirepository1.0-dev libclang-dev libvte-dev \\
    libwebkit2gtk-4.1-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \\
    pkg-config libxdo-dev libgdk-pixbuf-xlib-2.0-dev libpango1.0-dev libcairo2-dev libatk1.0-dev \\
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
RUN npm install -g pnpm yarn
RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh && chmod +x dotnet-install.sh && ./dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet && rm dotnet-install.sh
"""
        jdk = """RUN ln -sfn /usr/lib/jvm/java-8-openjdk-$(dpkg --print-architecture) /opt/jdk8 \\
    && ln -sfn /usr/lib/jvm/java-17-openjdk-$(dpkg --print-architecture) /opt/jdk17 \\
    && ln -sfn /usr/lib/jvm/java-21-openjdk-$(dpkg --print-architecture) /opt/jdk21 \\
    && ln -sfn /usr/lib/jvm/java-25-openjdk-$(dpkg --print-architecture) /opt/jdk25
ENV JAVA_HOME=/opt/jdk21
ENV PATH="$JAVA_HOME/bin:$PATH"
"""
    return head + body + venv + rustup + jdk

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str, set_status_fn, base_image: str = "ubuntu:24.04", target_filter: str = "", java_version: int | None = None, extra_env: dict | None = None, project_name: str = "") -> bool | None:
    SANDBOX_STATE.update(status="building", reason="")
    if not shutil.which("docker"):
        UI.error(t('err_docker'))
        SANDBOX_STATE.update(status="error",
                             reason="docker CLI not found on PATH")
        return

    docker_cmd = get_docker_cmd()
    host_dir = Path(__file__).resolve().parent

    import scanner as _scanner
    plugins_path = _scanner._plugins_path()
    if not plugins_path.exists():
        default_plugins = [
            {"name": "Java (Gradle)", "tool": "gradle", "detect": ["build.gradle", "build.gradle.kts"], "wrapper": "gradlew", "out_dirs": ["build/libs", "dist"], "out_exts": [".jar", ".war"]},
            {"name": "Java (Maven)", "tool": "mvn", "detect": ["pom.xml"], "wrapper": "mvnw", "out_dirs": ["target"], "out_exts": [".jar", ".war"]},
            {"name": "Node.js", "tool": "npm", "detect": ["package.json"], "cmd_system": "DYNAMIC_JS_RESOLUTION", "out_dirs": ["dist", "build"], "out_exts": ["*DIR*"]},
            {"name": "Go", "tool": "go", "detect": ["go.mod"], "cmd_system": "DYNAMIC_GO_RESOLUTION", "out_dirs": ["build_output"], "out_exts": [""]},
            {"name": "Rust", "tool": "cargo", "detect": ["Cargo.toml"], "cmd_system": "cargo build --release", "out_dirs": ["target/release"], "out_exts": [""]}
        ]
        fallback_seed = host_dir / "plugins.json"
        try:
            fallback_seed.write_text(json.dumps(default_plugins, indent=4), encoding="utf-8")
            plugins_path = fallback_seed
        except OSError:
            pass

    image_tag = f"justcompiler-engine:{_compute_engine_hash(host_dir)}"

    # registry/pull settings readable regardless of engine-image freshness
    try:
        _cfg_all = json.loads((host_dir / "config.json").read_text(encoding="utf-8"))
    except Exception:
        _cfg_all = {}
    registry = str(_cfg_all.get("image_registry",
                                "ghcr.io/milanv2l/justcompiler")).strip().rstrip("/")
    pull_enabled = bool(_cfg_all.get("pull_images", True))

    def _pull_image(remote_ref, local_tag) -> bool:
        """Pull a prebuilt image; retag to local name. Never raises."""
        if not pull_enabled or "/" not in registry:
            return False
        try:
            set_status_fn(f"Pulling {remote_ref} ...")
            r = subprocess.run(docker_cmd + ["pull", remote_ref],
                               capture_output=True, text=True, timeout=1200)
            if r.returncode != 0:
                return False
            subprocess.run(docker_cmd + ["tag", remote_ref, local_tag],
                           capture_output=True)
            UI.success(f"Pulled prebuilt image {remote_ref}")
            return True
        except Exception as e:
            UI.warn(f"Image pull skipped ({e}); building locally")
            return False

    set_status_fn(t('docker_cache_check'))
    check_image = subprocess.run(docker_cmd + ["images", "-q", image_tag], capture_output=True, text=True)

    if not check_image.stdout.strip():

        # Optional sandbox hardening via config.json (all optional, safe defaults)
        try:
            cfg0 = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
        except Exception:
            cfg0 = {}
        profile = "slim" if cfg0.get("profile") == "slim" else "full"

        base_dockerfile_content = _base_dockerfile(base_image, profile)
        base_hash = hashlib.sha256((base_image + base_dockerfile_content).encode()).hexdigest()[:12]
        base_tag = f"justcompiler-base:{base_hash}"

        # STEP 1: Check if the heavy base environment (justcompiler-base) exists locally
        check_base = subprocess.run(docker_cmd + ["images", "-q", base_tag], capture_output=True, text=True)

        if not check_base.stdout.strip():
            remote_base = f"{registry}/justcompiler-base:{base_hash}"
            if _pull_image(remote_base, base_tag):
                pass
            else:
                set_status_fn(t('docker_building_base'))
                UI.info("Base image not found or changed. Rebuilding...")
                base_dockerfile_path = host_dir / "Dockerfile.base"
                ok_base = False
                try:
                    base_dockerfile_path.write_text(base_dockerfile_content, encoding="utf-8")
                    ok_base = _build_with_spinner(t('docker_building_base'), docker_cmd + ["build", "-f", str(base_dockerfile_path), "-t", base_tag, str(host_dir)])
                finally:
                    if base_dockerfile_path.exists(): base_dockerfile_path.unlink()
                if not ok_base:
                    SANDBOX_STATE.update(status="error",
                                         reason=f"base image build failed ({base_tag})")
                    return False
        else:
            UI.success(f"Base environment {base_tag} available")
        _prune_old_images(docker_cmd, "justcompiler-base", keep_tag=base_tag, keep=2)

        # STAP 2: Build the featherlight engine layer (< 0.5s, reuses local base)
        set_status_fn("Syncing engine layer...")
        
        dockerfile_content = f"""FROM {base_tag}
WORKDIR /workspace
COPY core.py engine.py plugins.json /workspace/
COPY entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh
ENTRYPOINT ["/workspace/entrypoint.sh"]
"""
        entrypoint_content = """#!/bin/bash
set -e
mkdir -p /workspace/artifacts /workspace/persist
if [ -n "$JC_JAVA_VERSION" ] && [ -x "/opt/jdk$JC_JAVA_VERSION/bin/java" ]; then
    export JAVA_HOME="/opt/jdk$JC_JAVA_VERSION"
    export PATH="$JAVA_HOME/bin:$PATH"
fi
if [ -n "$JC_GRADLE_HEAP" ]; then
    export GRADLE_OPTS="$GRADLE_OPTS -Dorg.gradle.jvmargs=-Xmx${JC_GRADLE_HEAP}g -XX:MaxMetaspaceSize=512m"
fi
echo "JAVA_HOME=$JAVA_HOME ($(java -version 2>&1 | head -1))"
if [ -d /workspace/src ]; then
    rsync -a --delete /workspace/src/ /workspace/persist/
fi
exec python3 /workspace/engine.py --src /workspace/persist --out /workspace/artifacts --auto-install "$@"
"""
        dockerfile_path = host_dir / "Dockerfile"
        entrypoint_path = host_dir / "entrypoint.sh"
        dockerignore_path = host_dir / ".dockerignore"
        
        try:
            dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
            entrypoint_path.write_text(entrypoint_content, encoding="utf-8")
            dockerignore_path.write_text("_git_cache/\nEXECUTABLE/\n__pycache__/\n", encoding="utf-8")
            
            UI.info("Engine-laag synchroniseren...")
            ok_eng = _build_with_spinner(t('docker_building_spinner'), docker_cmd + ["build", "-t", image_tag, str(host_dir)])
            if not ok_eng:
                SANDBOX_STATE.update(status="error",
                                     reason=f"engine layer build failed ({image_tag})")
                return False
        finally:
            if dockerfile_path.exists(): dockerfile_path.unlink()
            if entrypoint_path.exists(): entrypoint_path.unlink()
            if dockerignore_path.exists(): dockerignore_path.unlink()

    # sandbox images guaranteed present from here on
    SANDBOX_STATE.update(status="ready", reason="")

    home = Path.home()
    cache_dirs = {k: home / v for k, v in {
        "gradle": ".gradle", "maven": ".m2", "npm": ".npm", "pip": ".cache/pip",
        "cargo": ".cargo/registry", "go_mod": "go/pkg/mod", "go_build": ".cache/go-build",
        "pnpm": ".local/share/pnpm", "yarn": ".cache/yarn"
    }.items()}
    for p in cache_dirs.values(): 
        p.mkdir(parents=True, exist_ok=True)

    def _cache_mounts() -> list:
        return [
            "-v", f"{cache_dirs['gradle']}:/root/.gradle:z",
            "-v", f"{cache_dirs['maven']}:/root/.m2:z",
            "-v", f"{cache_dirs['npm']}:/root/.npm:z",
            "-v", f"{cache_dirs['pip']}:/root/.cache/pip:z",
            "-v", f"{cache_dirs['cargo']}:/root/.cargo/registry:z",
            "-v", f"{cache_dirs['go_mod']}:/root/go/pkg/mod:z",
            "-v", f"{cache_dirs['go_build']}:/root/.cache/go-build:z",
            "-v", f"{cache_dirs['pnpm']}:/root/.local/share/pnpm:z",
            "-v", f"{cache_dirs['yarn']}:/root/.cache/yarn:z",
        ]

    vol_name = _volume_name(target_path)
    subprocess.run(docker_cmd + ["volume", "create", vol_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _prune_old_images(docker_cmd, "justcompiler-engine", keep_tag=image_tag, keep=2)

    # Optional sandbox hardening via config.json (all optional, safe defaults)
    try:
        cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}

    # All cache dirs are mounted into the container for optimal reuse
    # unique name per run so parallel builds never kill each other
    run_name = f"justcompiler_run_{secrets.token_hex(4)}"
    ACTIVE_RUN_NAME["name"] = run_name
    subprocess.run(docker_cmd + ["rm", "-f", run_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run_cmd = docker_cmd + ["run", "--name", run_name] + _sandbox_flags(java_version, cfg, extra_env)
    run_cmd += [
        "-v", f"{target_path.resolve()}:/workspace/src:ro,z",
        "-v", f"{vol_name}:/workspace/persist:z",
        *_cache_mounts(),
        image_tag, "--lang", lang
    ]
    if project_name:
        run_cmd += ["--name", project_name]
    if target_filter:
        run_cmd += ["--filter", target_filter]
    if run_tests: 
        run_cmd.append("--test")

    try:
        set_status_fn(t('docker_compiling_status'))
        t0 = time.time()
        UI.info(t('docker_compiling_status'))
        log_lines = []
        proc = subprocess.Popen(run_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, errors="replace")
        try:
            for line in proc.stdout:
                # route through core.UI so a bound TUI sink streams it live;
                # unbound (console) mode prints exactly as before
                UI.log(UI.DIM, "", line.rstrip("\n"))
                log_lines.append(line)
            proc.wait()
        except KeyboardInterrupt:
            proc.kill()
            raise
        returncode = proc.returncode
        elapsed = time.time() - t0
        try:
            artifacts_path.mkdir(exist_ok=True)

            if returncode != 0:
                # FAILED build: pull only the small diagnostic files, never the
                # (possibly huge) partial artifact tree — users waited enough.
                set_status_fn("Saving build logs...")
                for f in ("build_log.txt", "build_manifest.json"):
                    subprocess.run(
                        docker_cmd + ["cp", f"{run_name}:/workspace/artifacts/{f}",
                                      str(artifacts_path.resolve())],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # SUCCESS: prefer the single compressed bundle the engine wrote
                # (one-file transfer beats per-file docker cp on many-file trees)
                set_status_fn("Safeguarding build artifacts...")
                bundle_local = artifacts_path / "_bundle.tar.gz"
                got_bundle = False
                try:
                    subprocess.run(
                        docker_cmd + ["cp", f"{run_name}:/workspace/artifacts/_bundle.tar.gz",
                                      str(bundle_local)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
                    got_bundle = bundle_local.is_file() and bundle_local.stat().st_size > 0
                except Exception:
                    got_bundle = False

                if got_bundle:
                    mb = bundle_local.stat().st_size / (1024 * 1024)
                    UI.info(f"Transferring artifact bundle ({mb:.1f} MB)…")
                    import tarfile as _tf
                    extracted = 0
                    with _tf.open(bundle_local, "r:gz") as tf:
                        members = tf.getmembers()
                        total = sum(m.size for m in members) or 1
                        last = 0.0
                        for m in members:
                            tf.extract(m, path=artifacts_path)
                            extracted += m.size
                            pct = min(99.0, extracted * 100.0 / total)
                            if pct - last >= 2.0:
                                UI._emit("progress", pct=pct,
                                         text=f"Extracting artifacts… {extracted // (1024*1024)} MB")
                                last = pct
                    try:
                        bundle_local.unlink()
                    except Exception:
                        pass
                    UI._emit("progress", pct=100.0, text="Artifacts saved")
                else:
                    # FALLBACK: older engine image without bundling — full tree cp
                    # with size probe while the container is still alive.
                    total_kb = None
                    if shutil.which("du"):
                        try:
                            probe = subprocess.run(
                                docker_cmd + ["exec", run_name, "du", "-sk", "/workspace/artifacts"],
                                capture_output=True, text=True, timeout=30)
                            if probe.returncode == 0:
                                total_kb = int(probe.stdout.split()[0])
                        except Exception:
                            total_kb = None

                    UI.info(f"Copying artifacts from sandbox"
                            + (f" (~{total_kb // 1024} MB)" if total_kb and total_kb > 1024 else "…"))

                    cp_proc = subprocess.Popen(
                        docker_cmd + ["cp", f"{run_name}:/workspace/artifacts/.", str(artifacts_path.resolve())],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if total_kb and shutil.which("du"):
                        dest = str(artifacts_path.resolve())
                        while cp_proc.poll() is None:
                            try:
                                used = subprocess.run(["du", "-sk", dest],
                                                      capture_output=True, text=True)
                                kb = int(used.stdout.split()[0])
                                pct = min(99.0, kb * 100.0 / max(total_kb, 1))
                                UI._emit("progress", pct=pct, text="Saving artifacts…")
                            except Exception:
                                pass
                            time.sleep(0.5)
                        cp_proc.wait()
                        UI._emit("progress", pct=100.0, text="Artifacts saved")
                    else:
                        cp_proc.wait()
                        UI._emit("progress", pct=100.0, text="Artifacts saved")

                # bundle excludes manifest/log (written after harvest): pull small
                for f in ("build_log.txt", "build_manifest.json"):
                    try:
                        subprocess.run(
                            docker_cmd + ["cp", f"{run_name}:/workspace/artifacts/{f}",
                                          str(artifacts_path.resolve())],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
                    UI._emit("progress", pct=100.0, text="Artifacts saved")

            # Always salvage whatever the container produced (logs, manifest,
            # partial artifacts) regardless of build outcome.
            (artifacts_path / "build.log").write_text("".join(log_lines), encoding="utf-8", errors="replace")
        except Exception:
            pass

        if returncode != 0:
            set_status_fn(t('docker_failed_status'))
            UI.error(t('docker_failed_status'))
            return False
        else:
            set_status_fn(t('docker_success_status'))
            UI.success(t('docker_success_status'))
            return True

    except KeyboardInterrupt:
        set_status_fn(t('docker_abort_status'))
        UI.warn("Build aborted by user. Cleaning up sandbox...")
        return False
    finally:
        set_status_fn(t('docker_cleanup_status'))
        subprocess.run(docker_cmd + ["rm", "-f", run_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _gc_stale_runs(docker_cmd)
        # prune in background: it can take seconds and blocked the Save step
        threading.Thread(
            target=lambda: subprocess.run(docker_cmd + ["image", "prune", "-f"],
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL),
            daemon=True).start()
