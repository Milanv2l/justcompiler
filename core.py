import os
import sys
import subprocess
import shutil
import platform
import re
import json
from pathlib import Path

class UI:
    HEADER, BLUE, CYAN, GREEN, YELLOW, RED, RESET, BOLD = '\033[95m', '\033[94m', '\033[96m', '\033[92m', '\033[93m', '\033[91m', '\033[0m', '\033[1m'

    @classmethod
    def info(cls, msg): print(f"{cls.CYAN}❯ {msg}{cls.RESET}")
    @classmethod
    def success(cls, msg): print(f"{cls.GREEN}✔ {msg}{cls.RESET}")
    @classmethod
    def warn(cls, msg): print(f"{cls.YELLOW}⚡ {msg}{cls.RESET}")
    @classmethod
    def error(cls, msg): print(f"{cls.RED}✖ {msg}{cls.RESET}")
    @classmethod
    def log(cls, color, action, target): print(f"{color}{cls.BOLD}{action:<12}{cls.RESET} {target}")

# Centraal taalsysteem
_LANG = "en"
_STRINGS = {
    "en": {
        "title": "=== AUTOBUILDER PRO V26.0 (MULTI-LANGUAGE) ===",
        "menu_1": " [1] Build local project directory",
        "menu_2": " [2] Clone & build remote Git URL",
        "menu_3": " [3] Exit",
        "choice_prompt": "Make a choice (1-3): ",
        "path_prompt": "Path to project (Enter for current directory): ",
        "git_prompt": "Git URL (use url#branch for specific branch): ",
        "test_prompt": "Run test suites before building? (y/n): ",
        "env_title": "=== ENVIRONMENT ===",
        "env_1": " [1] Docker Sandbox (Safe & Fast)",
        "env_2": " [2] Local / Bare-Metal",
        "env_choice": "Choice [1-2, default=1]: ",
        "err_docker": "Docker is not installed. Falling back to Bare-Metal or install Docker.",
        "err_sudo": "Docker requires sudo privileges.",
        "sudo_prompt": "🔐 Enter your system password (typing is invisible): ",
        "err_auth": "Authentication failed. Falling back to Bare-Metal.",
        "err_files": "Critical files (engine.py or core.py) are missing!",
        "sandbox_prep": "Preparing sandbox...",
        "sandbox_ready": "Sandbox ready. Creating cache connections...",
        "git_clone": "Cloning repository...",
        "git_fail": "Cloning failed.",
        "err_dir": "Directory does not exist.",
        "act_detected": "Detected", "act_inspect": "Inspect", "act_build": "Building",
        "act_ready": "Compiled", "act_fail": "Failed", "act_retry": "Retry",
        "act_skipped": "Skipped", "act_saved": "Saved", "act_test": "Testing",
        "act_verify": "Verified", "act_scan": "Scanning", "act_install": "Installing",
        "act_remove": "Removing", "act_clean": "Cleaned",
        "req_msg": "Requirements: {req}",
        "test_fail_abort": "Aborted. Quality control for {name} failed!",
        "test_success": "All project tests passed successfully!",
        "err_output_title": "Critical error output:",
        "fallback_msg": "Strategy failed, loading fallback...",
        "compile_fail": "Code compilation failed.",
        "report_header": "=== PIPELINE REPORT ===",
        "report_status": " {green}{success} Passed{reset} | {red}{failed} Failed{reset} | {yellow}{skipped} Skipped{reset} ({time}s)",
        "tool_missing": "Tool '{tool}' is missing on your host.",
        "tool_prompt": "  Automatically install via {mgr}? (y/n): ",
        "cleanup_prompt": "Do you want to remove the temporary packages ({tools})? (y/n): ",
        "cleanup_host": "Host machine environment restored to clean state."
    },
    "nl": {
        "title": "=== AUTOBUILDER PRO V26.0 (MEERTALIG) ===",
        "menu_1": " [1] Lokale projectmap bouwen",
        "menu_2": " [2] Remote Git URL klonen & bouwen",
        "menu_3": " [3] Afsluiten",
        "choice_prompt": "Maak een keuze (1-3): ",
        "path_prompt": "Pad naar project (Enter voor huidige map): ",
        "git_prompt": "Git URL (gebruik url#branch voor specifieke branch): ",
        "test_prompt": "Test-suites uitvoeren voor het bouwen? (j/n): ",
        "env_title": "=== OMGEVING ===",
        "env_1": " [1] Docker Sandbox (Veilig & Snel)",
        "env_2": " [2] Lokaal / Bare-Metal",
        "env_choice": "Keuze [1-2, standaard=1]: ",
        "err_docker": "Docker is niet geïnstalleerd. Val terug op Bare-Metal of installeer Docker.",
        "err_sudo": "Docker vereist sudo-rechten.",
        "sudo_prompt": "🔐 Voer je systeemwachtwoord in (typen is onzichtbaar): ",
        "err_auth": "Authenticatie mislukt. Val terug op Bare-Metal.",
        "err_files": "Critische bestanden (engine.py of core.py) missen!",
        "sandbox_prep": "Sandbox voorbereiden...",
        "sandbox_ready": "Sandbox gereed. Cache verbindingen maken...",
        "git_clone": "Repository klonen...",
        "git_fail": "Klonen mislukt.",
        "err_dir": "Map bestaat niet.",
        "act_detected": "Gedetecteerd", "act_inspect": "Inspecteer", "act_build": "Bouwen",
        "act_ready": "Klaar", "act_fail": "Mislukt", "act_retry": "Herproberen",
        "act_skipped": "Overgeslagen", "act_saved": "Opgeslagen", "act_test": "Testen",
        "act_verify": "Geverifieerd", "act_scan": "Scannen", "act_install": "Installatie",
        "act_remove": "Verwijderen", "act_clean": "Opgeschoond",
        "req_msg": "Vereisten: {req}",
        "test_fail_abort": "Afgebroken. Kwaliteitscontrole voor {name} mislukt!",
        "test_success": "Alle project-tests zijn succesvol geslaagd!",
        "err_output_title": "Belangrijkste foutmeldingen:",
        "fallback_msg": "Strategie faalde, fallback laden...",
        "compile_fail": "Code kon niet gecompileerd worden.",
        "report_header": "=== RAPPORTAGE ===",
        "report_status": " {green}{success} Geslaagd{reset} | {red}{failed} Mislukt{reset} | {yellow}{skipped} Overgeslagen{reset} ({time}s)",
        "tool_missing": "Tool '{tool}' ontbreekt op je host.",
        "tool_prompt": "  Automatisch installeren via {mgr}? (j/n): ",
        "cleanup_prompt": "Wil je de tijdelijke pakketten ({tools}) weer verwijderen? (j/n): ",
        "cleanup_host": "Host machine is weer netjes."
    }
}

def set_lang(lang_code):
    global _LANG
    if lang_code in _STRINGS: _LANG = lang_code

def t(key, **kwargs):
    return _STRINGS[_LANG].get(key, _STRINGS["en"].get(key, key)).format(**kwargs)

PKG_MAP = {
    "dnf": {
        "java": ["java-latest-openjdk-devel"], "go": ["golang"], "cargo": ["cargo"],
        "npm": ["nodejs"], "cmake": ["cmake"], "make": ["make", "gcc-c++"],
        "php": ["php-cli"], "composer": ["composer"], "ruby": ["ruby"],
        "python3": ["python3-devel", "python3-pip"], "dotnet": ["dotnet-sdk-8.0"],
        "kernel-build": ["gcc", "make", "flex", "bison", "bc", "elfutils-libelf-devel", "openssl-devel"],
        "valac": ["vala", "meson", "ninja-build"], "shards": ["crystal"]
    },
    "apt": {
        "java": ["openjdk-25-jdk"], "go": ["golang-go"], "cargo": ["cargo"],
        "npm": ["nodejs", "npm"], "cmake": ["cmake"], "make": ["build-essential"],
        "php": ["php-cli"], "composer": ["composer"], "ruby": ["ruby-full"],
        "python3": ["python3-dev", "python3-pip"], "dotnet": ["dotnet-sdk-8.0"],
        "kernel-build": ["build-essential", "flex", "bison", "bc", "libelf-dev", "libssl-dev"],
        "valac": ["valac", "meson", "ninja-build"], "shards": ["crystal"]
    },
    "pacman": {
        "java": ["jdk-openjdk"], "go": ["go"], "cargo": ["rust"],
        "npm": ["nodejs", "npm"], "cmake": ["cmake"], "make": ["base-devel"],
        "php": ["php"], "composer": ["composer"], "ruby": ["ruby"],
        "python3": ["python", "python-pip"], "dotnet": ["dotnet-sdk"],
        "kernel-build": ["base-devel", "bc", "flex", "bison"],
        "valac": ["vala", "meson", "ninja"], "shards": ["crystal", "shards"]
    },
    "apk": {
        "java": ["openjdk21"], "go": ["go"], "cargo": ["cargo"],
        "npm": ["nodejs", "npm"], "cmake": ["cmake"], "make": ["build-base"],
        "php": ["php83"], "composer": ["composer"], "ruby": ["ruby"],
        "python3": ["python3-dev", "py3-pip"], "dotnet": ["dotnet8-sdk"],
        "kernel-build": ["build-base", "flex", "bison", "bc", "elfutils-dev", "openssl-dev"],
        "valac": ["vala", "meson"], "shards": ["crystal"]
    },
    "zypper": {
        "java": ["java-21-openjdk-devel"], "go": ["go"], "cargo": ["cargo"],
        "npm": ["nodejs", "npm"], "cmake": ["cmake"], "make": ["make", "gcc-c++"],
        "php": ["php8"], "composer": ["composer"], "ruby": ["ruby"],
        "python3": ["python3-devel", "python3-pip"], "dotnet": ["dotnet-sdk"],
        "kernel-build": ["gcc", "make", "flex", "bison", "bc", "libelf-devel", "libopenssl-devel"],
        "valac": ["vala", "meson"], "shards": ["crystal"]
    },
    "winget": {
        "java": ["Microsoft.OpenJDK.21"], "go": ["GoLang.Go"], "cargo": ["Rustlang.Rust.MSVC"],
        "npm": ["OpenJS.NodeJS"], "cmake": ["Kitware.CMake"], "make": ["Ezwinports.Make"],
        "php": ["PHP.PHP"], "composer": ["Composer.Composer"], "ruby": ["RubyInstallerTeam.Ruby"],
        "python3": ["Python.Python.3.12"], "dotnet": ["Microsoft.DotNet.SDK.8"]
    }
}

class DependencyManager:
    def __init__(self, auto_install: bool = False):
        self.in_docker = Path("/workspace").exists() or os.path.exists("/.dockerenv")
        self.installed_tools = []
        self.auto_install = auto_install

    def inspect_version(self, root: Path, tool: str) -> str:
        try:
            if tool in ["npm", "pnpm", "yarn"]:
                pkg_json = root / "package.json"
                if pkg_json.exists():
                    data = json.loads(pkg_json.read_text(encoding="utf-8"))
                    if "node" in data.get("engines", {}): return f"Node.js {data['engines']['node']}"
                    deps = data.get("dependencies", {})
                    if deps: return f"Node (Deps: {', '.join([f'{k}@{v}' for k, v in list(deps.items())[:2]])})"
            elif tool == "go":
                go_mod = root / "go.mod"
                if go_mod.exists():
                    match = re.search(r"^go\s+([\d\.]+)", go_mod.read_text(encoding="utf-8"), re.MULTILINE)
                    if match: return f"Go v{match.group(1)}"
            elif tool in ["mvn", "gradle"]:
                pom = root / "pom.xml"
                if pom.exists():
                    match = re.search(r"<(?:java\.version|maven\.compiler\.release|maven\.compiler\.target)>([\d\.]+)</", pom.read_text(encoding="utf-8"))
                    if match: return f"Java v{match.group(1)}"
                gradle = root / "build.gradle"
                if gradle.exists():
                    match = re.search(r"(?:sourceCompatibility|targetCompatibility|release)\s*=\s*['\"]?([\d\.]+)['\"]?", gradle.read_text(encoding="utf-8"))
                    if match: return f"Java compatibility {match.group(1)}"
        except Exception: pass
        return "System-default"

    def get_pkg_manager(self):
        if platform.system() == "Windows":
            if shutil.which("winget"): return "winget"
        elif platform.system() == "Linux":
            if shutil.which("dnf"): return "dnf"
            if shutil.which("apt-get"): return "apt"
            if shutil.which("pacman"): return "pacman"
            if shutil.which("apk"): return "apk"
            if shutil.which("zypper"): return "zypper"
        return None

    def trigger_install(self, tool: str) -> bool:
        mgr = self.get_pkg_manager()
        if not mgr or not PKG_MAP.get(mgr, {}).get(tool): return False

        if not self.auto_install:
            print(f"\n{UI.YELLOW}{UI.BOLD}⚡ {t('tool_missing', tool=tool)}{UI.RESET}")
            if input(t('tool_prompt', mgr=mgr)).strip().lower() not in ['j', 'ja', 'y', 'yes']: return False
        else:
            UI.log(UI.YELLOW, t('act_install'), f"Automatische bare-metal installatie voor '{tool}' gestart via {mgr}...")

        success = True
        for pkg in PKG_MAP[mgr][tool]:
            UI.log(UI.CYAN, t('act_install'), f"'{pkg}' via {mgr}...")

            if mgr == "winget": cmd = f"winget install --silent --accept-package-agreements --accept-source-agreements {pkg}"
            elif mgr == "dnf": cmd = f"sudo dnf install -y {pkg}"
            elif mgr == "apt": cmd = f"sudo apt-get update && sudo apt-get install -y {pkg}"
            elif mgr == "pacman": cmd = f"sudo pacman -S --noconfirm {pkg}"
            elif mgr == "apk": cmd = f"sudo apk add {pkg}"
            elif mgr == "zypper": cmd = f"sudo zypper in -y {pkg}"

            if subprocess.run(cmd, shell=True).returncode == 0:
                if (pkg, mgr) not in self.installed_tools: self.installed_tools.append((pkg, mgr))
            else: success = False
        return success

    def cleanup(self):
        if not self.installed_tools: return

        if self.auto_install:
            print("\n" + "=" * 55)
            UI.success("Bare-metal build voltooid. Geïnstalleerde runtime/compilers zijn behouden op je host.")
            return

        print("\n" + "=" * 55)
        tools = ", ".join([pkg for pkg, _ in self.installed_tools])
        if input(f"{UI.YELLOW}{UI.BOLD}❯ {t('cleanup_prompt', tools=tools)}{UI.RESET}").strip().lower() in ['j', 'ja', 'y', 'yes']:
            for pkg, mgr in self.installed_tools:
                UI.log(UI.CYAN, t('act_remove'), f"'{pkg}' via {mgr}...")
                if mgr == "winget": cmd = f"winget uninstall --silent {pkg}"
                elif mgr == "dnf": cmd = f"sudo dnf remove -y {pkg}"
                elif mgr == "apt": cmd = f"sudo apt-get purge -y {pkg} && sudo apt-get autoremove -y"
                elif mgr == "pacman": cmd = f"sudo pacman -Rns --noconfirm {pkg}"
                elif mgr == "apk": cmd = f"sudo apk del {pkg}"
                elif mgr == "zypper": cmd = f"sudo zypper rm -y {pkg}"
                subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            UI.log(UI.GREEN, t('act_clean'), t('cleanup_host'))
