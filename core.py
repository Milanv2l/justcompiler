import os
import sys
import subprocess
import shutil
import threading
import time
from pathlib import Path

_CURRENT_LANG = "en"

class _SpinnerContext:
    """Asynchronous context manager for a smooth terminal loading spinner."""
    def __init__(self, text: str):
        self.text = text
        self.is_spinning = False
        self.thread = None
        self.success = True
        # Moderne braille-spinner animatie
        self.spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def spin(self):
        i = 0
        while self.is_spinning:
            # \033[K cleart de rest van de terminal-lijn voor een strakke look
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
        """Mark the process as failed to change the exit icon."""
        self.success = False

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.is_spinning = False
        if self.thread:
            self.thread.join()
        
        sys.stdout.write('\r\033[K') # Wis de spinner lijn
        sys.stdout.flush()
        
        # Laat een blijvend succes/faal bericht achter
        if exc_type is not None or not self.success:
            UI.error(self.text)
        else:
            UI.success(self.text)


class UI:
    """ANSI color escape sequences and modernized logging methods for the CLI."""
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # --- OUDE LOG METHODE (nodig voor engine.py backwards compatibility) ---
    @classmethod
    def log(cls, color, prefix, msg):
        print(f"{color}{prefix}{cls.RESET} {msg}")

    # --- MODERNE LOG METHODES ---
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
        """Starts a non-blocking UI spinner. Use as a 'with' context manager."""
        return _SpinnerContext(msg)

# Professional, clean CLI terminology
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
        "runtime_prompt": "Execute in Docker Sandbox? (Otherwise Bare-Metal) (y/n): ",
        
        "env_title": "Select Execution Environment:",
        "env_1": "  1. Docker Sandbox (Isolated & Safe)",
        "env_2": "  2. Bare-Metal (Host System)",
        "env_choice": "Environment choice [1-2]: ",

        "err_dir": "Invalid target path or directory does not exist.",
        "cloning": "Cloning repository into temporary workspace...",
        "clone_fail": "Failed to clone remote repository.",
        
        # Spinner Messages
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
        "test_success": "Tests passed successfully.",
        "compile_fail": "Compilation failed completely.",
        "fallback_msg": "Attempting fallback strategy...",
        "err_output_title": "Error Output:",
        "report_header": "=== BUILD REPORT ===",
        "report_status": "{green}✓ Succeeded: {success}{reset} | {red}✖ Failed: {failed}{reset} | {yellow}⚠ Skipped: {skipped}{reset} | {time}s",
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
        "runtime_prompt": "Uitvoeren in Docker Sandbox? (Anders Bare-Metal) (j/n): ",
        
        "env_title": "Selecteer Uitvoeringsomgeving:",
        "env_1": "  1. Docker Sandbox (Geïsoleerd & Veilig)",
        "env_2": "  2. Bare-Metal (Host Systeem)",
        "env_choice": "Omgevingskeuze [1-2]: ",

        "err_dir": "Ongeldig doelpad of map bestaat niet.",
        "cloning": "Repository klonen naar tijdelijke workspace...",
        "clone_fail": "Kan de externe repository niet klonen.",
        
        # Spinner Messages
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
    """Handles automatic dependency resolution natively with clean UI spinners."""
    
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
        """Silently resolves dependencies using the animated UI spinner."""
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
                if res.returncode != 0: sp.fail()import os
import sys
import subprocess
import shutil
from pathlib import Path

_CURRENT_LANG = "en"

class UI:
    """ANSI color escape sequences and standardized logging methods for the CLI."""
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    BLUE = "\033[34m"
    RESET = "\033[0m"
    HEADER = "\033[95m"
    BOLD = "\033[1m"

    # --- OUDE LOG METHODE (nodig voor engine.py) ---
    @classmethod
    def log(cls, color, prefix, msg):
        print(f"{color}{prefix}{cls.RESET} {msg}")

    # --- NIEUWE LOG METHODES (nodig voor justcompiler.py) ---
    @staticmethod
    def info(msg: str):
        print(f"[INFO] {msg}")

    @staticmethod
    def success(msg: str):
        print(f"{UI.GREEN}[OK] {msg}{UI.RESET}")

    @staticmethod
    def warn(msg: str):
        print(f"{UI.YELLOW}[WARN] {msg}{UI.RESET}")

    @staticmethod
    def error(msg: str):
        print(f"{UI.RED}[ERROR] {msg}{UI.RESET}")

# Professional, clean CLI terminology
_TRANSLATIONS = {
    "en": {
        "title": "JustCompiler CLI",
        "menu_1": "  1. Compile local workspace",
        "menu_2": "  2. Compile remote Git repository",
        "menu_3": "  3. Exit Application",
        "choice_prompt": "Enter choice [1-3]: ",
        "path_prompt": "Local workspace path (Leave empty for current directory): ",
        "git_prompt": "Remote Git URL (HTTPS): ",
        "test_prompt": "Run tests automatically during build? (y/n): ",
        "runtime_prompt": "Execute in Docker Sandbox? (Otherwise Bare-Metal) (y/n): ",
        "err_dir": "Invalid target path or directory does not exist.",
        "cloning": "Cloning repository into temporary workspace...",
        "clone_fail": "Failed to clone remote repository.",
        
        # Core Engine / UI Text strings used by engine.py
        "act_scan": "Scanning     ",
        "req_msg": "Version: {req}",
        "act_detected": "Detected    ",
        "act_test": "Testing     ",
        "act_verify": "Verified    ",
        "act_ready": "Ready       ",
        "act_saved": "Saved       ",
        "test_fail_abort": "Tests failed. Aborting build for {name}.",
        "test_success": "Tests passed successfully.",
        "compile_fail": "Compilation failed completely.",
        "fallback_msg": "Attempting fallback strategy...",
        "err_output_title": "Error Output:",
        "report_header": "=== BUILD REPORT ===",
        "report_status": "{green}{success} Succeeded{reset} | {red}{failed} Failed{reset} | {yellow}{skipped} Skipped{reset} | {time}s",
    },
    "nl": {
        "title": "JustCompiler CLI",
        "menu_1": "  1. Lokale workspace compileren",
        "menu_2": "  2. Externe Git repository compileren",
        "menu_3": "  3. Applicatie Afsluiten",
        "choice_prompt": "Voer keuze in [1-3]: ",
        "path_prompt": "Lokaal workspace pad (Leeg laten voor huidige map): ",
        "git_prompt": "Externe Git URL (HTTPS): ",
        "test_prompt": "Tests automatisch uitvoeren tijdens build? (j/n): ",
        "runtime_prompt": "Uitvoeren in Docker Sandbox? (Anders Bare-Metal) (j/n): ",
        "err_dir": "Ongeldig doelpad of map bestaat niet.",
        "cloning": "Repository klonen naar tijdelijke workspace...",
        "clone_fail": "Kan de externe repository niet klonen.",
        
        # Core Engine / UI Text strings used by engine.py
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
        "fallback_msg": "Terugvallen op alternatieve strategie...",
        "err_output_title": "Foutmelding(en):",
        "report_header": "=== BUILD RAPPORT ===",
        "report_status": "{green}{success} Voltooid{reset} | {red}{failed} Mislukt{reset} | {yellow}{skipped} Overgeslagen{reset} | {time}s",
    }
}

def set_lang(lang: str):
    """Sets the global language context for the CLI."""
    global _CURRENT_LANG
    if lang in _TRANSLATIONS:
        _CURRENT_LANG = lang

def t(key: str, **kwargs) -> str:
    """Retrieves the localized string for a given key, supporting format kwargs."""
    text = _TRANSLATIONS[_CURRENT_LANG].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    return text


class DependencyManager:
    """Handles automatic dependency resolution for various languages before compilation."""
    
    def __init__(self, auto_install: bool = True):
        self.auto_install = auto_install
        # Check om te zien of we binnen een Docker sandbox draaien
        self.in_docker = Path('/.dockerenv').exists()

    def inspect_version(self, root: Path, tool: str) -> str:
        """Checks the CLI version of a specific compiler/tool."""
        if not shutil.which(tool):
            return "Not Installed"
        try:
            result = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout:
                # Retoneer alleen de eerste schone zin voor de logs
                return result.stdout.split('\n')[0][:35].strip()
        except Exception:
            pass
        return "Unknown Version"

    def get_pkg_manager(self) -> str:
        """Determines which package manager is available on the current platform."""
        if shutil.which("apt-get"): return "apt"
        if shutil.which("dnf"): return "dnf"
        if shutil.which("brew"): return "brew"
        return "unknown"

    def trigger_install(self, tool: str) -> bool:
        """Automatically installs missing system tools if auto_install is authorized."""
        if not self.auto_install:
            return False
            
        UI.warn(f"Attempting automatic installation for '{tool}'...")
        pkg_mgr = self.get_pkg_manager()
        
        # Package mapping voor bekende CLI-omgevingen (zoals Ubuntu 24.04 Docker)
        pkg = tool
        if tool == "mvn": pkg = "maven"
        elif tool in ["gradlew", "gradle"]: pkg = "gradle"
        elif tool in ["java", "javac"]: pkg = "openjdk-21-jdk"

        if pkg_mgr == "apt" and os.geteuid() == 0:
            subprocess.run(["apt-get", "update", "-y"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            res = subprocess.run(["apt-get", "install", "-y", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return res.returncode == 0
            
        return False

    def cleanup(self):
        """Post-build cleanup actions."""
        # Tijdelijke placeholder voor post-build taken (bijv. cache legen)
        pass

    def resolve_dependencies(self, target_dir: Path):
        """Resolves project dependencies locally for supported package managers."""
        if not self.auto_install:
            return

        target_dir = Path(target_dir)

        # 1. Python
        if (target_dir / "requirements.txt").exists():
            UI.info("Resolving Python dependencies via pip...")
            subprocess.run(
                ["pip3", "install", "-r", "requirements.txt"], 
                cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        
        # 2. Node.js
        if (target_dir / "package.json").exists():
            UI.info("Resolving Node.js dependencies via npm...")
            subprocess.run(["npm", "install"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 3. Go
        if (target_dir / "go.mod").exists():
            UI.info("Resolving Go dependencies...")
            subprocess.run(["go", "mod", "tidy"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["go", "mod", "download"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 4. Rust
        if (target_dir / "Cargo.toml").exists():
            UI.info("Fetching Rust dependencies via Cargo...")
            subprocess.run(["cargo", "fetch"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 5. Java (Maven / Gradle)
        if (target_dir / "pom.xml").exists():
            UI.info("Resolving Java (Maven) dependencies...")
            wrapper = "mvnw" if os.name == "nt" else "./mvnw"
            cmd = wrapper if (target_dir / "mvnw").exists() else "mvn"
            subprocess.run([cmd, "dependency:resolve"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        if (target_dir / "build.gradle").exists() or (target_dir / "build.gradle.kts").exists():
            UI.info("Resolving Java (Gradle) dependencies...")
            wrapper = "gradlew.bat" if os.name == "nt" else "./gradlew"
            cmd = wrapper if (target_dir / "gradlew").exists() else "gradle"
            subprocess.run([cmd, "build", "-x", "test"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
