import os
import sys
import subprocess
import shutil
import argparse
import platform
import urllib.request
import threading
from pathlib import Path
import core
from core import UI, t
import docker_manager

# --- JUSTCOMPILER VERSION ---
VERSION = "1.1.9"

# Globale statusvariabele voor de sneltoets-announcer
CURRENT_STATUS = "Opstarten... / Starting up..."

def set_current_status(msg: str):
    global CURRENT_STATUS
    CURRENT_STATUS = msg

def status_reporter_loop():
    """Luistert op de achtergrond naar de 's' toets om de status te melden."""
    while True:
        try:
            user_input = input().strip().lower()
            if user_input == 's':
                print(f"\n{UI.CYAN}[JUSTCOMPILER STATUS]: {CURRENT_STATUS}{UI.RESET}\n")
        except (IOError, ValueError, EOFError):
            break

def start_status_listener():
    """Start de status-listener veilig als achtergrond-thread."""
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
    CURRENT_STATUS = "Controleren op updates via GitHub..."
    current_dir = Path(__file__).resolve().parent
    version_url = "https://raw.githubusercontent.com/Milanv2l/justcompiler/main/version.txt"
    
    try:
        with urllib.request.urlopen(version_url, timeout=1.5) as response:
            remote_version = response.read().decode('utf-8').strip()
        
        if remote_version != VERSION:
            print(f"{UI.CYAN}[INFO] New update found ({remote_version}). Downloading components...{UI.RESET}")
            files = ["justcompiler.py", "core.py", "engine.py", "plugins.json"]
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

def handle_remote_git(url: str) -> Path:
    global CURRENT_STATUS
    CURRENT_STATUS = "Remote Git repository aan het binnenhalen..."
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

    # TAALSELECTIE DIRECT ALS EERSTE UITVOEREN
    print("Select language / Kies taal:")
    print("  1. English (Default)")
    print("  2. Nederlands")
    lang_choice = input("Choice / Keuze [1-2]: ").strip()
    selected_lang = "nl" if lang_choice == "2" else "en"
    core.set_lang(selected_lang)

    if len(sys.argv) > 1 and sys.argv[1].lower() == "uninstall":
        handle_uninstall()
        sys.exit(0)

    check_for_updates()

    artifacts_folder = Path("./EXECUTABLE")
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

    # OPTIE VOOR OUDE DOCKER-VERSION OPVRAGEN
    custom_docker_ver = input(f"{UI.YELLOW}{t('docker_version_prompt', version=VERSION)}{UI.RESET}").strip()
    if not custom_docker_ver:
        custom_docker_ver = None

    start_status_listener()

    # Altijd via Docker Sandbox starten
    docker_manager.bootstrap_sandbox(
        target_path=target, 
        artifacts_path=artifacts_folder, 
        run_tests=tests, 
        lang=selected_lang,
        set_status_fn=set_current_status,
        custom_version=custom_docker_ver,
        current_version=VERSION
    )
