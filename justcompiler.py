import os
import sys
import subprocess
import shutil
import argparse
import platform
import json
from pathlib import Path
import urllib.request
import core
from core import UI, t
import baremetal

# --- JUSTCOMPILER VERSION ---
VERSION = "1.0.7"

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

def check_for_updates():
    """Silently checks GitHub for a newer version and updates files automatically."""
    current_dir = Path(__file__).resolve().parent
    version_url = "https://raw.githubusercontent.com/Milanv2l/justcompiler/main/version.txt"
    
    try:
        with urllib.request.urlopen(version_url, timeout=1.5) as response:
            remote_version = response.read().decode('utf-8').strip()
        
        if remote_version != VERSION:
            print(f"{UI.CYAN}[INFO] New update found ({remote_version}). Downloading components...{UI.RESET}")
            files = ["justcompiler.py", "core.py", "engine.py", "baremetal.py", "plugins.json"]
            for file_name in files:
                file_url = f"https://raw.githubusercontent.com/Milanv2l/justcompiler/main/{file_name}"
                with urllib.request.urlopen(file_url, timeout=5) as file_response:
                    (current_dir / file_name).write_bytes(file_response.read())
            
            print(f"{UI.GREEN}[OK] JustCompiler updated successfully to {remote_version}! Please restart the tool.{UI.RESET}")
            sys.exit(0)
    except Exception:
        pass

def handle_uninstall():
    """Cleans up shell profiles, removes Docker images, and deletes the installation directory."""
    print(f"{UI.YELLOW}[WARN] Uninstalling JustCompiler... / JustCompiler wordt verwijderd...{UI.RESET}")
    confirm = input("Are you sure you want to uninstall JustCompiler? (y/n): ").strip().lower()
    if confirm not in ['j', 'ja', 'y', 'yes']:
        print("[INFO] Uninstallation cancelled. / Deïnstallatie geannuleerd.")
        return

    install_dir = Path.home() / ".justcompiler"
    is_windows = platform.system() == "Windows"

    if shutil.which("docker"):
        print("[INFO] Cleaning up Docker sandbox components...")
        docker_cmd = ["docker"]
        if not is_windows:
            if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                print("[INFO] Docker requires sudo privileges to remove the image.")
                docker_cmd = ["sudo", "docker"]
        
        subprocess.run(docker_cmd + ["rmi", "-f", "justcompiler-engine"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"{UI.GREEN}[OK] Docker image 'justcompiler-engine' successfully removed.{UI.RESET}")

    if is_windows:
        try:
            profile_path = subprocess.check_output(["powershell", "-NoProfile", "-Command", "$PROFILE"], text=True).strip()
            p_path = Path(profile_path)
            if p_path.exists():
                lines = p_path.read_text(encoding="utf-8").splitlines()
                new_lines = [line for line in lines if "justcompiler" not in line.lower()]
                p_path.write_text("\n".join(new_lines), encoding="utf-8")
                print(f"{UI.GREEN}[OK] Removed alias from PowerShell profile.{UI.RESET}")
        except Exception as e:
            print(f"{UI.YELLOW}[WARN] Could not automatically clean PowerShell profile: {e}{UI.RESET}")
    else:
        profiles = [Path.home() / ".zshrc", Path.home() / ".bashrc", Path.home() / ".profile"]
        for p_path in profiles:
            if p_path.exists():
                try:
                    lines = p_path.read_text(encoding="utf-8").splitlines()
                    new_lines = [line for line in lines if "justcompiler" not in line.lower()]
                    p_path.write_text("\n".join(new_lines), encoding="utf-8")
                    print(f"{UI.GREEN}[OK] Removed alias from {p_path.name}.{UI.RESET}")
                except Exception as e:
                    print(f"{UI.YELLOW}[WARN] Could not clean {p_path.name}: {e}{UI.RESET}")

    print("[INFO] Cleaning up installation files...")
    if is_windows:
        cmd = f"Start-Sleep -s 1; Remove-Item -Recurse -Force '{install_dir}'"
        subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=0x08000000)
    else:
        cmd = f"sleep 1 && rm -rf '{install_dir}'"
        subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"{UI.GREEN}[OK] JustCompiler has been completely uninstalled. Please restart your terminal.{UI.RESET}")

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

    # --- ULTIMATE DOCKERFILE ---
    # Bevat nu unzip, zip, wget, jq, npm, pnpm en yarn voor maximale compatibiliteit
    dockerfile_content = """FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y curl wget unzip zip jq git python3 python3-pip python3-venv build-essential g++ cmake qt6-base-dev qt6-tools-dev-tools openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo dotnet-sdk-8.0 php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal apt-file npm && rm -rf /var/lib/apt/lists/*
RUN apt-file update
RUN npm install -g pnpm yarn

# --- VEILIGE PYTHON VIRTUAL ENVIRONMENT ---
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel
RUN pip install pyinstaller cx_Freeze

WORKDIR /workspace
COPY core.py /workspace/core.py
COPY engine.py /workspace/engine.py
COPY plugins.json /workspace/plugins.json
ENTRYPOINT ["python3", "/workspace/engine.py", "--src", "/workspace/src", "--out", "/workspace/artifacts"]
"""
    dockerfile_path = host_dir / "Dockerfile"
    
    try:
        dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
        with UI.spinner("Initializing Docker Sandbox Environment..."):
            build_result = subprocess.run(docker_cmd + ["build", "-t", "justcompiler-engine", str(host_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if build_result.returncode != 0:
                UI.error("Docker build failed.")
                return
    finally:
        if dockerfile_path.exists():
            dockerfile_path.unlink()

    UI.success(t('act_ready'))

    # --- GEAVANCEERDE CACHE MAPPING ---
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
    
    # Maak alle cache mappen lokaal aan als ze niet bestaan
    for path in cache_dirs.values(): 
        path.mkdir(parents=True, exist_ok=True)

    run_cmd = docker_cmd + [
        "run", "--rm",
        "--name", "justcompiler_active_run", # Handig voor geforceerde cleanup
        "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{target_path.resolve()}:/workspace/src:z",
        "-v", f"{artifacts_path.resolve()}:/workspace/artifacts:z",
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
        "justcompiler-engine",
        "--lang", lang
    ]
    if run_tests: 
        run_cmd.append("--test")

    try:
        result = subprocess.run(run_cmd)
        if result.returncode != 0:
            UI.error(f"Container exited unexpectedly with code: {result.returncode}")
    except KeyboardInterrupt:
        UI.warn("Build aborted by user. Cleaning up sandbox...")
        subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def handle_remote_git(url: str) -> Path:
    UI.info(t('cloning'))
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
        UI.error(t('clone_fail'))
        sys.exit(1)
    return cache_dir

if __name__ == "__main__":
    init_terminal_colors()

    if len(sys.argv) > 1 and sys.argv[1].lower() == "uninstall":
        handle_uninstall()
        sys.exit(0)

    check_for_updates()

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
