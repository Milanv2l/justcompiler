import os
import sys
import subprocess
import shutil
import platform
import urllib.request
import time
import json
import hashlib
import datetime
from pathlib import Path
import core
from core import UI, t
import docker_manager

VERSION = "1.3.2"
CURRENT_STATUS = "Standby"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
UPDATE_FILES = ["justcompiler.py", "core.py", "engine.py", "docker_manager.py", "plugins.json", "checksums.txt"]

def verify_checksum(file_path, expected_hash):
    sha256 = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                sha256.update(chunk)
        return sha256.hexdigest() == expected_hash
    except Exception:
        return False

def load_checksums(file_path):
    try:
        sums = {}
        for line in Path(file_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0]
        return sums
    except Exception:
        return {}

def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    default = {"check_updates": True, "run_tests": False, "base_image": "ubuntu:24.04", "theme": "default"}
    try:
        CONFIG_FILE.write_text(json.dumps(default, indent=4), encoding="utf-8")
    except Exception:
        pass
    return default

def save_config(**updates):
    config = load_config()
    config.update(updates)
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=4), encoding="utf-8")
    except Exception:
        pass
    return config

def set_current_status(msg: str):
    global CURRENT_STATUS
    CURRENT_STATUS = msg

def init_terminal_colors():
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

def check_for_updates():
    config = load_config()
    if not config.get("check_updates", True):
        return
    try:
        _do_update(ask=True)
    except Exception:
        pass

def _scan_targets(root: Path) -> list:
    """Walk project and return detected build targets with platform/modloader info."""
    targets = []
    seen_plugins = set()
    plugins_path = Path(__file__).resolve().parent / "plugins.json"
    if not plugins_path.exists():
        return targets
    try:
        plugins = json.loads(plugins_path.read_text())
    except Exception:
        return targets

    for dirpath, dirs, _ in os.walk(str(root)):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                   ["node_modules", "target", "build", "dist", "bin", "venv", "__pycache__", "BUILD_ARTIFACTS", "_git_cache"]]
        files = set(os.listdir(dirpath))
        for idx, p in enumerate(plugins):
            pname = p["name"]
            if pname in seen_plugins:
                continue
            if any(f in files for f in p["detect"]) or \
               any(any(os.path.splitext(f)[0].endswith(d.rstrip('*').rstrip('.')) or
                       f.endswith(d.replace('*', '')) for f in files) for d in p["detect"] if '*' in d):
                seen_plugins.add(pname)
                platform = _classify_platform(dirpath, pname, p.get("tool", ""))
                targets.append({"name": pname, "plugin_idx": idx, "platform": platform,
                                "dir": dirpath, "tool": p["tool"]})
                break
    return targets

def _classify_platform(dirpath: str, plugin_name: str, tool: str) -> str:
    """Detect platform/modloader from project files."""
    d = Path(dirpath)
    files = set(f.name for f in d.iterdir() if f.is_file())
    upper = set(f.name for f in d.parent.iterdir() if f.is_file()) if d.parent != d else set()
    all_files = files | upper

    if "fabric.mod.json" in all_files:
        return "Minecraft Fabric Mod"
    if "quilt.mod.json" in all_files:
        return "Minecraft Quilt Mod"
    if "neoforge.mods.toml" in all_files:
        return "Minecraft NeoForge Mod"
    if "mods.toml" in all_files:
        return "Minecraft Forge Mod"
    if "plugin.yml" in all_files:
        return "Bukkit/Paper Plugin"
    if "bungee.yml" in all_files:
        return "BungeeCord Plugin"
    if "velocity-plugin.json" in all_files:
        return "Velocity Plugin"
    if "fxmanifest.lua" in all_files or "__resource.lua" in all_files:
        return "FiveM/RedM Resource"
    if "build.txt" in all_files:
        return "tModLoader Mod"
    if "mod.conf" in all_files:
        return "Luanti Mod"
    if "project.godot" in all_files:
        return "Godot Project"
    if any(f.endswith(".toc") for f in all_files):
        return "WoW Addon"
    if "AndroidManifest.xml" in all_files:
        return "Android App"

    if tool in ("pnpm", "npm", "yarn", "bun", "deno"):
        return "Node.js App"
    if tool == "go":
        return "Go App"
    if tool == "cargo" or "rust" in plugin_name.lower():
        return "Rust App"
    if tool in ("gradle", "mvn", "ant", "sbt"):
        if "java" in plugin_name.lower():
            return "Java Library"
        if "kotlin" in plugin_name.lower():
            return "Kotlin App"
        return "JVM Project"
    if "python" in tool or tool == "pip":
        return "Python App"
    if tool in ("flutter", "dart"):
        return "Dart/Flutter App"
    if tool in ("swift", "xcode"):
        return "Swift/Xcode App"
    return "Unknown"

def _auto_select_target(project_root: Path, targets: list) -> str:
    if not targets:
        return ""
    if len(targets) == 1:
        return targets[0]["name"]
    try:
        pdata = json.loads((Path(__file__).resolve().parent / "plugins.json").read_text())
    except Exception:
        return targets[0]["name"]
    markers = {}
    for f in project_root.rglob("*"):
        if not f.is_file():
            continue
        for t in targets:
            plugin = pdata[t["plugin_idx"]]
            for d in plugin.get("detect", []):
                if "*" in d:
                    pat = d.replace("*", "")
                    if f.name.endswith(pat) or f.name == pat:
                        markers[t["name"]] = markers.get(t["name"], 0) + 1
                elif "/" not in d:
                    if f.name == d:
                        markers[t["name"]] = markers.get(t["name"], 0) + 2
                else:
                    rel = str(f.relative_to(project_root)).replace("\\", "/")
                    if rel == d:
                        markers[t["name"]] = markers.get(t["name"], 0) + 3
    if markers:
        return max(markers, key=markers.get)
    return targets[0]["name"]

def _force_update(selected_lang):
    print()
    try:
        if _do_update(ask=False, force=True):
            print(f"{UI.GREEN}[OK] JustCompiler updated! Please restart.{UI.RESET}")
            sys.exit(0)
    except Exception as e:
        print(f"{UI.RED}[ERR] Update failed: {e}{UI.RESET}")
        input(f"\n{UI.CYAN}{UI.BOLD}Press Enter to return...{UI.RESET}")
        return selected_lang
    print(f"{UI.YELLOW}[INFO] You are already on the latest version ({VERSION}).{UI.RESET}")
    input(f"\n{UI.CYAN}{UI.BOLD}Press Enter to return...{UI.RESET}")
    return selected_lang

def _do_update(ask=True, force=False):
    global CURRENT_STATUS
    current_dir = Path(__file__).resolve().parent
    version_url = "https://raw.githubusercontent.com/Milanv2l/justcompiler/main/version.txt"
    with urllib.request.urlopen(version_url, timeout=3) as response:
        remote_version = response.read().decode('utf-8').strip()
    if not force and remote_version == VERSION:
        return False if not ask else None
    if ask:
        print(f"{UI.YELLOW}[INFO] New version {remote_version} available (current: {VERSION}){UI.RESET}")
        confirm = input(f"{UI.CYAN}{UI.BOLD}Update to v{remote_version}? (y/n): {UI.RESET}").strip().lower()
        if confirm not in ['j', 'ja', 'y', 'yes']:
            return
    if force:
        set_current_status(f"Re-downloading v{remote_version}...")
    else:
        set_current_status(f"Downloading v{remote_version}...")
    base_url = f"https://raw.githubusercontent.com/Milanv2l/justcompiler/v{remote_version}"
    version_label = remote_version
    temp_dir = current_dir / f".update_{remote_version}"
    temp_dir.mkdir(exist_ok=True)
    try:
        for file_name in UPDATE_FILES:
            file_url = f"{base_url}/{file_name}"
            try:
                with urllib.request.urlopen(file_url, timeout=5) as file_response:
                    (temp_dir / file_name).write_bytes(file_response.read())
            except Exception:
                file_url = f"https://raw.githubusercontent.com/Milanv2l/justcompiler/main/{file_name}"
                with urllib.request.urlopen(file_url, timeout=5) as file_response:
                    (temp_dir / file_name).write_bytes(file_response.read())
        checksums = load_checksums(temp_dir / "checksums.txt")
        if checksums:
            all_ok = True
            for fname in UPDATE_FILES:
                if fname == "checksums.txt":
                    continue
                if fname in checksums and not verify_checksum(temp_dir / fname, checksums[fname]):
                    UI.warn(f"Checksum mismatch: {fname}")
                    all_ok = False
            if not all_ok:
                print(f"{UI.RED}[ERR] Checksum verification failed. Update aborted.{UI.RESET}")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return False
        for file_name in UPDATE_FILES:
            src = temp_dir / file_name
            if src.exists():
                shutil.copy2(src, current_dir / file_name)
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"{UI.GREEN}[OK] JustCompiler updated to {remote_version}! Please restart.{UI.RESET}")
        return True
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

def show_tui_header():
    UI.clear()
    system_str = f"{platform.system()} ({platform.machine()})"
    docker_status = f"{UI.GREEN}Available{UI.RESET}" if shutil.which("docker") else f"{UI.RED}Missing{UI.RESET}"
    lines = [
        f"{UI.BOLD}Version:{UI.RESET} {VERSION}  │  {UI.BOLD}System:{UI.RESET} {system_str}",
        f"{UI.BOLD}Docker:{UI.RESET} {docker_status}  │  {UI.BOLD}Status:{UI.RESET} {UI.YELLOW}{CURRENT_STATUS}{UI.RESET}",
    ]
    UI.draw_panel("JustCompiler Hub", lines, color=UI.MAGENTA)

def _remove_alias():
    profile_files = []
    if platform.system() == "Windows":
        profile = os.environ.get("PROFILE", "")
        if profile:
            profile_files.append(Path(profile))
    else:
        for f in [Path.home() / ".bashrc", Path.home() / ".zshrc", Path.home() / ".bash_profile"]:
            if f.exists():
                profile_files.append(f)
    for pf in profile_files:
        try:
            lines = pf.read_text(encoding="utf-8").splitlines(keepends=True)
            filtered = [l for l in lines if "justcompiler" not in l or "alias" not in l]
            if len(filtered) != len(lines):
                pf.write_text("".join(filtered), encoding="utf-8")
        except Exception:
            pass

def handle_uninstall():
    print(f"{UI.YELLOW}[WARN] Uninstalling JustCompiler... / JustCompiler wordt verwijderd...{UI.RESET}")
    confirm = input(f"{UI.CYAN}{UI.BOLD}Are you sure you want to uninstall JustCompiler? (y/n): {UI.RESET}").strip().lower()
    if confirm not in ['j', 'ja', 'y', 'yes']:
        sys.exit(0)
    _remove_alias()
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
    show_tui_header()
    branch = None
    if "#" in url: 
        url, branch = [p.strip() for p in url.split("#", 1)]

    if not branch:
        if hasattr(UI, 'spinner'):
            with UI.spinner("Querying remote Git repository for available branches..."):
                default_branch, other_branches = fetch_remote_git_info(url)
        else:
            print("Querying remote Git repository for available branches...")
            default_branch, other_branches = fetch_remote_git_info(url)
        
        set_current_status("Awaiting branch selection")
        show_tui_header()
        
        branch_lines = [f" [1] 🌟 Default / Standaard ({default_branch})"]
        for idx, br in enumerate(other_branches, 2):
            branch_lines.append(f" [{idx}] 🌿 {br}")
        
        if hasattr(UI, 'draw_panel'):
            UI.draw_panel("Branch Selection", branch_lines, color=UI.CYAN)
        else:
            print("=== Branch Selection ===")
            for bl in branch_lines: print(bl)
            
        max_choice = len(other_branches) + 1
        
        try:
            sys.stdout.flush()
            choice_input = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Select branch [1-{max_choice}]: {UI.RESET}").strip()
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
    show_tui_header()
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

def handle_settings(selected_lang):
    while True:
        show_tui_header()
        config = load_config()
        lang_name = "English" if selected_lang == "en" else "Nederlands"
        updates_status = t("settings_on") if config.get("check_updates", True) else t("settings_off")
        tests_status = t("settings_on") if config.get("run_tests", False) else t("settings_off")
        current_theme = config.get("theme", "default")
        theme_name = t("theme_default") if current_theme == "default" else t("theme_minimal")
        lines = [
            f" {t('settings_lang', lang=lang_name)}",
            f" {t('settings_updates', status=updates_status)}",
            f" {t('settings_tests', status=tests_status)}",
            f" {t('settings_theme', theme=theme_name)}",
            f" {t('settings_force_update')}",
            f" {t('settings_back')}"
        ]
        UI.draw_panel(t('settings_title'), lines, color=UI.YELLOW)
        sys.stdout.flush()
        s = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{t('settings_prompt')}{UI.RESET}").strip()
        if s == "1":
            print(f"\n{UI.CYAN}Language / Taal:{UI.RESET}")
            print("  [1] English")
            print("  [2] Nederlands")
            sys.stdout.flush()
            c = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Choice / Keuze [1-2]: {UI.RESET}").strip()
            new_lang = "nl" if c == "2" else "en"
            core.set_lang(new_lang)
            save_config(lang=new_lang)
            selected_lang = new_lang
        elif s == "2":
            new_val = not config.get("check_updates", True)
            save_config(check_updates=new_val)
        elif s == "3":
            new_val = not config.get("run_tests", False)
            save_config(run_tests=new_val)
        elif s == "4":
            new_theme = "minimal" if current_theme == "default" else "default"
            save_config(theme=new_theme)
            UI.border_enabled = new_theme != "minimal"
        elif s == "5":
            return _force_update(selected_lang)
        else:
            return selected_lang

def _auto_pick_artifact(artifacts: list) -> tuple | None:
    scores = []
    for kind, name, cmd in artifacts:
        score = 0
        low = name.lower()
        if "-sources" in low or "-javadoc" in low or "-doc" in low:
            score -= 100
        if kind == "mod":
            score += 50
        elif kind in ("plugin", "bungee-plugin", "velocity-plugin"):
            score += 40
        elif kind == "binary":
            score += 10
        elif kind == "executable":
            score += 10
        scores.append((score, kind, name, cmd))
    scores.sort(key=lambda x: (-x[0], x[2]))
    if scores and scores[0][0] > -100:
        return (scores[0][1], scores[0][2], scores[0][3])
    return artifacts[0] if artifacts else None

def _detect_artifacts(folder: Path) -> list:
    found = []
    is_windows = platform.system() == "Windows"
    is_macos = platform.system() == "Darwin"

    for f in folder.iterdir():
        if not f.is_file() or f.stat().st_size == 0:
            continue

        # JAR — cross-platform
        if f.suffix == ".jar":
            kind = _classify_jar(f)
            found.append((kind, f.name, ["java", "-jar", str(f)]))
            continue

        # Python — cross-platform
        if f.suffix == ".py":
            py_cmd = "python" if is_windows else "python3"
            found.append(("python", f.name, [py_cmd, str(f)]))
            continue

        # JavaScript — cross-platform
        if f.suffix == ".js":
            found.append(("node", f.name, ["node", str(f)]))
            continue

        # Windows-specific
        if is_windows:
            if f.suffix in (".exe", ".bat", ".cmd"):
                found.append(("executable", f.name, [str(f)]))
            continue

        # macOS-specific
        if is_macos:
            try:
                magic = f.read_bytes()[:4]
            except Exception:
                magic = b""
            # Mach-O fat/universal binary
            if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
                found.append(("binary", f.name, [str(f)]))
            # Mach-O 64-bit
            elif magic in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
                found.append(("binary", f.name, [str(f)]))
            # Mach-O 32-bit
            elif magic in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"):
                found.append(("binary", f.name, [str(f)]))
            elif f.suffix in (".sh", ".bash"):
                found.append(("script", f.name, ["bash", str(f)]))
            continue

        # Linux / generic Unix
        try:
            magic = f.read_bytes()[:4]
        except Exception:
            magic = b""
        if magic == b"\x7fELF":
            found.append(("binary", f.name, [str(f)]))
        elif f.suffix in (".sh", ".bash"):
            found.append(("script", f.name, ["bash", str(f)]))

    return found

def _classify_jar(path: Path) -> str:
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "fabric.mod.json" in names or "quilt.mod.json" in names:
                return "mod"
            if "META-INF/mods.toml" in names or "META-INF/neoforge.mods.toml" in names:
                return "mod"
            if "plugin.yml" in names:
                return "plugin"
            if "bungee.yml" in names:
                return "bungee-plugin"
            if "velocity-plugin.json" in names:
                return "velocity-plugin"
    except Exception:
        pass
    return "jar"

if __name__ == "__main__":
    init_terminal_colors()

    if len(sys.argv) > 1:
        if sys.argv[1].lower() == "uninstall":
            handle_uninstall()
        if sys.argv[1].lower() in ("--version", "-v"):
            print(f"JustCompiler v{VERSION}")
            sys.exit(0)

    config = load_config()
    UI.border_enabled = config.get("theme", "default") != "minimal"
    selected_lang = "en"

    for i, arg in enumerate(sys.argv):
        if arg in ("--lang", "-l") and i + 1 < len(sys.argv):
            selected_lang = sys.argv[i + 1] if sys.argv[i + 1] in ("en", "nl") else "en"
            save_config(lang=selected_lang)
            break
    else:
        if "lang" in config:
            selected_lang = config["lang"]
        else:
            UI.clear()
            print(f"{UI.CYAN}Select interface language / Kies taal:{UI.RESET}")
            print("  [1] English (Default)")
            print("  [2] Nederlands")
            sys.stdout.flush()
            lang_choice = input(f"\n{UI.CYAN}{UI.BOLD}Choice / Keuze [1-2]: {UI.RESET}").strip()
            selected_lang = "nl" if lang_choice == "2" else "en"
            save_config(lang=selected_lang)

    core.set_lang(selected_lang)
    show_tui_header()
    check_for_updates()

    artifacts_folder = Path("./EXECUTABLE")
    artifacts_folder.mkdir(exist_ok=True)

    while True:
        set_current_status("Awaiting instructions")
        show_tui_header()

        menu_items = [
            f"{UI.CYAN} {t('menu_1')}{UI.RESET}",
            f"{UI.CYAN} {t('menu_2')}{UI.RESET}",
            f"{UI.YELLOW} {t('menu_3')}{UI.RESET}",
            f"{UI.RED} {t('menu_4')}{UI.RESET}"
        ]

        UI.draw_panel(t('title'), menu_items, color=UI.CYAN)
        sys.stdout.flush()
        choice = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{t('choice_prompt')}{UI.RESET}").strip()
        target = None

        if choice == "1":
            UI.info(t('path_prompt'))
            path_input = input(f"{UI.CYAN}{UI.BOLD}➔ {UI.RESET}").strip()
            target = Path(path_input) if path_input else Path(".")
        elif choice == "2":
            UI.info(t('git_prompt'))
            url = input(f"{UI.CYAN}{UI.BOLD}➔ {UI.RESET}").strip()
            if url:
                target = handle_remote_git(url)
        elif choice == "3":
            selected_lang = handle_settings(selected_lang)
            continue
        else:
            UI.clear()
            sys.exit(0)

        if not target or not target.exists():
            UI.error(t('err_dir'))
            time.sleep(2)
            continue

        # Scan and auto-select build target
        set_current_status("Scanning project...")
        show_tui_header()
        targets = _scan_targets(target)
        target_filter = _auto_select_target(target, targets)
        if target_filter:
            UI.log(UI.GREEN, t('build_selected'), target_filter)
        else:
            UI.log(UI.YELLOW, t('build_auto'), "")

        tests = load_config().get("run_tests", False)
        if tests:
            UI.info(t('test_prompt') + " " + t('settings_on'))

        base_image = load_config().get("base_image", "ubuntu:24.04")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        project_name = target.resolve().name
        build_folder = artifacts_folder / f"{project_name}_{ts}"
        build_folder.mkdir(parents=True, exist_ok=True)
        success = docker_manager.bootstrap_sandbox(
            target_path=target,
            artifacts_path=build_folder,
            run_tests=tests,
            lang=selected_lang,
            set_status_fn=set_current_status,
            base_image=base_image,
            target_filter=target_filter
        )

        sys.stdout.flush()
        if success and any(build_folder.iterdir()):
            artifacts = _detect_artifacts(build_folder)
            if artifacts:
                best = _auto_pick_artifact(artifacts)
                if best:
                    kind, name, cmd = best
                    UI.success(f"{t('build_ready')} {name} ({kind})")
                    print(f"{UI.DIM}─" * 60 + f"{UI.RESET}")
                    subprocess.run(cmd, shell=platform.system() == "Windows")
                    print(f"{UI.DIM}─" * 60 + f"{UI.RESET}")
            ans = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{t('open_folder')} ").strip().lower()
            if ans in ['j', 'ja', 'y', 'yes']:
                if platform.system() == "Windows":
                    subprocess.Popen(["explorer", str(build_folder.resolve())])
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", str(build_folder.resolve())])
                else:
                    subprocess.Popen(["xdg-open", str(build_folder.resolve())])
        input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{UI.DIM}{t('press_enter')}{UI.RESET}")
