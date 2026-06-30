import os
import sys
import subprocess
import shutil
import platform
import json
from pathlib import Path
from core import UI, t

def get_docker_cmd():
    """Bepaalt of docker of sudo docker gebruikt moet worden."""
    docker_cmd = ["docker"]
    if platform.system() != "Windows":
        if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            if subprocess.run(["sudo", "-v"]).returncode == 0:
                docker_cmd = ["sudo", "docker"]
    return docker_cmd

def detect_local_versions():
    """Scant de lokale Docker-omgeving en retourneert een lijst met beschikbare versietags."""
    if not shutil.which("docker"):
        return []
    
    docker_cmd = get_docker_cmd()
    try:
        res = subprocess.run(docker_cmd + ["images", "justcompiler-engine", "--format", "{{.Tag}}"], capture_output=True, text=True)
        if res.returncode == 0:
            # Filter ongeldige of lege tags eruit en sorteer unieke tags
            tags = [line.strip() for line in res.stdout.splitlines() if line.strip() and line.strip() != "<none>"]
            return sorted(list(set(tags)), reverse=True)
    except Exception:
        pass
    return []

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str, set_status_fn, version_to_use: str, current_version: str = "1.1.9"):
    """
    Beheert de volledige lifecycle van de Docker Sandboxomgeving.
    """
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

    if not (host_dir / "engine.py").exists() or not (host_dir / "core.py").exists():
        UI.error(t('err_files'))
        sys.exit(1)

    image_tag = f"justcompiler-engine:{version_to_use}"

    set_status_fn(t('docker_cache_check'))
    check_image = subprocess.run(docker_cmd + ["images", "-q", image_tag], capture_output=True, text=True)
    
    # Bouw het image alleen als deze lokaal nog niet bestaat
    if not check_image.stdout.strip():
        if version_to_use != current_version:
            UI.error(t('err_custom_version_missing', version=version_to_use))
            sys.exit(1)

        with UI.spinner(t('docker_clean_old')):
            get_images = subprocess.run(docker_cmd + ["images", "justcompiler-engine", "-q"], capture_output=True, text=True)
            if get_images.stdout.strip():
                for img_id in get_images.stdout.splitlines():
                    subprocess.run(docker_cmd + ["rmi", "-f", img_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        set_status_fn(t('docker_building_base'))
        dockerfile_content = """FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \\
    curl wget unzip zip jq git python3 python3-pip python3-venv build-essential g++ cmake \\
    qt6-base-dev qt6-tools-dev-tools openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo \\
    php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal apt-file \\
    libgtk-3-dev libwebkit2gtk-4.1-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \\
    pkg-config libxdo-dev libgdk-pixbuf-xlib-2.0-dev libpango1.0-dev libcairo2-dev libatk1.0-dev \\
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
RUN apt-file update
RUN npm install -g pnpm yarn

RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o dotnet-install.sh && chmod +x dotnet-install.sh && ./dotnet-install.sh --channel 8.0 --install-dir /usr/share/dotnet && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet && rm dotnet-install.sh

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel
RUN pip install pyinstaller cx_Freeze

WORKDIR /workspace

COPY core.py /workspace/core.py
COPY engine.py /workspace/engine.py
COPY plugins.json /workspace/plugins.json

ENTRYPOINT ["/bin/bash", "-c", "mkdir -p /workspace/artifacts && cp -R /workspace/src /workspace/build_src && python3 /workspace/engine.py --src /workspace/build_src --out /workspace/artifacts \\"$@\\"", "--"]
"""
        dockerfile_path = host_dir / "Dockerfile"
        
        try:
            dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
            with UI.spinner(t('docker_building_spinner')):
                build_result = subprocess.run(docker_cmd + ["build", "-t", image_tag, str(host_dir)], capture_output=True, text=True)
                if build_result.returncode != 0:
                    UI.error("Docker build failed!")
                    print(f"{UI.RED}{build_result.stderr}{UI.RESET}")
                    return
        finally:
            if dockerfile_path.exists():
                dockerfile_path.unlink()
    else:
        if version_to_use != current_version:
            UI.success(t('docker_reusing_old', version=version_to_use))

    UI.success(t('act_ready'))

    home = Path.home()
    cache_dirs = {
        "gradle": home / ".gradle", 
        "maven": home / ".m2", 
        "npm": home / ".npm",
        "pnpm": home / ".local" / "share" / "pnpm" / "store",
        "yarn": home / ".yarn" / "cache",
        "pip": home / ".cache" / "pip", 
        "cargo_registry": home / ".cargo" / "registry",
        "cargo_git": home / ".cargo" / "git",
        "go_pkg": home / "go" / "pkg",
        "go_build": home / ".cache" / "go-build"
    }
    
    for path in cache_dirs.values(): 
        path.mkdir(parents=True, exist_ok=True)

    run_cmd = docker_cmd + [
        "run",
        "--name", "justcompiler_active_run",
        "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{target_path.resolve()}:/workspace/src:ro,z",
        "-v", f"{cache_dirs['gradle']}:/root/.gradle:z",
        "-v", f"{cache_dirs['maven']}:/root/.m2:z",
        "-v", f"{cache_dirs['npm']}:/root/.npm:z",
        "-v", f"{cache_dirs['pnpm']}:/root/.local/share/pnpm/store:z",
        "-v", f"{cache_dirs['yarn']}:/root/.yarn/cache:z",
        "-v", f"{cache_dirs['pip']}:/root/.cache/pip:z",
        "-v", f"{cache_dirs['cargo_registry']}:/root/.cargo/registry:z",
        "-v", f"{cache_dirs['cargo_git']}:/root/.cargo/git:z",
        "-v", f"{cache_dirs['go_pkg']}:/root/go/pkg:z",
        "-v", f"{cache_dirs['go_build']}:/root/.cache/go-build:z",
        image_tag,
        "--lang", lang
    ]
    if run_tests: 
        run_cmd.append("--test")

    try:
        set_status_fn(t('docker_compiling_status'))
        with UI.spinner(t('docker_compiling_spinner')):
            result = subprocess.run(run_cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            
        if result.returncode != 0:
            set_status_fn(t('docker_failed_status'))
            UI.error(t('compile_fail'))
            if result.stderr:
                print(f"{UI.RED}{result.stderr}{UI.RESET}")
            if result.stdout:
                print(f"{UI.YELLOW}Gedetailleerd compiler-logboek:{UI.RESET}")
                print(result.stdout)
        else:
            set_status_fn(t('docker_success_status'))
            UI.success(t('test_success'))
            
            artifacts_path.mkdir(exist_ok=True)
            subprocess.run(docker_cmd + ["cp", "justcompiler_active_run:/workspace/artifacts/.", str(artifacts_path.resolve())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if result.stdout:
                print(f"{UI.CYAN}Resultaten / Output info:{UI.RESET}")
                print('\n'.join(result.stdout.splitlines()[-8:]))
                
    except KeyboardInterrupt:
        set_status_fn(t('docker_abort_status'))
        UI.warn("Build aborted by user. Cleaning up sandbox...")
    finally:
        set_status_fn(t('docker_cleanup_status'))
        subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        set_status_fn("Systeem stand-by / Ready.")