import os
import sys
import subprocess
from pathlib import Path

_CURRENT_LANG = "en"

class UI:
    """ANSI color escape sequences and standardized logging methods for the CLI."""
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"

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
        "menu_3": "  3. Exit",
        "choice_prompt": "Select an option [1-3]: ",
        "path_prompt": "Enter project path (Leave empty for current directory): ",
        "git_prompt": "Enter Git repository URL: ",
        "test_prompt": "Run unit tests if available? (y/n): ",
        "env_title": "Select Execution Environment:",
        "env_1": "  1. Docker Sandbox (Recommended / Isolated)",
        "env_2": "  2. Local Host (Bare-metal / Requires local tools)",
        "env_choice": "Select environment [1-2]: ",
        "err_docker": "Docker is not installed or not running.",
        "err_sudo": "Docker requires elevated privileges on this system.",
        "sudo_prompt": "Please authenticate for sudo: ",
        "err_auth": "Sudo authentication failed.",
        "err_files": "Required core components are missing in the installation directory.",
        "sandbox_prep": "Preparing Docker sandbox environment...",
        "sandbox_ready": "Sandbox environment initialized successfully.",
        "docker_start": "Starting Docker container...",
        "git_clone": "Cloning remote repository...",
        "git_fail": "Failed to clone the specified repository.",
        "err_dir": "The target directory does not exist.",
    },
    "nl": {
        "title": "JustCompiler CLI",
        "menu_1": "  1. Lokale workspace compileren",
        "menu_2": "  2. Externe Git repository compileren",
        "menu_3": "  3. Afsluiten",
        "choice_prompt": "Selecteer een optie [1-3]: ",
        "path_prompt": "Voer projectpad in (Leeg laten voor huidige map): ",
        "git_prompt": "Voer Git repository URL in: ",
        "test_prompt": "Unit tests uitvoeren indien beschikbaar? (j/n): ",
        "env_title": "Selecteer Executie-omgeving:",
        "env_1": "  1. Docker Sandbox (Aanbevolen / Geïsoleerd)",
        "env_2": "  2. Lokale Host (Bare-metal / Vereist lokale tools)",
        "env_choice": "Selecteer omgeving [1-2]: ",
        "err_docker": "Docker is niet geïnstalleerd of is niet actief.",
        "err_sudo": "Docker vereist verhoogde rechten op dit systeem.",
        "sudo_prompt": "Verifieer uw identiteit voor sudo: ",
        "err_auth": "Sudo-authenticatie mislukt.",
        "err_files": "Vereiste kernbestanden ontbreken in de installatiemap.",
        "sandbox_prep": "Docker sandbox-omgeving voorbereiden...",
        "sandbox_ready": "Sandbox-omgeving succesvol geïnitialiseerd.",
        "docker_start": "Docker container wordt gestart...",
        "git_clone": "Externe repository klonen...",
        "git_fail": "Klonen van de opgegeven repository mislukt.",
        "err_dir": "De doelmap bestaat niet.",
    }
}

def set_lang(lang: str):
    """Sets the global language for the interface."""
    global _CURRENT_LANG
    if lang in _TRANSLATIONS:
        _CURRENT_LANG = lang

def t(key: str) -> str:
    """Retrieves the localized string for a given key."""
    return _TRANSLATIONS[_CURRENT_LANG].get(key, key)


class DependencyManager:
    """Handles automatic dependency resolution for various languages before compilation."""
    
    @staticmethod
    def resolve_dependencies(target_dir: Path):
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
            if (target_dir / "yarn.lock").exists():
                UI.info("Resolving Node.js dependencies via yarn...")
                subprocess.run(["yarn", "install"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif (target_dir / "pnpm-lock.yaml").exists():
                UI.info("Resolving Node.js dependencies via pnpm...")
                subprocess.run(["pnpm", "install"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
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
            subprocess.run([cmd, "dependencies"], cwd=target_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
