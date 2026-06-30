import os
import sys
import subprocess
import shutil
import threading
import time
from pathlib import Path

_CURRENT_LANG = "en"

class _SpinnerContext:
    def __init__(self, text: str):
        self.text = text
        self.is_spinning = False
        self.thread = None
        self.success = True
        self.spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def spin(self):
        i = 0
        while self.is_spinning:
            sys.stdout.write(f"\r\033[K{UI.CYAN}{self.spinner_chars[i]}{UI.RESET} {self.text}")
            sys.stdout.flush()
            i = (i + 1) % len(self.spinner_chars)
            time.sleep(0.08)

    def __enter__(self):
        self.is_spinning = True
        self.thread = threading.Thread(target=self.spin, daemon=True)
        self.thread.start()
        return self

    def fail(self):
        self.success = False

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.is_spinning = False
        if self.thread:
            self.thread.join()
        
        sys.stdout.write('\r\033[K')
        sys.stdout.flush()
        
        if exc_type is not None or not self.success:
            UI.error(self.text)
        else:
            UI.success(self.text)


class UI:
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    HEADER = "\033[95m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    @classmethod
    def log(cls, color, prefix, msg):
        print(f"{color}{prefix}{cls.RESET} {msg}")

    @staticmethod
    def info(msg: str):
        print(f"{UI.CYAN}ℹ{UI.RESET} {msg}")

    @staticmethod
    def success(msg: str):
        print(f"{UI.GREEN}✓{UI.RESET} {msg}")

    @staticmethod
    def warn(msg: str):
        print(f"{UI.YELLOW}⚠{UI.RESET} {msg}")

    @staticmethod
    def error(msg: str):
        print(f"{UI.RED}✖{UI.RESET} {msg}")

    @staticmethod
    def header(title: str):
        print(f"\n{UI.BOLD}{UI.MAGENTA}── {title} ──{UI.RESET}")

    @staticmethod
    def spinner(msg: str) -> _SpinnerContext:
        return _SpinnerContext(msg)

_TRANSLATIONS = {
    "en": {
        "title": "JustCompiler",
        "menu_1": "  1. Compile local workspace",
        "menu_2": "  2. Compile remote Git repository",
        "menu_3": "  3. Exit Application",
        "choice_prompt": "Enter choice [1-3]: ",
        "path_prompt": "Local workspace path (Leave empty for current dir): ",
        "git_prompt": "Remote Git URL (HTTPS): ",
        "test_prompt": "Run tests automatically during build? (y/n): ",
        
        "docker_version_detected_title": "Detected Local Sandbox Container Environments:",
        "docker_version_detected_prompt": "Select which sandbox version to deploy [1-X, Default=1]: ",
        
        "err_dir": "Invalid target path or directory does not exist.",
        "cloning": "Cloning repository into temporary workspace...",
        "clone_fail": "Failed to clone remote repository.",
        "err_docker": "Docker is not installed or not running on this system.",
        "err_sudo": "Docker requires root privileges.",
        "sudo_prompt": "Please enter sudo password if prompted: ",
        "err_auth": "Authentication failed. Cannot run Docker sandbox.",
        "err_files": "Critical framework components (engine.py/core.py) are missing.",
        "err_custom_version_missing": "Specified old Docker image tag 'justcompiler-engine:{version}' was not found locally!",

        # Docker Status Messages
        "docker_cache_check": "Checking Docker cache and existing environments...",
        "docker_clean_old": "New script version detected! Cleaning up old Docker sandboxes...",
        "docker_building_base": "Building Modern Ubuntu 26.04 LTS Sandbox base image...",
        "docker_building_spinner": "Building Modern Ubuntu 26.04 LTS Sandbox Environment...",
        "docker_reusing_old": "Successfully mapped and reusing old Docker container version: {version}",
        "docker_compiling_status": "Compiling project on the background inside the secure sandbox...",
        "docker_compiling_spinner": "Compiling project inside the safe sandbox... (Press 's' + Enter for status)",
        "docker_failed_status": "Compilation failed with compiler errors.",
        "docker_success_status": "Compilation successful! Safeguarding build artifacts...",
        "docker_abort_status": "Aborted by user. Clearing sandbox environments...",
        "docker_cleanup_status": "Removing temporary container environments...",

        "deps_py": "Resolving Python dependencies",
        "deps_node": "Resolving Node.js packages",
        "deps_go": "Fetching Go modules",
        "deps_rust": "Fetching Rust crates",
        "deps_java": "Resolving Java dependencies",
        "installing": "Installing missing build tool: {tool}",

        "act_scan": "Scanning     ",
        "req_msg": "Version: {req}",
        "act_detected": "Detected    ",
        "act_test": "Testing     ",
        "act_verify": "Verified    ",
        "act_ready": "Ready       ",
        "act_saved": "Saved       ",
        "test_fail_abort": "Tests failed. Aborting build for {name}.",
        "test_success": "All tests passed successfully.",
        "compile_fail": "Compilation failed completely.",
    },
    "nl": {
        "title": "JustCompiler",
        "menu_1": "  1. Lokale workspace compileren",
        "menu_2": "  2. Externe Git repository compileren",
        "menu_3": "  3. Applicatie Afsluiten",
        "choice_prompt": "Voer keuze in [1-3]: ",
        "path_prompt": "Lokaal workspace pad (Leeg laten voor huidige map): ",
        "git_prompt": "Externe Git URL (HTTPS): ",
        "test_prompt": "Tests automatisch uitvoeren tijdens build? (j/n): ",
        
        "docker_version_detected_title": "Gedetecteerde Lokale Sandbox Containeromgevingen:",
        "docker_version_detected_prompt": "Selecteer welke sandbox-versie je wilt gebruiken [1-X, Standaard=1]: ",
        
        "err_dir": "Ongeldig doelpad of map bestaat niet.",
        "cloning": "Repository klonen naar tijdelijke workspace...",
        "clone_fail": "Kan de externe repository niet klonen.",
        "err_docker": "Docker is niet geïnstalleerd of staat niet aan op dit systeem.",
        "err_sudo": "Docker vereist root-privileges op dit systeem.",
        "sudo_prompt": "Voer uw sudo-wachtwoord in indien gevraagd: ",
        "err_auth": "Authenticatie mislukt. Kan Docker sandbox niet starten.",
        "err_files": "Kritieke framework-onderdelen (engine.py/core.py) ontbreken.",
        "err_custom_version_missing": "Gespecificeerde oude Docker-image 'justcompiler-engine:{version}' is lokaal niet gevonden!",

        # Docker Status Berichten
        "docker_cache_check": "Docker cache controleren en actieve omgevingen inspecteren...",
        "docker_clean_old": "Nieuwe scriptversie gedetecteerd! Oude Docker-omgevingen worden opgeruimd...",
        "docker_building_base": "Modern Ubuntu 26.04 LTS Sandbox basis-image opbouwen...",
        "docker_building_spinner": "Modern Ubuntu 26.04 LTS Sandbox-omgeving bouwen...",
        "docker_reusing_old": "Succesvol gekoppeld met oude Docker containerversie: {version}",
        "docker_compiling_status": "Project op de achtergrond aan het compileren binnen de sandbox...",
        "docker_compiling_spinner": "Project aan het compileren in de veilige sandbox... (Druk op 's' + Enter voor status)",
        "docker_failed_status": "Compilatie mislukt met foutmeldingen.",
        "docker_success_status": "Compilatie succesvol! Resultaten worden nu veiliggesteld...",
        "docker_abort_status": "Afgebroken door gebruiker. Sandbox wordt opgeschoond...",
        "docker_cleanup_status": "Tijdelijke containeromgevingen weghalen...",

        "deps_py": "Python afhankelijkheden ophalen",
        "deps_node": "Node.js pakketten installeren",
        "deps_go": "Go modules ophalen",
        "deps_rust": "Rust crates ophalen",
        "deps_java": "Java afhankelijkheden oplossen",
        "installing": "Ontbrekende build-tool installeren: {tool}",

        "act_scan": "Scannen      ",
        "req_msg": "Versie: {req}",
        "act_detected": "Gevonden    ",
        "act_test": "Testen      ",
        "act_verify": "Geverifieerd",
        "act_ready": "Klaar       ",
        "act_saved": "Opgeslagen  ",
        "test_fail_abort": "Tests mislukt. Build afgebroken voor {name}.",
        "test_success": "Alle tests succesvol doorstaan.",
        "compile_fail": "Compilatie volledig mislukt.",
    }
}

def set_lang(lang: str):
    global _CURRENT_LANG
    if lang in _TRANSLATIONS:
        _CURRENT_LANG = lang

def t(key: str, **kwargs) -> str:
    text = _TRANSLATIONS[_CURRENT_LANG].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text

class DependencyManager:
    def __init__(self, auto_install: bool = True):
        self.auto_install = auto_install
        self.in_docker = Path('/.dockerenv').exists()

    def inspect_version(self, root: Path, tool: str) -> str:
        if not shutil.which(tool):
            return "Niet geïnstalleerd" if _CURRENT_LANG == "nl" else "Not Installed"
        try:
            res = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout:
                return res.stdout.split('\n')[0][:35].strip()
        except Exception:
            pass
        return "Versie onbekend" if _CURRENT_LANG == "nl" else "Unknown Version"

    def get_pkg_manager(self) -> str:
        if shutil.which("apt-get"): return "apt"
        if shutil.which("dnf"): return "dnf"
        if shutil.which("brew"): return "brew"
        return "unknown"

    def trigger_install(self, tool: str) -> bool:
        if not self.auto_install:
            return False
            
        pkg_mgr = self.get_pkg_manager()
        pkg = tool
        if tool == "mvn": pkg = "maven"
        elif tool in ["gradlew", "gradle"]: pkg = "gradle"
        elif tool in ["java", "javac"]: pkg = "openjdk-21-jdk"

        if pkg_mgr == "apt" and os.geteuid() == 0:
            with UI.spinner(t("installing", tool=pkg)) as spinner:
                subprocess.run(["apt-get", "update", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                res = subprocess.run(["apt-get", "install", "-y", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0:
                    spinner.fail()
                return res.returncode == 0
            
        return False

    def cleanup(self):
        pass

    def resolve_dependencies(self, target_dir: Path):
        if not self.auto_install:
            return

        target_dir = Path(target_dir)

        if (target_dir / "requirements.txt").exists():
            with UI.spinner(t("deps_py")) as sp:
                res = subprocess.run(["pip3", "install", "-r", "requirements.txt"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0: sp.fail()
        
        if (target_dir / "package.json").exists():
            with UI.spinner(t("deps_node")) as sp:
                res = subprocess.run(["npm", "install"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0: sp.fail()

        if (target_dir / "go.mod").exists():
            with UI.spinner(t("deps_go")) as sp:
                subprocess.run(["go", "mod", "tidy"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                res = subprocess.run(["go", "mod", "download"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0: sp.fail()

        if (target_dir / "Cargo.toml").exists():
            with UI.spinner(t("deps_rust")) as sp:
                res = subprocess.run(["cargo", "fetch"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0: sp.fail()

        if (target_dir / "pom.xml").exists():
            with UI.spinner(t("deps_java")) as sp:
                wrapper = "mvnw" if os.name == "nt" else "./mvnw"
                cmd = wrapper if (target_dir / "mvnw").exists() else "mvn"
                res = subprocess.run([cmd, "dependency:resolve"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0: sp.fail()
            
        if (target_dir / "build.gradle").exists() or (target_dir / "build.gradle.kts").exists():
            with UI.spinner(t("deps_java")) as sp:
                wrapper = "gradlew.bat" if os.name == "nt" else "./gradlew"
                cmd = wrapper if (target_dir / "gradlew").exists() else "gradle"
                res = subprocess.run([cmd, "build", "-x", "test"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode != 0: sp.fail()
