import os
import sys
import subprocess
import shutil
import argparse
import platform
from pathlib import Path

class UI:
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'

    @classmethod
    def info(cls, msg): print(f"{cls.CYAN}❯ {msg}{cls.RESET}")
    @classmethod
    def success(cls, msg): print(f"{cls.GREEN}✔ {msg}{cls.RESET}")
    @classmethod
    def warn(cls, msg): print(f"{cls.YELLOW}⚡ {msg}{cls.RESET}")
    @classmethod
    def error(cls, msg): print(f"{cls.RED}✖ {msg}{cls.RESET}")

def bootstrap_sandbox(target_path: Path, artifacts_path: Path):
    """Bouwt de minimale container en start de engine binnen de sandbox."""
    if not shutil.which("docker"):
        UI.error("Docker is niet geïnstalleerd op deze host. Schakel over naar Bare-Metal of installeer Docker CE/Desktop.")
        return

    docker_cmd = ["docker"]
    is_windows = platform.system() == "Windows"

    # Cross-platform rechten check
    if not is_windows:
        if subprocess.run(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            UI.warn("Docker daemon vereist beheerdersrechten (sudo).")
            print(f"{UI.YELLOW}🔐 Voer je systeemwachtwoord in (typen is onzichtbaar): {UI.RESET}")
            # Cache het sudo wachtwoord veilig via het OS
            if subprocess.run(["sudo", "-v"]).returncode == 0:
                docker_cmd = ["sudo", "docker"]
            else:
                UI.error("Authenticatie mislukt. Val terug op Bare-Metal.")
                return

    host_dir = Path(__file__).resolve().parent
    engine_script = host_dir / "engine.py"

    if not engine_script.exists():
        UI.error("Critische fout: 'engine.py' niet gevonden in dezelfde map!")
        sys.exit(1)

# Volledig uitgeruste Universele Dockerfile
    dockerfile_content = """FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
# Base, C++, Java, Go, Rust, Python, C# (.NET), PHP, Ruby
RUN apt-get update && apt-get install -y \
    curl git python3 python3-pip python3-venv build-essential g++ cmake qt6-base-dev qt6-tools-dev-tools \
    openjdk-21-jdk maven gradle golang cargo \
    dotnet-sdk-8.0 php-cli composer ruby-full \
    && rm -rf /var/lib/apt/lists/*
# Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs && npm install -g pnpm yarn && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY engine.py /workspace/engine.py
COPY plugins.json /workspace/plugins.json
ENTRYPOINT ["python3", "/workspace/engine.py", "--src", "/workspace/src", "--out", "/workspace/artifacts"]
"""

    dockerfile_path = host_dir / "Dockerfile"
    with open(dockerfile_path, "w", encoding="utf-8") as f:
        f.write(dockerfile_content)

    UI.info("Sandbox controleren en compiler-lagen verzegelen...")
    build_process = subprocess.run(docker_cmd + ["build", "-t", "autobuilder-engine", str(host_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if dockerfile_path.exists():
        dockerfile_path.unlink()

    if build_process.returncode != 0:
        UI.error("Bouw van container-infrastructuur mislukt.")
        return

    UI.success("Sandbox gereed. Engine wordt virtueel gestart...\n")

    # Pad conversie voor Docker mounts (werkt op Linux, Mac en Windows)
    src_mount = str(target_path.resolve())
    out_mount = str(artifacts_path.resolve())

    run_cmd = docker_cmd + [
        "run", "-it", "--rm",
        "-v", f"{src_mount}:/workspace/src",
        "-v", f"{out_mount}:/workspace/artifacts",
        "autobuilder-engine"
    ]

    try:
        subprocess.run(run_cmd)
    except KeyboardInterrupt:
        UI.warn("Sandbox handmatig afgebroken.")

def handle_remote_git(url: str) -> Path:
    """Clonet een remote repository veilig op de host alvorens deze te mounten."""
    UI.info("Repository downloaden naar host cache...")
    branch = None
    if "#" in url:
        url, branch = url.split("#", 1)
        branch = branch.strip()

    cache_dir = Path("./_git_cache") / url.split("/")[-1].replace(".git", "")

    if cache_dir.exists():
        try:
            shutil.rmtree(cache_dir)
        except PermissionError:
            UI.warn("Rechtenrestrictie gedetecteerd (veroorzaakt door eerdere Docker-run).")
            if platform.system() != "Windows":
                print(f"{UI.YELLOW}🔐 Authenticatie nodig om Docker-cache op te schonen...{UI.RESET}")
                subprocess.run(f"sudo rm -rf {cache_dir}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                UI.error(f"Verwijder de map '{cache_dir}' handmatig en probeer opnieuw.")
                sys.exit(1)

    clone_cmd = f"git clone -b {branch} {url} {cache_dir}" if branch else f"git clone {url} {cache_dir}"
    result = subprocess.run(clone_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if result.returncode != 0:
        UI.error("Downloaden van remote Git repository mislukt.")
        sys.exit(1)

    return cache_dir

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoBuilder Pro - Host Orchestrator")
    parser.add_argument("--local-runtime", action="store_true", help="Sla Docker over en draai direct op bare-metal.")
    args = parser.parse_args()

    artifacts_folder = Path("./BUILD_ARTIFACTS")
    artifacts_folder.mkdir(exist_ok=True)

    print(f"{UI.CYAN}=== AUTOBUILDER PRO V15.5 ==={UI.RESET}")
    print(" [1] Bouw lokale projectmap")
    print(" [2] Download & bouw remote Git URL")
    print(" [3] Afsluiten")

    choice = input(f"\n{UI.YELLOW}Maak een keuze (1-3): {UI.RESET}").strip()

    target_workspace = None
    if choice == "1":
        path_input = input(f"{UI.YELLOW}Pad naar project (Enter voor huidige map): {UI.RESET}").strip()
        target_workspace = Path(path_input) if path_input else Path(".")
    elif choice == "2":
        git_url = input(f"{UI.YELLOW}Plak Git URL (gebruik url#branch voor specifieke branch): {UI.RESET}").strip()
        if git_url:
            target_workspace = handle_remote_git(git_url)
    else:
        UI.info("Engine runtime afgesloten.")
        sys.exit(0)

    if not target_workspace or not target_workspace.exists():
        UI.error("Geselecteerde werkruimte bestaat niet.")
        sys.exit(1)

    print(f"\n{UI.CYAN}=== RUNTIME SELECTION ==={UI.RESET}")
    print(" [1] Beveiligde Container Sandbox (Aanbevolen)")
    print(" [2] Direct op Host machine (Bare-Metal)")
    env_choice = input(f"{UI.YELLOW}Selecteer omgeving [1-2, standaard=1]: {UI.RESET}").strip()

    if env_choice == "2" or args.local_runtime:
        UI.warn("Bare-metal geselecteerd. Engine wordt lokaal uitgevoerd...")
        engine_path = Path(__file__).resolve().parent / "engine.py"
        os.system(f"python3 {engine_path} --src {target_workspace.resolve()} --out {artifacts_folder.resolve()}")
    else:
        bootstrap_sandbox(target_workspace, artifacts_folder)
