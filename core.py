import os
import sys
import subprocess
import shutil
import threading
import time
import re
from pathlib import Path

_CURRENT_LANG = "en"

class _SpinnerContext:
    """Asynchronous context manager for a smooth terminal loading spinner."""
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
    """ANSI color escape sequences and modernized box-drawing layout methods for the TUI."""
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
        print(f"{UI.CYAN}❯{UI.RESET} {msg}")

    @staticmethod
    def success(msg: str):
        print(f"{UI.GREEN}✔{UI.RESET} {msg}")

    @staticmethod
    def warn(msg: str):
        print(f"{UI.YELLOW}⚡{UI.RESET} {msg}")

    @staticmethod
    def error(msg: str):
        print(f"{UI.RED}✖{UI.RESET} {msg}")

    @staticmethod
    def header(title: str):
        print(f"\n{UI.BOLD}{UI.MAGENTA}── {title} ──{UI.RESET}")

    @staticmethod
    def spinner(msg: str) -> _SpinnerContext:
        return _SpinnerContext(msg)

    @staticmethod
    def clear():
        """Smoothly clears the terminal screen."""
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()

    @staticmethod
    def draw_panel(title: str, lines: list, width: int = 75, color: str = CYAN):
        """Draws a clean, modern TUI panel with Unicode borders and padding."""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        title_text = f" {UI.BOLD}{title}{UI.RESET}{color} "
        plain_title = ansi_escape.sub('', title_text)
        top_line = f"{color}┌─{title_text}" + "─" * (width - len(plain_title) - 4) + f"┐{UI.RESET}"
        print(top_line)
        
        for line in lines:
            plain_line = ansi_escape.sub('', line)
            padding = width - len(plain_line) - 4
            if padding < 0: 
                padding = 0
            print(f"{color}│{UI.RESET}  {line}" + " " * padding + f" {color}│{UI.RESET}")
            
        print(f"{color}└" + "─" * (width - 2) + f"┘{UI.RESET}")


_TRANSLATIONS = {
    "en": {
        "title": "JustCompiler Engine Dashboard",
        "menu_1": "1. Compile local workspace",
        "menu_2": "2. Compile remote Git repository",
        "menu_3": "3. Exit Application",
        "choice_prompt": "Select an option [1-3]: ",
        "path_prompt": "Workspace path (Leave empty for current dir): ",
        "git_prompt": "Remote Git URL (HTTPS) [use url#branch]: ",
        "test_prompt": "Run automated test suites? (y/n): ",
        "runtime_prompt": "Select execution sandbox: ",
        
        "env_title": "Execution Environments",
        "env_1": "1. Docker Sandbox (Isolated & Safe)",
        "env_2": "2. Bare-Metal (Host Native System)",
        "env_choice": "Select environment [1-2]: ",

        "err_dir": "Invalid target path or directory does not exist.",
        "cloning": "Cloning repository into temporary workspace...",
        "clone_fail": "Failed to clone remote repository.",
        "err_docker": "Docker is not installed or running. Sandbox unavailable.",
        "err_files": "Required engine files are missing.",
        "err_auth": "Authentication failed or permissions denied.",
        "err_sudo": "Docker requires elevated root privileges.",
        "sudo_prompt": "Please verify sudo privileges... ",
        "act_ready": "Sandbox environment is verified and ready.",
        
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
        "act_ready_lbl": "Ready       ",
        "act_saved": "Saved       ",
        "test_fail_abort": "Tests failed. Aborting build for {name}.",
        "test_success": "Tests passed successfully.",
        "compile_fail": "Compilation failed completely.",
        "fallback_msg": "Attempting fallback strategy...",
        "err_output_title": "Error Output:",
        "report_header": "=== BUILD REPORT ===",
        "report_status": "{green}✓ Succeeded: {success}{reset} | {red}✖ Failed: {failed}{reset} | {yellow}⚠ Skipped: {skipped}{reset} | {time}s",
    },
    "nl": {
        "title": "JustCompiler Engine Dashboard",
        "menu_1": "1. Lokale workspace compileren",
        "menu_2": "2. Externe Git repository compileren",
        "menu_3": "3. Applicatie Afsluiten",
        "choice_prompt": "Selecteer een optie [1-3]: ",
        "path_prompt": "Workspace pad (Leeg laten voor huidige map): ",
        "git_prompt": "Externe Git URL (HTTPS) [gebruik url#branch]: ",
        "test_prompt": "Automatische testsuites uitvoeren? (j/n): ",
        "runtime_prompt": "Selecteer sandbox-omgeving: ",
        
        "env_title": "Uitvoeringsomgevingen",
        "env_1": "1. Docker Sandbox (Geïsoleerd & Veilig)",
        "env_2": "2. Bare-Metal (Host Systeem)",
        "env_choice": "Selecteer omgeving [1-2]: ",

        "err_dir": "Ongeldig doelpad of map bestaat niet.",
        "cloning": "Repository klonen naar tijdelijke workspace...",
        "clone_fail": "Kan de externe repository niet klonen.",
        "err_docker": "Docker is niet geïnstalleerd of actief. Sandbox onbeschikbaar.",
        "err_files": "Vereiste enginebestanden ontbreken.",
        "err_auth": "Authenticatie mislukt of rechten geweigerd.",
        "err_sudo": "Docker vereist verhoogde administratorrechten (sudo).",
        "sudo_prompt": "Verifieer a.u.b. sudo-toegang... ",
        "act_ready": "Sandbox-omgeving is geverifieerd en klaar.",
        
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
        "act_ready_lbl": "Klaar       ",
        "act_saved": "Opgeslagen  ",
        "test_fail_abort": "Tests mislukt. Build afgebroken voor {name}.",
        "test_success": "Alle tests succesvol doorstaan.",
        "compile_fail": "Compilatie volledig mislukt.",
        "fallback_msg": "Terugvallen op alternatieve strategie...",
        "err_output_title": "Foutmelding(en):",
        "report_header": "=== BUILD RAPPORT ===",
        "report_status": "{green}✓ Voltooid: {success}{reset} | {red}✖ Mislukt: {failed}{reset} | {yellow}⚠ Overgeslagen: {skipped}{reset} | {time}s",
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
                return res.stdout.splitlines()[0][:35].strip()
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
