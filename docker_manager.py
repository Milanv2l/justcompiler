import os
import sys
import subprocess
import shutil
import platform
import json
import time
import hashlib
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

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str, set_status_fn, base_image: str = "ubuntu:24.04", target_filter: str = "", java_version: int | None = None) -> bool | None:
    if not shutil.which("docker"):
        UI.error(t('err_docker'))
        return

    docker_cmd = get_docker_cmd()
    host_dir = Path(__file__).resolve().parent

    plugins_path = host_dir / "plugins.json"
    if not plugins_path.exists():
        default_plugins = [
            {"name": "Java (Gradle)", "tool": "gradle", "detect": ["build.gradle", "build.gradle.kts"], "wrapper": "gradlew", "out_dirs": ["build/libs", "dist"], "out_exts": [".jar", ".war"]},
            {"name": "Java (Maven)", "tool": "mvn", "detect": ["pom.xml"], "wrapper": "mvnw", "out_dirs": ["target"], "out_exts": [".jar", ".war"]},
            {"name": "Node.js", "tool": "npm", "detect": ["package.json"], "cmd_system": "DYNAMIC_JS_RESOLUTION", "out_dirs": ["dist", "build"], "out_exts": ["*DIR*"]},
            {"name": "Go", "tool": "go", "detect": ["go.mod"], "cmd_system": "DYNAMIC_GO_RESOLUTION", "out_dirs": ["build_output"], "out_exts": [""]},
            {"name": "Rust", "tool": "cargo", "detect": ["Cargo.toml"], "cmd_system": "cargo build --release", "out_dirs": ["target/release"], "out_exts": [""]}
        ]
        plugins_path.write_text(json.dumps(default_plugins, indent=4), encoding="utf-8")

    image_tag = f"justcompiler-engine:{_compute_engine_hash(host_dir)}"

    set_status_fn(t('docker_cache_check'))
    check_image = subprocess.run(docker_cmd + ["images", "-q", image_tag], capture_output=True, text=True)

    if not check_image.stdout.strip():

        base_dockerfile_content = f"""FROM {base_image}
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \\
    curl wget unzip zip jq git python3 python3-pip python3-venv build-essential g++ cmake \\
    qt6-base-dev qt6-tools-dev-tools openjdk-8-jdk openjdk-17-jdk openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo \\
    php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal apt-file \\
    libgtk-3-dev libwebkit2gtk-4.1-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \\
    pkg-config libxdo-dev libgdk-pixbuf-xlib-2.0-dev libpango1.0-dev libcairo2-dev libatk1.0-dev \\
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
RUN npm install -g pnpm yarn
RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh && chmod +x dotnet-install.sh && ./dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet && rm dotnet-install.sh
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel pyinstaller cx_Freeze
RUN ln -sfn /usr/lib/jvm/java-8-openjdk-$(dpkg --print-architecture) /opt/jdk8 \\
    && ln -sfn /usr/lib/jvm/java-17-openjdk-$(dpkg --print-architecture) /opt/jdk17 \\
    && ln -sfn /usr/lib/jvm/java-21-openjdk-$(dpkg --print-architecture) /opt/jdk21 \\
    && ln -sfn /usr/lib/jvm/java-25-openjdk-$(dpkg --print-architecture) /opt/jdk25
ENV JAVA_HOME=/opt/jdk21
ENV PATH="$JAVA_HOME/bin:$PATH"
"""
        base_hash = hashlib.sha256((base_image + base_dockerfile_content).encode()).hexdigest()[:12]
        base_tag = f"justcompiler-base:{base_hash}"

        # STAP 1: Controleer of de zware basisomgeving (justcompiler-base) al lokaal bestaat
        check_base = subprocess.run(docker_cmd + ["images", "-q", base_tag], capture_output=True, text=True)

        if not check_base.stdout.strip():
            set_status_fn(t('docker_building_base'))
            UI.info("Basisomgeving niet gevonden of gewijzigd. Opnieuw bouwen...")
            base_dockerfile_path = host_dir / "Dockerfile.base"
            try:
                base_dockerfile_path.write_text(base_dockerfile_content, encoding="utf-8")
                _build_with_spinner(t('docker_building_base'), docker_cmd + ["build", "-f", str(base_dockerfile_path), "-t", base_tag, str(host_dir)])
            finally:
                if base_dockerfile_path.exists(): base_dockerfile_path.unlink()
        else:
            UI.success(f"Basisomgeving {base_tag} beschikbaar")

        # STAP 2: Bouw de vederlichte engine layer (duurt < 0.5 seconde, hergebruikt de lokale basis)
        set_status_fn("Snelkoppeling maken...")
        
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
echo "JAVA_HOME=$JAVA_HOME ($(java -version 2>&1 | head -1))"
if [ -d /workspace/src ]; then
    cp -ur /workspace/src/. /workspace/persist/
fi
exec python3 /workspace/engine.py --src /workspace/persist --out /workspace/artifacts "$@"
"""
        dockerfile_path = host_dir / "Dockerfile"
        entrypoint_path = host_dir / "entrypoint.sh"
        dockerignore_path = host_dir / ".dockerignore"
        
        try:
            dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
            entrypoint_path.write_text(entrypoint_content, encoding="utf-8")
            dockerignore_path.write_text("_git_cache/\nEXECUTABLE/\n__pycache__/\n", encoding="utf-8")
            
            UI.info("Engine-laag synchroniseren...")
            _build_with_spinner(t('docker_building_spinner'), docker_cmd + ["build", "-t", image_tag, str(host_dir)])
        finally:
            if dockerfile_path.exists(): dockerfile_path.unlink()
            if entrypoint_path.exists(): entrypoint_path.unlink()
            if dockerignore_path.exists(): dockerignore_path.unlink()

    home = Path.home()
    cache_dirs = {k: home / v for k, v in {
        "gradle": ".gradle", "maven": ".m2", "npm": ".npm", "pip": ".cache/pip", "cargo": ".cargo/registry"
    }.items()}
    for p in cache_dirs.values(): 
        p.mkdir(parents=True, exist_ok=True)

    vol_name = _volume_name(target_path)
    subprocess.run(docker_cmd + ["volume", "create", vol_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Alle cache-mappen worden nu daadwerkelijk gekoppeld aan de container voor optimaal hergebruik
    subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run_cmd = docker_cmd + ["run", "--name", "justcompiler_active_run"]
    if java_version:
        run_cmd += ["-e", f"JC_JAVA_VERSION={java_version}", "-e", "PYTHONUNBUFFERED=1"]
    else:
        run_cmd += ["-e", "PYTHONUNBUFFERED=1"]
    run_cmd += [
        "-v", f"{target_path.resolve()}:/workspace/src:ro,z",
        "-v", f"{vol_name}:/workspace/persist:z",
        "-v", f"{cache_dirs['gradle']}:/root/.gradle:z",
        "-v", f"{cache_dirs['maven']}:/root/.m2:z",
        "-v", f"{cache_dirs['npm']}:/root/.npm:z",
        "-v", f"{cache_dirs['pip']}:/root/.cache/pip:z",
        "-v", f"{cache_dirs['cargo']}:/root/.cargo/registry:z",
        image_tag, "--lang", lang
    ]
    if target_filter:
        run_cmd += ["--filter", target_filter]
    if run_tests: 
        run_cmd.append("--test")

    try:
        set_status_fn(t('docker_compiling_status'))
        t0 = time.time()
        UI.info(t('docker_compiling_status'))
        result = subprocess.run(run_cmd)
        elapsed = time.time() - t0

        if result.returncode != 0:
            set_status_fn(t('docker_failed_status'))
            UI.error(t('docker_failed_status'))
            return False
        else:
            set_status_fn(t('docker_success_status'))
            UI.success(t('docker_success_status'))

            artifacts_path.mkdir(exist_ok=True)
            subprocess.run(docker_cmd + ["cp", "justcompiler_active_run:/workspace/artifacts/.", str(artifacts_path.resolve())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True

    except KeyboardInterrupt:
        set_status_fn(t('docker_abort_status'))
        UI.warn("Build aborted by user. Cleaning up sandbox...")
        return False
    finally:
        set_status_fn(t('docker_cleanup_status'))
        subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
