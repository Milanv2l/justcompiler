import os
import sys
import subprocess
import shutil
import platform
import json
from pathlib import Path
from core import UI, t

def get_docker_cmd():
    docker_cmd = ["docker"]
    if platform.system() != "Windows":
        if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            if subprocess.run(["sudo", "-v"]).returncode == 0:
                docker_cmd = ["sudo", "docker"]
    return docker_cmd

def detect_local_versions():
    if not shutil.which("docker"):
        return []
    
    docker_cmd = get_docker_cmd()
    try:
        res = subprocess.run(docker_cmd + ["images", "justcompiler-engine", "--format", "{{.Tag}}"], capture_output=True, text=True)
        if res.returncode == 0:
            tags = [line.strip() for line in res.stdout.splitlines() if line.strip() and line.strip() != "<none>"]
            return sorted(list(set(tags)), reverse=True)
    except Exception:
        pass
    return []

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str, set_status_fn, version_to_use: str, current_version: str):
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

    image_tag = f"justcompiler-engine:{version_to_use}"

    set_status_fn(t('docker_cache_check'))
    check_image = subprocess.run(docker_cmd + ["images", "-q", image_tag], capture_output=True, text=True)
    
    if not check_image.stdout.strip():
        if version_to_use != current_version:
            UI.error(t('err_custom_version_missing', version=version_to_use))
            sys.exit(1)

        set_status_fn(t('docker_building_base'))
        
        dockerfile_content = f"""FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \\
    curl wget unzip zip jq git python3 python3-pip python3-venv build-essential g++ cmake \\
    qt6-base-dev qt6-tools-dev-tools openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo \\
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
WORKDIR /workspace
COPY core.py engine.py plugins.json /workspace/
COPY entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh
ENTRYPOINT ["/workspace/entrypoint.sh"]
"""
        entrypoint_content = """#!/bin/bash
mkdir -p /workspace/artifacts
if [ -d /workspace/src ]; then
    cp -R /workspace/src /workspace/build_src
fi
exec python3 /workspace/engine.py --src /workspace/build_src --out /workspace/artifacts "$@"
"""
        dockerfile_path = host_dir / "Dockerfile"
        entrypoint_path = host_dir / "entrypoint.sh"
        dockerignore_path = host_dir / ".dockerignore"
        
        try:
            dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
            entrypoint_path.write_text(entrypoint_content, encoding="utf-8")
            dockerignore_path.write_text("_git_cache/\nEXECUTABLE/\n__pycache__/\n", encoding="utf-8")
            
            print(f"{UI.CYAN}➔ Synchroniseren van de Docker Sandbox Image...{UI.RESET}")
            print(f"{UI.DIM}Als de image al is opgebouwd, duurt deze controle minder dan een seconde.{UI.RESET}\n")
            print(f"{UI.DIM}" + "─" * 75 + f"{UI.RESET}")
            
            subprocess.run(docker_cmd + ["build", "-t", image_tag, str(host_dir)])
            print(f"{UI.DIM}" + "─" * 75 + f"{UI.RESET}\n")
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

    run_cmd = docker_cmd + [
        "run", "--name", "justcompiler_active_run", "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{target_path.resolve()}:/workspace/src:ro,z",
        "-v", f"{cache_dirs['gradle']}:/root/.gradle:z",
        "-v", f"{cache_dirs['maven']}:/root/.m2:z",
        image_tag, "--lang", lang
    ]
    if run_tests: 
        run_cmd.append("--test")

    try:
        set_status_fn(t('docker_compiling_status'))
        UI.info("Executing isolated pipeline. Real-time compilation streams:")
        print(f"{UI.DIM}─" * 75 + f"{UI.RESET}")
        result = subprocess.run(run_cmd)
        print(f"{UI.DIM}─" * 75 + f"{UI.RESET}")
            
        if result.returncode != 0:
            set_status_fn(t('docker_failed_status'))
            UI.error("Compilation failed. Inspect structural constraints above.")
        else:
            set_status_fn(t('docker_success_status'))
            UI.success("Compilation lifecycle completed successfully.")
            
            artifacts_path.mkdir(exist_ok=True)
            subprocess.run(docker_cmd + ["cp", "justcompiler_active_run:/workspace/artifacts/.", str(artifacts_path.resolve())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
    except KeyboardInterrupt:
        set_status_fn(t('docker_abort_status'))
        UI.warn("Build aborted by user. Cleaning up sandbox...")
    finally:
        set_status_fn(t('docker_cleanup_status'))
        subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
