import os
import sys
import subprocess
import shutil
import time
import json
import argparse
import urllib.request
from pathlib import Path

class Theme:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

    @classmethod
    def log(cls, color, action, target): print(f"{color}{cls.BOLD}{action:<12}{cls.RESET} {target}")

class Engine:
    def __init__(self, src_root: Path, out_root: Path, plugin_url: str = None):
        self.src_root = src_root
        self.out_root = out_root
        self.log_file = out_root / "build_log.txt"
        self.manifest_file = out_root / "build_manifest.json"
        self.stats = {"success": 0, "failed": 0, "skipped": 0}
        self.manifest_data = {"build_time_utc": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), "projects": []}

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(f"=== UNIVERSAL ENGINE LOG: {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")

        subprocess.run("git config --global --add safe.directory '*'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.plugins = self.load_plugins(plugin_url)

    def load_plugins(self, url):
        """Laadt plugins lokaal, of via een server als URL is opgegeven."""
        try:
            if url:
                Theme.log(Theme.BLUE, "Network", f"Plugins ophalen van {url}...")
                with urllib.request.urlopen(url) as response:
                    return json.loads(response.read().decode())
            else:
                # Laad lokaal in de Docker container
                with open("/workspace/plugins.json", "r") as f:
                    return json.load(f)
        except Exception as e:
            Theme.log(Theme.RED, "Fatal", f"Kon plugins niet laden: {e}")
            sys.exit(1)

    def run_command(self, cmd, cwd):
        env = os.environ.copy()
        with open(self.log_file, "a", encoding="utf-8") as log_output:
            log_output.write(f"\n--- RUNNING: {cmd} in {cwd} ---\n")
            proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=log_output, stderr=log_output, text=True, env=env)
            proc.wait()
        return proc.returncode == 0

    def generate_manifest(self):
        """Nieuwe Feature: SBOM / Manifest Generator"""
        with open(self.manifest_file, "w", encoding="utf-8") as f:
            json.dump(self.manifest_data, f, indent=4)
        Theme.log(Theme.CYAN, "Manifest", f"Build manifest gegenereerd -> {self.manifest_file.name}")

    def harvest(self, name, project_root, plugin):
        harvested_items = []
        search_paths = [project_root] + list(project_root.parents)[:2]
        ignored = ["zipinfo", "md5ref", "depends.txt", "pack.txt", ".ds_store", "makefile", "cmakecache.txt"]

        for out_dir in plugin["out_dirs"]:
            for base_path in search_paths:
                target = base_path / out_dir
                if not target.exists(): continue

                if "*DIR*" in plugin["out_exts"]:
                    dest = self.out_root / f"{name}_{out_dir.replace('/', '_')}"
                    if dest.exists(): shutil.rmtree(dest)
                    shutil.copytree(target, dest, ignore=shutil.ignore_patterns('.*', 'node_modules', 'venv', '__pycache__'))
                    harvested_items.append({"type": "directory", "name": dest.name})
                    Theme.log(Theme.GREEN, "Artifact", f"Map -> {dest.name}")
                else:
                    for file in target.rglob('*'):
                        if file.is_file():
                            is_binary = not file.suffix and os.access(file, os.X_OK)
                            if (file.suffix in plugin["out_exts"] or is_binary):
                                if file.name.lower() in ignored or "node_modules" in file.parts or ".cmake" in file.name:
                                    continue

                                dest_file = self.out_root / f"{name}_{file.name}"
                                shutil.copy2(file, dest_file)
                                harvested_items.append({"type": "file", "name": dest_file.name})
                                Theme.log(Theme.GREEN, "Artifact", f"Bestand -> {dest_file.name}")
        return harvested_items

    def resolve_dynamic_cmd(self, root, plugin, attempt):
        cmd = plugin.get("cmd_system", "")
        tool = plugin["tool"]

        if cmd == "DYNAMIC_JS_RESOLUTION":
            install = f"pnpm install {'--dangerously-allow-all-builds' if attempt == 1 else '--no-frozen-lockfile'}" if tool == "pnpm" else f"npm install --legacy-peer-deps"
            return f"{install} && npm run build" if (root / "package.json").exists() else install

        elif cmd == "DYNAMIC_GO_RESOLUTION":
            return "go run build.go build" if (root / "build.go").exists() else "go build -o build_output/ ./..."

        elif cmd == "DYNAMIC_CPP_RESOLUTION":
            if (root / "CMakeLists.txt").exists(): return "mkdir -p build && cd build && cmake .. && make -j$(nproc)"
            pro_files = list(root.glob("*.pro"))
            if pro_files: return f"qmake6 {pro_files[0].name} && make -j$(nproc)"
            return "make -j$(nproc)"

        elif cmd == "DYNAMIC_PYTHON_RESOLUTION":
            # Installeer requirements en bouw executables met pyinstaller als mogelijk
            req = "pip install -r requirements.txt" if (root / "requirements.txt").exists() else "echo 'No requirements'"
            main_file = next((f for f in root.glob("*.py") if f.name in ["main.py", "app.py"]), None)
            if main_file: return f"{req} && pip install pyinstaller && pyinstaller --onefile {main_file.name}"
            return req

        return cmd

    def process(self, root: Path, files, plugin):
        name = root.name if root.name != "src" else "Root-Workspace"
        Theme.log(Theme.BLUE, "Detected", f"{name} [{plugin['name']}]")

        t0 = time.time()
        for attempt in range(1, 4):
            cmd = self.resolve_dynamic_cmd(root, plugin, attempt)

            Theme.log(Theme.CYAN, "Building", f"Strategie {attempt}/3...")
            if self.run_command(cmd, root):
                duration = round(time.time() - t0, 1)
                Theme.log(Theme.GREEN, "Compiled", f"{name} in {duration}s")

                # Oogst bestanden en voeg toe aan manifest
                artifacts = self.harvest(name, root, plugin)
                if artifacts:
                    self.manifest_data["projects"].append({
                        "project_name": name,
                        "language": plugin["name"],
                        "build_duration_seconds": duration,
                        "artifacts": artifacts
                    })
                    self.stats["success"] += 1
                    return
            Theme.log(Theme.YELLOW, "Retry", "Aanpak mislukt, fallback activeren...")

        Theme.log(Theme.RED, "Failed", f"Compilatie mislukt voor {name}.")
        self.stats["failed"] += 1

    def execute(self):
        t_start = time.time()
        Theme.log(Theme.BLUE, "Scanning", f"{self.src_root.resolve()}")

        for root, dirs, files in os.walk(str(self.src_root)):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ["node_modules", "target", "build", "dist", "bin", "venv", "__pycache__"]]

            for plugin in self.plugins:
                # Check of files of extensies overeenkomen met detectie regels
                if any(f in files for f in plugin["detect"]) or any(any(f.endswith(d.replace('*', '')) for f in files) for d in plugin["detect"] if '*' in d):
                    self.process(Path(root), files, plugin)
                    dirs[:] = []
                    break

        self.generate_manifest()

        total_time = round(time.time() - t_start, 1)
        print(f"\n{Theme.HEADER}{Theme.BOLD}=== RAPPORTAGE ==={Theme.RESET}")
        print(f" {Theme.GREEN}{self.stats['success']} Geslaagd{Theme.RESET} | {Theme.RED}{self.stats['failed']} Mislukt{Theme.RESET} | {Theme.YELLOW}{self.stats['skipped']} Overgeslagen{Theme.RESET} ({total_time}s)")
        print(f"{Theme.HEADER}{Theme.BOLD}=================={Theme.RESET}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--url", help="Optionele URL om plugins.json op te halen", default=None)
    args = parser.parse_args()

    Engine(Path(args.src), Path(args.out), args.url).execute()
