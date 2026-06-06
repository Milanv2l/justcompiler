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

# Professional, clean CLI terminology (No emojis, no hype)
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
