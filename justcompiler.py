import os
import sys
import subprocess
import shutil
import argparse
import platform
import json
import threading
from pathlib import Path
import urllib.request
import core
from core import UI, t
import baremetal

VERSION = "1.2.0"
CURRENT_STATUS = "Standby"

def status_reporter_loop():
    global CURRENT_STATUS
    while True:
        try:
            user_input = input().strip().lower()
            if user_input == 's':
                print(f"\n{UI.CYAN}[JUSTCOMPILER STATUS]: {CURRENT_STATUS}{UI.RESET}\n")
        except (IOError, ValueError, EOFError): break

def start_status_listener():
    threading.Thread(target=status_reporter_loop, daemon=True).start()

def init_terminal_colors():
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception: pass

def check_for_updates():
    global CURRENT_STATUS
    CURRENT_STATUS = "Checking updates..."
    current_dir = Path(__file__).resolve().parent
    version_url = "https://raw.githubusercontent.com/Milanv2l/justcompiler/main/version.txt"
    try:
        with urllib.request.urlopen(version_url, timeout=1.5) as response:
            remote_version = response.read().decode('utf-8').strip()
        if remote_version != VERSION:
            files = ["justcompiler.py", "core.py", "engine.py", "baremetal.py", "plugins.json"]
            for file_name in files:
                file_url = f"https://raw.githubusercontent.com/Milanv2l/justcompiler/main/{file_name}"
                with urllib.request.urlopen(file_url, timeout=5) as file_response:
                    (current_dir / file_name).write_bytes(file_response.read())
            print(f"{UI.GREEN}[OK] JustCompiler updated to {remote_version}!{UI.RESET}")
            sys.exit(0)
    except Exception: pass

def show_tui_header():
    UI.clear()
    system_str = f"{platform.system()} ({platform.machine()})"
    docker_status = f"{UI.GREEN}Available{UI.RESET}" if shutil.which("docker") else f"{UI.RED}Missing{UI.RESET}"
    
    lines = [
        f"{UI.BOLD}Version:{UI.RESET} {VERSION}  │  {UI.BOLD}System:{UI.RESET} {system_str}",
        f"{UI.BOLD}Docker Environment:{UI.RESET} {docker_status}  │  {UI.BOLD}Status:{UI.RESET} {UI.YELLOW}{CURRENT_STATUS}{UI.RESET}",
        f"{UI.DIM}─────────────────────────────────────────────────────────────────────────{UI.RESET}",
        f"Shortcut: Press {UI.BOLD}'s' + Enter{UI.RESET} at any time to request diagnostic health logs."
    ]
    UI.draw_panel("JustCompiler Hub", lines, color=UI.MAGENTA)
    print()

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str):
    global CURRENT_STATUS
    if not shutil.which("docker"):
        UI.error(t('err_docker'))
        return

    docker_cmd = ["docker"]
    if platform.system() != "Windows":
        if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            if subprocess.run(["sudo", "-v"]).returncode == 0:
                docker_cmd = ["sudo", "docker"]
            else:
                UI.error(t('err_auth'))
                return

    host_dir = Path(__file__).resolve().parent
    image_tag = f"justcompiler-engine:{VERSION}"

    CURRENT_STATUS = "Building sandbox..."
    dockerfile_content = """FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \\
    curl wget unzip zip jq git python3 python3-pip python3-venv build-essential g++ cmake \\
    qt6-base-dev qt6-tools-dev-tools openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo \\
    php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal apt-file \\
    libgtk-3-dev libwebkit2gtk-4.1-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \\
    pkg-config libxdo-dev libgdk-pixbuf-xlib-2.0-dev libpango1.0-dev libcairo2-dev libatk1.0-dev \\
    dotnet-sdk-10.0 \\
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs
RUN npm install -g pnpm yarn
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel pyinstaller cx_Freeze
WORKDIR /workspace
COPY core.py engine.py plugins.json /workspace/
ENTRYPOINT ["/bin/bash", "-c", "mkdir -p /workspace/artifacts && cp -R /workspace/src /workspace/build_src && python3 /workspace/engine.py --src /workspace/build_src --out /workspace/artifacts \\"$@\\"", "--"]
"""
    dockerfile_path = host_dir / "Dockerfile"
    try:
        dockerfile_path.write_text(dockerfile_content, encoding="utf-8")
        with UI.spinner("Syncing Docker sandbox image cache..."):
            subprocess.run(docker_cmd + ["build", "-t", image_tag, str(host_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        if dockerfile_path.exists(): dockerfile_path.unlink()

    home = Path.home()
    cache_dirs = {k: home / v for k, v in {
        "gradle": ".gradle", "maven": ".m2", "npm": ".npm", "pip": ".cache/pip", "cargo": ".cargo/registry"
    }.items()}
    for p in cache_dirs.values(): p.mkdir(parents=True, exist_ok=True)

    run_cmd = docker_cmd + [
        "run", "--name", "justcompiler_active_run", "-e", "PYTHONUNBUFFERED=1",
        "-v", f"{target_path.resolve()}:/workspace/src:ro,z",
        "-v", f"{cache_dirs['gradle']}:/root/.gradle:z",
        "-v", f"{cache_dirs['maven']}:/root/.m2:z",
        image_tag, "--lang", lang
    ]
    if run_tests: run_cmd.append("--test")

    start_status_listener()
    try:
        CURRENT_STATUS = "Compiling inside Sandbox"
        show_tui_header()
        UI.info("Executing isolated pipeline. Real-time compilation streams:")
        print(f"{UI.DIM}─" * 75 + f"{UI.RESET}")
        result = subprocess.run(run_cmd)
        print(f"{UI.DIM}─" * 75 + f"{UI.RESET}")
        if result.returncode == 0:
            CURRENT_STATUS = "Finished Successfully"
            artifacts_path.mkdir(exist_ok=True)
            subprocess.run(docker_cmd + ["cp", "justcompiler_active_run:/workspace/artifacts/.", str(artifacts_path.resolve())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            UI.success("Compilation lifecycle completed successfully.")
        else:
            CURRENT_STATUS = "Pipeline Failed"
            UI.error("Compilation failed. Inspect structural constraints above.")
    except KeyboardInterrupt:
        CURRENT_STATUS = "Aborted"
        UI.warn("Build interrupted dynamically by client request.")
    finally:
        subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def handle_remote_git(url: str) -> Path:
    global CURRENT_STATUS
    CURRENT_STATUS = "Fetching git repository"
    show_tui_header()
    UI.info(t('cloning'))
    branch = url.split("#")[1] if "#" in url else None
    url = url.split("#")[0]
    cache_dir = Path("./_git_cache") / url.split("/")[-1].replace(".git", "")
    if cache_dir.exists(): shutil.rmtree(cache_dir, ignore_errors=True)
    cmd = f"git clone -b {branch} {url} {cache_dir}" if branch else f"git clone {url} {cache_dir}"
    if subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        UI.error(t('clone_fail'))
        sys.exit(1)
    return cache_dir

if __name__ == "__main__":
    init_terminal_colors()
    check_for_updates()

    UI.clear()
    print(f"{UI.CYAN}Select interface language / Kies taal:{UI.RESET}")
    print("  [1] English (Default)")
    print("  [2] Nederlands")
    selected_lang = "nl" if input("\nChoice / Keuze [1-2]: ").strip() == "2" else "en"
    core.set_lang(selected_lang)

    artifacts_folder = Path("./EXECUTABLE")
    artifacts_folder.mkdir(exist_ok=True)

    while True:
        CURRENT_STATUS = "Awaiting instructions"
        show_tui_header()
        
        menu_items = [
            f"{UI.CYAN} {t('menu_1')}{UI.RESET}",
            f"{UI.CYAN} {t('menu_2')}{UI.RESET}",
            f"{UI.RED} {t('menu_3')}{UI.RESET}"
        ]
        UI.draw_panel(t('title'), menu_items, color=UI.CYAN)
        
        choice = input(f"\n{UI.BOLD}➔ {t('choice_prompt')}{UI.RESET}").strip()
        target = None

        if choice == "1":
            show_tui_header()
            path_input = input(f"{UI.BOLD}➔ {t('path_prompt')}{UI.RESET}").strip()
            target = Path(path_input) if path_input else Path(".")
        elif choice == "2":
            show_tui_header()
            url = input(f"{UI.BOLD}➔ {t('git_prompt')}{UI.RESET}").strip()
            if url: target = handle_remote_git(url)
        else:
            UI.clear()
            sys.exit(0)

        if not target or not target.exists():
            UI.error(t('err_dir'))
            time.sleep(2)
            continue

        show_tui_header()
        tests = input(f"{UI.BOLD}➔ {t('test_prompt')}{UI.RESET}").strip().lower() in ['j', 'ja', 'y', 'yes']

        show_tui_header()
        env_lines = [f" [1] {t('env_1')}", f" [2] {t('env_2')}"]
        UI.draw_panel(t('env_title'), env_lines, color=UI.YELLOW)
        env_choice = input(f"\n{UI.BOLD}➔ {t('env_choice')}{UI.RESET}").strip()

        if env_choice == "2":
            CURRENT_STATUS = "Bare-metal build running"
            show_tui_header()
            baremetal.run_bare_metal(target, artifacts_folder, tests, selected_lang)
        else:
            bootstrap_sandbox(target, artifacts_folder, tests, selected_lang)
            
        input(f"\n{UI.DIM}Press Enter to return to dashboard...{UI.RESET}")
