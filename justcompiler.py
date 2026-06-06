import os
import sys
import subprocess
import shutil
import argparse
import platform
import json
from pathlib import Path
import core
from core import UI, t
import baremetal

def init_terminal_colors():
    """Enables ANSI escape sequences for coloring in the Windows terminal."""
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str):
    if not shutil.which("docker"):
        UI.error(t('err_docker'))
        return

    docker_cmd = ["docker"]
    if platform.system() != "Windows":
        if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            UI.warn(t('err_sudo'))
            print(t('sudo_prompt'), end="")
            sys.stdout.flush()
            if subprocess.run(["sudo", "-v"]).returncode == 0:
                docker_cmd = ["sudo", "docker"]
            else:
                UI.error(t('err_auth'))
                return

    host_dir = Path(__file__).resolve().parent

    # Initialize plugins configuration if missing
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

    dockerfile_content = """FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y curl git python3 python3-pip python3-venv build-essential g++ cmake qt6-base-dev qt6-tools-dev-tools openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo dotnet-sdk-8.0 php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs && npm install -g pnpm yarn && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY core.py /workspace/core.py
COPY engine.py /workspace/engine.py
COPY plugins.json /workspace/plugins.json
ENTRYPOINT ["python3", "/workspace/engine.py", "--src", "/workspace/src", "--out", "/workspace/artifacts"]
"""
    dockerfile_path = host_dir / "Dockerfile"

    # Safe management of the temporary Dockerfile
    try:
        dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
        UI.info(t('sandbox_prep'))

        build_result = subprocess.run(docker_cmd + ["build", "-t", "justcompiler-engine", str(host_dir)])
        if build_result.returncode != 0:
            UI.error("Docker build failed.")
            return
    finally:
        if dockerfile_path.exists():
            dockerfile_path.unlink()

    UI.success(t('sandbox_ready'))

    # Setup caching directories
    home = Path.home()
    cache_dirs = {
        "gradle": home / ".gradle", "maven": home / ".m2", "npm": home / ".npm",
        "pip": home / ".cache" / "pip", "cargo": home / ".cargo" / "registry"
    }
    for path in cache_dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    run_cmd = docker_cmd + [
        "run", "--rm",
        "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{target_path.resolve()}:/workspace/src:z",
        "-v", f"{artifacts_path.resolve()}:/workspace/artifacts:z",
        "-v", f"{cache_dirs['gradle']}:/root/.gradle:z",
        "-v", f"{cache_dirs['maven']}:/root/.m2:z",
        "-v", f"{cache_dirs['npm']}:/root/.npm:z",
        "-v", f"{cache_dirs['pip']}:/root/.cache/pip:z",
        "-v", f"{cache_dirs['cargo']}:/root/.cargo/registry:z",
        "justcompiler-engine",
        "--lang", lang
    ]
    if run_tests:
        run_cmd.append("--test")

    UI.info(f"Launching container: {' '.join(run_cmd)}")

    try:
        result = subprocess.run(run_cmd)
        if result.returncode != 0:
            UI.error(f"Container exited unexpectedly with code: {result.returncode}")
    except KeyboardInterrupt:
        UI.warn("Build aborted by user.")
    finally:
        subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def handle_remote_git(url: str) -> Path:
    UI.info(t('git_clone'))
    branch = None
    if "#" in url:
        url, branch = [p.strip() for p in url.split("#", 1)]

    cache_dir = Path("./_git_cache") / url.split("/")[-1].replace(".git", "")
    if cache_dir.exists():
        if platform.system() != "Windows":
            subprocess.run(f"sudo rm -rf {cache_dir}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            shutil.rmtree(cache_dir, ignore_errors=True)

    clone_cmd = f"git clone -b {branch} {url} {cache_dir}" if branch else f"git clone {url} {cache_dir}"
    if subprocess.run(clone_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        UI.error(t('git_fail'))
        sys.exit(1)
    return cache_dir

if __name__ == "__main__":
    init_terminal_colors()

    parser = argparse.ArgumentParser(description="JustCompiler CLI")
    parser.add_argument("--local-runtime", action="store_true", help="Force local bare-metal execution")
    args = parser.parse_args()

    print("Select language / Kies taal:")
    print("  1. English (Default)")
    print("  2. Nederlands")
    lang_choice = input("Choice / Keuze [1-2]: ").strip()
    selected_lang = "nl" if lang_choice == "2" else "en"
    core.set_lang(selected_lang)

    artifacts_folder = Path("./BUILD_ARTIFACTS")
    artifacts_folder.mkdir(exist_ok=True)

    print(f"\n{UI.CYAN}{t('title')}{UI.RESET}")
    print(t('menu_1'))
    print(t('menu_2'))
    print(t('menu_3'))

    choice = input(f"\n{UI.YELLOW}{t('choice_prompt')}{UI.RESET}").strip()
    target = None

    if choice == "1":
        path_input = input(f"{UI.YELLOW}{t('path_prompt')}{UI.RESET}").strip()
        target = Path(path_input) if path_input else Path(".")
    elif choice == "2":
        url = input(f"{UI.YELLOW}{t('git_prompt')}{UI.RESET}").strip()
        if url:
            target = handle_remote_git(url)
    else:
        sys.exit(0)

    if not target or not target.exists():
        UI.error(t('err_dir'))
        sys.exit(1)

    tests = input(f"{UI.YELLOW}{t('test_prompt')}{UI.RESET}").strip().lower() in ['j', 'ja', 'y', 'yes']

    print(f"\n{UI.CYAN}{t('env_title')}{UI.RESET}")
    print(t('env_1'))
    print(t('env_2'))
    env_choice = input(f"{UI.YELLOW}{t('env_choice')}{UI.RESET}").strip()

    if env_choice == "2" or args.local_runtime:
        baremetal.run_bare_metal(target, artifacts_folder, tests, selected_lang)
    else:
        bootstrap_sandbox(target, artifacts_folder, tests, selected_lang)
