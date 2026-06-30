import os
import sys
import subprocess
import shutil
import platform
import urllib.request
import threading
import time
from pathlib import Path
import core
from core import UI, t
import docker_manager

VERSION = "1.1.10"
CURRENT_STATUS = "Standby"

def set_current_status(msg: str):
    global CURRENT_STATUS
    CURRENT_STATUS = msg
    show_tui_header()

def status_reporter_loop():
    while True:
        try:
            user_input = input().strip().lower()
            if user_input == 's':
                print(f"\n{UI.CYAN}[JUSTCOMPILER STATUS]: {CURRENT_STATUS}{UI.RESET}\n")
        except (IOError, ValueError, EOFError):
            break

def start_status_listener():
    t_thread = threading.Thread(target=status_reporter_loop, daemon=True)
    t_thread.start()

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
    set_current_status("Checking updates...")
    current_dir = Path(__file__).resolve().parent
    version_url = "https://raw.githubusercontent.com/Milanv2l/justcompiler/main/version.txt"
    try:
        with urllib.request.urlopen(version_url, timeout=1.5) as response:
            remote_version = response.read().decode('utf-8').strip()
        if remote_version != VERSION:
            files = ["justcompiler.py", "core.py", "engine.py", "docker_manager.py", "plugins.json"]
            for file_name in files:
                file_url = f"https://raw.githubusercontent.com/Milanv2l/justcompiler/main/{file_name}"
                with urllib.request.urlopen(file_url, timeout=5) as file_response:
                    (current_dir / file_name).write_bytes(file_response.read())
            print(f"{UI.GREEN}[OK] JustCompiler updated to {remote_version}! Please restart.{UI.RESET}")
            sys.exit(0)
    except Exception:
        pass

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

def handle_uninstall():
    print(f"{UI.YELLOW}[WARN] Uninstalling JustCompiler... / JustCompiler wordt verwijderd...{UI.RESET}")
    confirm = input("Are you sure you want to uninstall JustCompiler? (y/n): ").strip().lower()
    if confirm not in ['j', 'ja', 'y', 'yes']:
        sys.exit(0)
    install_dir = Path.home() / ".justcompiler"
    if shutil.which("docker"):
        docker_cmd = ["docker"] if platform.system() == "Windows" else ["sudo", "docker"]
        get_images = subprocess.run(docker_cmd + ["images", "justcompiler-engine", "-q"], capture_output=True, text=True)
        if get_images.stdout.strip():
            for img_id in get_images.stdout.splitlines():
                subprocess.run(docker_cmd + ["rmi", "-f", img_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if platform.system() == "Windows":
        cmd = f"Start-Sleep -s 1; Remove-Item -Recurse -Force '{install_dir}'"
        subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=0x08000000)
    else:
        cmd = f"sleep 1 && rm -rf '{install_dir}'"
        subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"{UI.GREEN}[OK] JustCompiler has been completely uninstalled.{UI.RESET}")
    sys.exit(0)


def fetch_remote_git_info(url: str) -> tuple:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    default_branch = "main"
    all_branches = []
    
    try:
        symref_res = subprocess.run(["git", "ls-remote", "--symref", url, "HEAD"], capture_output=True, text=True, env=env, timeout=4)
        if symref_res.returncode == 0:
            for line in symref_res.stdout.splitlines():
                if line.startswith("ref: refs/heads/"):
                    default_branch = line.split()[1].replace("refs/heads/", "").strip()
                    break
    except Exception: pass

    try:
        heads_res = subprocess.run(["git", "ls-remote", "--heads", url], capture_output=True, text=True, env=env, timeout=4)
        if heads_res.returncode == 0:
            for line in heads_res.stdout.splitlines():
                if "\trefs/heads/" in line:
                    b_name = line.split("\trefs/heads/")[-1].strip()
                    if b_name not in all_branches:
                        all_branches.append(b_name)
    except Exception: pass

    if default_branch in all_branches:
        all_branches.remove(default_branch)
    return default_branch, all_branches


def handle_remote_git(url: str) -> Path:
    set_current_status("Fetching git metadata...")
    branch = None
    if "#" in url: 
        url, branch = [p.strip() for p in url.split("#", 1)]

    if not branch:
        with UI.spinner("Querying remote Git repository for available branches..."):
            default_branch, other_branches = fetch_remote_git_info(url)
        
        set_current_status("Awaiting branch selection")
        branch_lines = [f" [1] 🌟 Default / Standaard ({default_branch})"]
        for idx, br in enumerate(other_branches, 2):
            branch_lines.append(f" [{idx}] 🌿 {br}")
        
        UI.draw_panel("Branch Selection", branch_lines, color=UI.CYAN)
        max_choice = len(other_branches) + 1
        
        try:
            choice_input = input(f"\n{UI.BOLD}➔ Select branch [1-{max_choice}]: {UI.RESET}").strip()
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
        except Exception:
            branch = default_branch

    set_current_status(f"Cloning branch: {branch}")
    UI.info(t('cloning'))

    cache_dir = Path("./_git_cache") / url.split("/")[-1].replace(".git", "")
    
    if cache_dir.exists():
        if platform.system() != "Windows":
            subprocess.run(f"sudo rm -rf {cache_dir}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            shutil.rmtree(cache_dir, ignore_errors=True)

    clone_cmd = ["git", "clone", "--depth", "1", "-b", branch, url, str(cache_dir)]
    result = subprocess.run(clone_cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        UI.error(t('clone_fail'))
        print(f"\n{UI.RED}Git Error Details:{UI.RESET}\n{result.stderr.strip()}")
        sys.exit(1)
        
    return cache_dir

if __name__ == "__main__":
    init_terminal_colors()

    if len(sys.argv) > 1 and sys.argv[1].lower() == "uninstall":
        handle_uninstall()

    UI.clear()
    print(f"{UI.CYAN}Select interface language / Kies taal:{UI.RESET}")
    print("  [1] English (Default)")
    print("  [2] Nederlands")
    selected_lang = "nl" if input("\nChoice / Keuze [1-2]: ").strip() == "2" else "en"
    core.set_lang(selected_lang)

    check_for_updates()

    artifacts_folder = Path("./EXECUTABLE")
    artifacts_folder.mkdir(exist_ok=True)

    start_status_listener()

    while True:
        set_current_status("Awaiting instructions")
        
        menu_items = [
            f"{UI.CYAN} {t('menu_1')}{UI.RESET}",
            f"{UI.CYAN} {t('menu_2')}{UI.RESET}",
            f"{UI.RED} {t('menu_3')}{UI.RESET}"
        ]
        UI.draw_panel(t('title'), menu_items, color=UI.CYAN)
        
        choice = input(f"\n{UI.BOLD}➔ {t('choice_prompt')}{UI.RESET}").strip()
        target = None

        if choice == "1":
            set_current_status("Waiting for local path")
            path_input = input(f"{UI.BOLD}➔ {t('path_prompt')}{UI.RESET}").strip()
            target = Path(path_input) if path_input else Path(".")
        elif choice == "2":
            set_current_status("Waiting for Git URL")
            url = input(f"{UI.BOLD}➔ {t('git_prompt')}{UI.RESET}").strip()
            if url: 
                target = handle_remote_git(url)
        else:
            UI.clear()
            sys.exit(0)

        if not target or not target.exists():
            UI.error(t('err_dir'))
            time.sleep(2)
            continue

        set_current_status("Checking configuration")
        tests = input(f"{UI.BOLD}➔ {t('test_prompt')}{UI.RESET}").strip().lower() in ['j', 'ja', 'y', 'yes']

        version_to_use = VERSION
        local_tags = docker_manager.detect_local_versions()

        if local_tags:
            set_current_status("Awaiting version selection")
            version_lines = [f" [1] 🌟 Default / Standaard ({VERSION}) [Aanbevolen]"]
            for idx, tag in enumerate(local_tags, start=2):
                version_lines.append(f" [{idx}] 📦 Hergebruik containerversie: {tag}")
            
            UI.draw_panel(t('docker_version_detected_title'), version_lines, color=UI.CYAN)
            v_choice = input(f"\n{UI.BOLD}➔ {t('docker_version_detected_prompt')}{UI.RESET}").strip()
            
            if v_choice.isdigit():
                v_idx = int(v_choice)
                if v_idx == 1:
                    version_to_use = VERSION
                elif 2 <= v_idx <= len(local_tags) + 1:
                    version_to_use = local_tags[v_idx - 2]

        docker_manager.bootstrap_sandbox(
            target_path=target, 
            artifacts_path=artifacts_folder, 
            run_tests=tests, 
            lang=selected_lang,
            set_status_fn=set_current_status,
            version_to_use=version_to_use,
            current_version=VERSION
        )
            
        input(f"\n{UI.DIM}Press Enter to return to dashboard...{UI.RESET}")
