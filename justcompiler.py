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

# --- JUSTCOMPILER VERSION ---
VERSION = "1.2.2"

# Globale statusvariabele voor de sneltoets-announcer
CURRENT_STATUS = "Opstarten... / Starting up..."

# --- RUNTIME DICTIONARY INJECTION FOR TRANSLATIONS ---
core._TRANSLATIONS["en"].update({
    "git_fetching_branches": "Querying remote Git repository for available branches...",
    "branch_title": "Branch Selection",
    "default_branch_label": "Default Branch",
    "branch_choice_prompt": "Select a branch or option [1-{max_choice}]: ",
    "selected_branch_info": "Target branch set to: {branch}",
    "err_git_fetch": "Could not fetch branch list. Falling back to repository defaults.",
    "commit_info": "Preparing to compile commit: {hash} ({msg})",
    "lang_title": "Language / Taal",
    "lang_choice": "Select language [1-2]: "
})

core._TRANSLATIONS["nl"].update({
    "git_fetching_branches": "Remote Git repository bevragen voor beschikbare branches...",
    "branch_title": "Branch Selectie",
    "default_branch_label": "Standaard branch",
    "branch_choice_prompt": "Kies een branch of optie [1-{max_choice}]: ",
    "selected_branch_info": "Doelbranch ingesteld op: {branch}",
    "err_git_fetch": "Kon branch-lijst niet ophalen. Er wordt teruggevallen op de repository standaard.",
    "commit_info": "Voorbereiden om commit te compileren: {hash} ({msg})",
    "lang_title": "Language / Taal",
    "lang_choice": "Selecteer taal [1-2]: "
})

def status_reporter_loop():
    """Luistert op de achtergrond naar de 's' toets om de status te melden."""
    global CURRENT_STATUS
    while True:
        try:
            user_input = input().strip().lower()
            if user_input == 's':
                print(f"\n{UI.CYAN}[JUSTCOMPILER STATUS]: {CURRENT_STATUS}{UI.RESET}\n")
        except (IOError, ValueError, EOFError):
            break

def start_status_listener():
    """Start de status-listener veilig als achtergrond-thread."""
    t = threading.Thread(target=status_reporter_loop, daemon=True)
    t.start()

def init_terminal_colors():
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

def check_for_updates():
    global CURRENT_STATUS
    CURRENT_STATUS = "Controleren op updates via GitHub..."
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
                docker_cmd = ["sudo", "docker"]
        
        get_images = subprocess.run(docker_cmd + ["images", "justcompiler-engine", "-q"], capture_output=True, text=True)
        if get_images.stdout.strip():
            for img_id in get_images.stdout.splitlines():
                subprocess.run(docker_cmd + ["rmi", "-f", img_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"{UI.GREEN}[OK] All JustCompiler Docker images successfully removed.{UI.RESET}")

    if is_windows:
        try:
            profile_path = subprocess.check_output(["powershell", "-NoProfile", "-Command", "$PROFILE"], text=True).strip()
            p_path = Path(profile_path)
            if p_path.exists():
                lines = p_path.read_text(encoding="utf-8").splitlines()
                new_lines = [line for line in lines if "justcompiler" not in line.lower()]
                p_path.write_text("\n".join(new_lines), encoding="utf-8")
        except Exception:
            pass
    else:
        profiles = [Path.home() / ".zshrc", Path.home() / ".bashrc", Path.home() / ".profile"]
        for p_path in profiles:
            if p_path.exists():
                try:
                    lines = p_path.read_text(encoding="utf-8").splitlines()
                    new_lines = [line for line in lines if "justcompiler" not in line.lower()]
                    p_path.write_text("\n".join(new_lines), encoding="utf-8")
                except Exception:
                    pass

    if is_windows:
        cmd = f"Start-Sleep -s 1; Remove-Item -Recurse -Force '{install_dir}'"
        subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=0x08000000)
    else:
        cmd = f"sleep 1 && rm -rf '{install_dir}'"
        subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"{UI.GREEN}[OK] JustCompiler has been completely uninstalled. Please restart your terminal.{UI.RESET}")

def bootstrap_sandbox(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str):
    global CURRENT_STATUS
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

    image_tag = f"justcompiler-engine:{VERSION}"

    CURRENT_STATUS = "Docker cache controleren en oude containers opruimen..."
    check_image = subprocess.run(docker_cmd + ["images", "-q", image_tag], capture_output=True, text=True)
    if not check_image.stdout.strip():
        with UI.spinner("Nieuwe scriptversie gedetecteerd! Oude Docker-omgevingen worden opgeruimd..."):
            get_images = subprocess.run(docker_cmd + ["images", "justcompiler-engine", "-q"], capture_output=True, text=True)
            if get_images.stdout.strip():
                for img_id in get_images.stdout.splitlines():
                    subprocess.run(docker_cmd + ["rmi", "-f", img_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    CURRENT_STATUS = "Modern Ubuntu 26.04 LTS Sandbox basis-image opbouwen..."
    
    # OPLOSSING: libxxf86vm-dev, libgl1-mesa-dev en aanvullende X11 libs toegevoegd voor vlekkeloze CGO compilaties
    dockerfile_content = """FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \\
    curl wget unzip zip jq git python3 python3-pip python3-venv build-essential g++ cmake \\
    qt6-base-dev qt6-tools-dev-tools openjdk-21-jdk openjdk-25-jdk maven gradle golang cargo \\
    php-cli composer ruby-full flex bison bc libelf-dev libssl-dev valac meson crystal apt-file \\
    libgtk-3-dev libwebkit2gtk-4.1-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev \\
    pkg-config libxdo-dev libgdk-pixbuf-xlib-2.0-dev libpango1.0-dev libcairo2-dev libatk1.0-dev \\
    libxxf86vm-dev libgl1-mesa-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev xorg-dev \\
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
        with UI.spinner("Building Modern Ubuntu 26.04 LTS Sandbox Environment..."):
            build_result = subprocess.run(docker_cmd + ["build", "-t", image_tag, str(host_dir)], capture_output=True, text=True)
            if build_result.returncode != 0:
                UI.error("Docker build failed! Dit is wat er misging:")
                print(f"{UI.RED}{build_result.stderr}{UI.RESET}")
                return
    finally:
        if dockerfile_path.exists():
            dockerfile_path.unlink()

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

    start_status_listener()

    try:
        CURRENT_STATUS = "Project op de achtergrond aan het compileren binnen de sandbox..."
        with UI.spinner("Project aan het compileren in de veilige sandbox... (Druk op 's' + Enter voor status)"):
            result = subprocess.run(run_cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            
        if result.returncode != 0:
            CURRENT_STATUS = "Compilatie mislukt met foutmeldingen."
            UI.error("Compilatie mislukt! Dit is de foutmelding van de compiler:")
            if result.stderr:
                print(f"{UI.RED}{result.stderr}{UI.RESET}")
            if result.stdout:
                print(f"{UI.YELLOW}Gedetailleerd compiler-logboek:{UI.RESET}")
                print(result.stdout)
        else:
            CURRENT_STATUS = "Compilatie succesvol! Resultaten worden nu veiliggesteld..."
            UI.success("Compilatie succesvol afgerond!")
            
            artifacts_path.mkdir(exist_ok=True)
            subprocess.run(docker_cmd + ["cp", "justcompiler_active_run:/workspace/artifacts/.", str(artifacts_path.resolve())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if result.stdout:
                print(f"{UI.CYAN}Resultaten / Output info:{UI.RESET}")
                print('\n'.join(result.stdout.splitlines()[-8:]))
                
    except KeyboardInterrupt:
        CURRENT_STATUS = "Afgebroken door gebruiker. Sandbox wordt opgeschoond..."
        UI.warn("Build aborted by user. Cleaning up sandbox...")
    finally:
        CURRENT_STATUS = "Tijdelijke containeromgevingen weghalen..."
        subprocess.run(docker_cmd + ["rm", "-f", "justcompiler_active_run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(docker_cmd + ["image", "prune", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        CURRENT_STATUS = "Systeem stand-by / Klaar."

def fetch_remote_git_info(url: str) -> tuple:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    default_branch = "main"
    all_branches = []

    try:
        symref_res = subprocess.run(
            ["git", "ls-remote", "--symref", url, "HEAD"],
            capture_output=True, text=True, env=env, timeout=4
        )
        if symref_res.returncode == 0:
            for line in symref_res.stdout.splitlines():
                if line.startswith("ref: refs/heads/"):
                    default_branch = line.split()[1].replace("refs/heads/", "").strip()
                    break
    except Exception:
        pass

    try:
        heads_res = subprocess.run(
            ["git", "ls-remote", "--heads", url],
            capture_output=True, text=True, env=env, timeout=4
        )
        if heads_res.returncode == 0:
            for line in heads_res.stdout.splitlines():
                if "\trefs/heads/" in line:
                    b_name = line.split("\trefs/heads/")[-1].strip()
                    if b_name not in all_branches:
                        all_branches.append(b_name)
    except Exception:
        pass

    if default_branch in all_branches:
        all_branches.remove(default_branch)

    return default_branch, all_branches

def handle_remote_git(url: str) -> Path:
    global CURRENT_STATUS
    CURRENT_STATUS = t("git_fetching_branches")
    
    branch = None
    if "#" in url: 
        url, branch = [p.strip() for p in url.split("#", 1)]

    if not branch:
        with UI.spinner(t("git_fetching_branches")):
            default_branch, other_branches = fetch_remote_git_info(url)
            
        UI.header(t("branch_title"))
        print(f"  [{UI.CYAN}1{UI.RESET}] 🌟 {t('default_branch_label')} ({default_branch})")
        
        for idx, br in enumerate(other_branches, 2):
            print(f"  [{UI.CYAN}{idx}{UI.RESET}] 🌿 {br}")
            
        max_choice = len(other_branches) + 1
        
        try:
            choice_input = input(f"\n{UI.YELLOW}{t('branch_choice_prompt', max_choice=max_choice)}{UI.RESET}").strip()
            if choice_input.isdigit():
                choice_idx = int(choice_input)
                if choice_idx == 1:
                    branch = default_branch
                elif 2 <= choice_idx <= max_choice:
                    branch = other_branches[choice_idx - 2]
                else:
                    branch = default_branch
            else:
                branch = default_branch
        except (KeyboardInterrupt, Exception):
            branch = default_branch

        UI.success(t("selected_branch_info", branch=branch))

    CURRENT_STATUS = "Remote Git repository aan het binnenhalen..."
    UI.info(t('cloning'))

    repo_name = url.split("/")[-1].replace(".git", "")
    cache_dir = Path("./_git_cache") / repo_name
    if cache_dir.exists():
        if platform.system() != "Windows":
            subprocess.run(f"sudo rm -rf {cache_dir}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            shutil.rmtree(cache_dir, ignore_errors=True)

    clone_cmd = ["git", "clone", "--depth", "1", "-b", branch, url, str(cache_dir)]
    if subprocess.run(clone_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        UI.error(t('clone_fail'))
        sys.exit(1)
        
    try:
        commit_log = subprocess.run(
            ["git", "log", "-1", "--format=%h|%s", "HEAD"],
            cwd=cache_dir, capture_output=True, text=True
        )
        if commit_log.returncode == 0:
            c_hash, c_msg = commit_log.stdout.strip().split("|", 1)
            UI.info(t("commit_info", hash=c_hash, msg=c_msg))
    except Exception:
        pass

    return cache_dir

if __name__ == "__main__":
    init_terminal_colors()

    if len(sys.argv) > 1 and sys.argv[1].lower() == "uninstall":
        handle_uninstall()
        sys.exit(0)

    check_for_updates()

    print(f"\n{UI.BOLD}{UI.MAGENTA}── {t('lang_title')} ──{UI.RESET}")
    print(f"  [{UI.CYAN}1{UI.RESET}] English")
    print(f"  [{UI.CYAN}2{UI.RESET}] Nederlands")
    
    lang_choice = input(f"\n{UI.YELLOW}Select language [1-2]: {UI.RESET}").strip()
    selected_lang = "nl" if lang_choice == "2" else "en"
    core.set_lang(selected_lang)

    parser = argparse.ArgumentParser(description="JustCompiler CLI")
    parser.add_argument("--local-runtime", action="store_true", help="Force local bare-metal execution")
    args = parser.parse_args()

    artifacts_folder = Path("./EXECUTABLE")
    artifacts_folder.mkdir(exist_ok=True)

    UI.header(t('title'))
    print(f"  [{UI.CYAN}1{UI.RESET}] {t('menu_1')[5:]}")
    print(f"  [{UI.CYAN}2{UI.RESET}] {t('menu_2')[5:]}")
    print(f"  [{UI.CYAN}3{UI.RESET}] {t('menu_3')[5:]}")

    choice = input(f"\n{UI.YELLOW}{t('choice_prompt')}{UI.RESET}").strip()
    target = None

    if choice == "1":
        path_input = input(f"\n{UI.YELLOW}{t('path_prompt')}{UI.RESET}").strip()
        target = Path(path_input) if path_input else Path(".")
    elif choice == "2":
        url = input(f"\n{UI.YELLOW}{t('git_prompt')}{UI.RESET}").strip()
        if url: 
            target = handle_remote_git(url)
    else:
        sys.exit(0)

    if not target or not target.exists():
        UI.error(t('err_dir'))
        sys.exit(1)

    tests = input(f"\n{UI.YELLOW}{t('test_prompt')}{UI.RESET}").strip().lower() in ['j', 'ja', 'y', 'yes']

    UI.header(t('env_title'))
    print(f"  [{UI.CYAN}1{UI.RESET}] {t('env_1')[5:]}")
    print(f"  [{UI.CYAN}2{UI.RESET}] {t('env_2')[5:]}")
    env_choice = input(f"\n{UI.YELLOW}{t('env_choice')}{UI.RESET}").strip()

    if env_choice == "2" or args.local_runtime:
        CURRENT_STATUS = "Lokaal (bare-metal) aan het compileren..."
        baremetal.run_bare_metal(target, artifacts_folder, tests, selected_lang)
    else:
        bootstrap_sandbox(target, artifacts_folder, tests, selected_lang)
