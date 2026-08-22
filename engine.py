import os
import sys
import subprocess
import shutil
import time
import json
import argparse
import urllib.request
import re
from pathlib import Path
import core
from core import UI, t, DependencyManager

class Engine:
    def __init__(self, src_root: Path, out_root: Path, test_mode: bool, auto_install: bool = False, plugin_url: str = None, project_name: str = ""):
        self.src_root = src_root.resolve()
        self.out_root = out_root.resolve()
        self.test_mode = test_mode
        self.project_name = project_name
        self.log_file = out_root / "build_log.txt"
        self.manifest_file = out_root / "build_manifest.json"
        self.stats = {"success": 0, "failed": 0, "skipped": 0}
        self.manifest_data = {"build_time_utc": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), "projects": []}
        self.dep_mgr = DependencyManager(auto_install=auto_install)
        self._go_noassets = False

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(f"=== UNIVERSAL ENGINE LOG ===\n\n")

        subprocess.run("git config --global --add safe.directory '*'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.plugins = self.load_plugins(plugin_url)

    def load_plugins(self, url: str | None) -> list:
        try:
            if url:
                with urllib.request.urlopen(url) as response: return json.loads(response.read().decode())
            paths = [Path("/workspace/plugins.json"), Path(__file__).resolve().parent / "plugins.json"]
            for p in paths:
                if p.exists(): return json.loads(p.read_text(encoding="utf-8"))
            raise FileNotFoundError("plugins.json missing!")
        except Exception as e:
            UI.error(f"Plugins error: {e}")
            sys.exit(1)

    def check_ready(self, plugin: dict, root: Path) -> bool:
        if self.dep_mgr.in_docker: return True
        tool = plugin["tool"]
        has_wrapper = "wrapper" in plugin and self.find_wrapper(root, plugin["wrapper"])

        if tool in ["gradle", "mvn"]:
            if not shutil.which("java") or not shutil.which("javac"): return self.dep_mgr.trigger_install("java")
            if self.dep_mgr.get_pkg_manager() == "dnf" and "25" in self.dep_mgr.inspect_version(root, tool):
                if not Path("/usr/lib/jvm/java-latest-openjdk").exists(): return self.dep_mgr.trigger_install("java")
            return True
        if not has_wrapper and not shutil.which(tool):
            if tool == "npm" and shutil.which("node"): return True
            return self.dep_mgr.trigger_install(tool)
        return True

    def run_cmd(self, cmd: str, cwd: Path) -> tuple[bool, list]:
        env = os.environ.copy()

        if sys.platform != "win32":
            cmd = cmd.replace("$(nproc)", str(os.cpu_count() or 1))
        else:
            cmd = cmd.replace("-j$(nproc)", "")

        if not self.dep_mgr.in_docker and self.dep_mgr.get_pkg_manager() == "dnf":
            for path in [Path("/usr/lib/jvm/java-latest-openjdk"), Path("/usr/lib/jvm/java-21-openjdk")]:
                if path.exists():
                    env["JAVA_HOME"] = str(path)
                    env["PATH"] = f"{path}/bin:{env.get('PATH', '')}"
                    break

        kw, all_output, errors = ["error:", "failed", "exception", "not supported", "syntaxerror",
                                  "cannot find", "could not resolve", "could not get",
                                  "status code 5", "connection refused", "failed to connect"], [], []
        t0 = time.time()

        with open(self.log_file, "a", encoding="utf-8") as log:
            log.write(f"\n--- RUN: {cmd} ---\n")
            proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            for line in proc.stdout:
                log.write(line)
                log.flush()
                all_output.append(line.rstrip())
                if any(k in line.lower() for k in kw) and len(errors) < 30:
                    stripped = line.strip()
                    if stripped and stripped not in errors:
                        errors.append(stripped)
            proc.wait()

        if proc.returncode != 0 and not errors:
            errors = all_output[-20:]
        return proc.returncode == 0, errors

    def test_cmd(self, root, plugin):
        tool = plugin["tool"]
        wrapper = self.find_wrapper(root, plugin.get("wrapper", ""))
        if wrapper and tool in ["gradle", "mvn"]: return f"\"{wrapper}\" test"
        if tool in ["npm", "pnpm", "yarn"]: return f"{tool} test"
        if tool == "go": return "go test ./..."
        if "python" in tool or tool == "pip":
            if shutil.which("pytest"): return "pytest"
            return "python -m unittest discover" if sys.platform == "win32" else "python3 -m unittest discover"
        return None

    EXEC_MAGIC = (b"\x7fELF", b"MZ", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                  b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")

    @classmethod
    def _looks_executable(cls, f: Path) -> bool:
        """Extensionless files must carry real executable magic. X_OK is
        meaningless when running as root (every readable file passes)."""
        try:
            with open(f, "rb") as fh:
                head = fh.read(4)
        except Exception:
            return False
        if head.startswith(b"#!"):
            return True
        return head in cls.EXEC_MAGIC

    def harvest(self, name: str, root: Path, plugin: dict) -> list:
        items = []
        seen = set()
        for out_dir in plugin["out_dirs"]:
            for base in [root] + list(root.parents)[:2]:

                if not str(base.resolve()).startswith(str(self.src_root)):
                    continue

                target = base / out_dir
                if not target.exists(): continue

                if "*DIR*" in plugin["out_exts"]:
                    dest = self.out_root / f"{name}_{out_dir.replace('/', '_')}"
                    if dest.exists(): shutil.rmtree(dest, ignore_errors=True)
                    try:
                        shutil.copytree(target, dest, ignore=shutil.ignore_patterns('.*', 'node_modules', 'venv', '__pycache__'))
                        items.append({"name": dest.name})
                        UI.log(UI.GREEN, t('act_saved'), f"Folder -> {dest.name}")
                    except Exception:
                        pass
                else:
                    try:
                        for f in target.rglob('*'):
                            try:
                                if not f.is_file():
                                    continue
                                if ".git" in f.parts or "node_modules" in f.parts:
                                    continue
                                if f.name.startswith('.'):
                                    continue
                                if f.suffix:
                                    ok = f.suffix in plugin["out_exts"]
                                else:
                                    # '' in out_exts must not blanket-accept
                                    # every extensionless file (root sees X_OK
                                    # on anything readable): require magic.
                                    ok = self._looks_executable(f)
                                if not ok:
                                    continue
                                dest_f = self.out_root / f"{name}_{f.name}"
                                if str(dest_f) in seen:
                                    continue
                                seen.add(str(dest_f))
                                shutil.copy2(f, dest_f)
                                items.append({"name": dest_f.name})
                                UI.log(UI.GREEN, t('act_saved'), f"File -> {dest_f.name}")
                            except PermissionError:
                                continue
                            except Exception:
                                continue
                    except Exception:
                        pass
        return items

    def find_wrapper(self, cwd, name):
        if not name: return None
        path = Path(cwd).resolve()
        names = [f"{name}.bat", f"{name}.cmd", name] if sys.platform == "win32" else [name]
        while True:
            for n in names:
                if (path / n).exists(): return str(path / n)
            if path.parent == path or path.name in ["workspace", "src"]: break
            path = path.parent
        return None

    def _find_workspace_root(self, root: Path) -> dict | None:
        for p in [root] + list(root.parents):
            # JS monorepos
            if p.joinpath("pnpm-workspace.yaml").exists():
                return {"root": p, "type": "pnpm"}
            if p.joinpath("nx.json").exists():
                return {"root": p, "type": "nx"}
            if p.joinpath("turbo.json").exists():
                return {"root": p, "type": "turbo"}
            if p.joinpath("lerna.json").exists():
                return {"root": p, "type": "lerna"}
            try:
                pkg = json.loads(p.joinpath("package.json").read_text())
                if "workspaces" in pkg:
                    return {"root": p, "type": "npm" if not p.joinpath("pnpm-lock.yaml").exists() and not p.joinpath("yarn.lock").exists() else ("pnpm" if p.joinpath("pnpm-lock.yaml").exists() else "yarn")}
            except Exception:
                pass
            # Go workspace
            if p.joinpath("go.work").exists():
                return {"root": p, "type": "go"}
            # Java/Gradle multi-project
            for gradle_file in ["settings.gradle", "settings.gradle.kts"]:
                if p.joinpath(gradle_file).exists():
                    try:
                        text = p.joinpath(gradle_file).read_text()
                        if "include" in text:
                            return {"root": p, "type": "gradle"}
                    except Exception:
                        pass
            # Java/Maven multi-module
            if p.joinpath("pom.xml").exists():
                try:
                    text = p.joinpath("pom.xml").read_text()
                    if "<module>" in text:
                        return {"root": p, "type": "maven"}
                except Exception:
                    pass
            # Rust workspace
            if p.joinpath("Cargo.toml").exists():
                try:
                    text = p.joinpath("Cargo.toml").read_text()
                    if "[workspace]" in text:
                        return {"root": p, "type": "cargo"}
                except Exception:
                    pass
            if p == self.src_root:
                break
        return None

    def build_cmd(self, root, plugin, attempt):
        cmd, tool = plugin.get("cmd_system", ""), plugin["tool"]
        wrap = self.find_wrapper(root, plugin.get("wrapper", ""))
        if wrap:
            if sys.platform != "win32": os.system(f"chmod +x \"{wrap}\"")
            if "gradle" in tool: return f"\"{wrap}\" assemble"
            if "mvn" in tool: return f"\"{wrap}\" clean package -DskipTests"

        if cmd == "DYNAMIC_JS_RESOLUTION":
            ws = self._find_workspace_root(root)
            if ws:
                t = ws["type"]
                if t == "pnpm":
                    install_cmd = "pnpm install --no-frozen-lockfile"
                    build_cmd = "pnpm run -r build" if attempt == 1 else "pnpm run --filter xmcl-electron-app build 2>/dev/null || pnpm run build"
                elif t == "yarn":
                    install_cmd = "yarn install --frozen-lockfile"
                    build_cmd = "yarn workspaces run build"
                elif t == "nx":
                    install_cmd = f"{tool} install --no-frozen-lockfile" if tool == "pnpm" else f"npm install --legacy-peer-deps"
                    build_cmd = "npx nx run-many --target=build --all"
                elif t == "turbo":
                    install_cmd = f"{tool} install --no-frozen-lockfile" if tool == "pnpm" else f"npm install --legacy-peer-deps"
                    build_cmd = "npx turbo run build"
                elif t == "lerna":
                    install_cmd = f"{tool} install --no-frozen-lockfile" if tool == "pnpm" else f"npm install --legacy-peer-deps"
                    build_cmd = "npx lerna run build"
                else:
                    install_cmd = "npm install --legacy-peer-deps"
                    build_cmd = "npm run build --workspaces"
            else:
                install_cmd = f"npm install --legacy-peer-deps" if tool != "pnpm" else f"pnpm install --no-frozen-lockfile"
                build_cmd = f"{tool} run build" if (root / "package.json").exists() else install_cmd
            return f"{install_cmd} && {build_cmd}"
        elif cmd == "DYNAMIC_GO_RESOLUTION":
            ws = self._find_workspace_root(root)
            prefix = f"cd {ws['root']} && " if ws and ws["type"] == "go" else ""
            # -o dir/ fails when the dir doesn't exist; create it up front.
            # 'noassets' tag swaps in stub generated assets (see auto/noassets.go pattern).
            tags = " -tags noassets" if self._go_noassets else ""
            return f"{prefix}mkdir -p build_output && go build -o build_output/{tags} ./..."
        elif cmd == "DYNAMIC_PYTHON_RESOLUTION":
            if (root / "pyproject.toml").exists():
                return "pip3 install build && python3 -m build --outdir dist"
            elif (root / "setup.py").exists():
                return "python3 setup.py sdist bdist_wheel --dist-dir dist" if shutil.which("wheel") else "python3 setup.py sdist --dist-dir dist"
            return "pip3 install -r requirements.txt && mkdir -p dist && cp -r . dist/" if (root / "requirements.txt").exists() else "python3 -m pip install --upgrade pip && pip3 install build && python3 -m build --outdir dist"
        elif cmd == "DYNAMIC_DART_RESOLUTION":
            if shutil.which("flutter"):
                return "flutter build linux --release" if (root / "linux").exists() else "flutter build web --release"
            return "dart pub get && dart compile exe bin/main.dart -o build/main 2>/dev/null || dart compile kernel bin/main.dart -o build/main.dill"
        elif cmd == "DYNAMIC_CPP_RESOLUTION":
            if sys.platform == "win32":
                return "if not exist build mkdir build && cd build && cmake .. && cmake --build . --config Release"
            else:
                return "mkdir -p build && cd build && cmake .. && make -j$(nproc)"
        return cmd

    def classify_errors(self, errs: list) -> str:
        """Bucket build failures so we can stop retrying hopeless categories."""
        text = "\n".join(errs)
        if "Could not resolve" in text and re.search(r"status code 5\d\d", text):
            return "upstream_outage"
        if "Could not GET" in text and re.search(r"Failed to connect|Could not connect|Connection refused|timed out", text):
            return "network_down"
        if "OutOfMemoryError" in text or "Java heap space" in text:
            return "oom"
        return ""

    def parse_and_rescue(self, errors) -> bool:
        if not self.dep_mgr.in_docker:
            return False

        RESCUE_DICTIONARY = {
            "openssl/ssl.h": "libssl-dev",
            "curl/curl.h": "libcurl4-openssl-dev",
            "sqlite3.h": "libsqlite3-dev",
            "uuid/uuid.h": "uuid-dev",
            "zlib.h": "zlib1g-dev",
            "SDL.h": "libsdl2-dev",
            "X11/Xlib.h": "libx11-dev",
            "GL/glew.h": "libglew-dev",
            "png.h": "libpng-dev",
            "jpeglib.h": "libjpeg-dev",
            "gtk/gtk.h": "libgtk-3-dev",
            "gtk-3.0/gtk.h": "libgtk-3-dev",
            "webkit2/webkit2.h": "libwebkit2gtk-4.1-dev",
            "libsoup/soup.h": "libsoup-3.0-dev",
            "pango/pango.h": "libpango1.0-dev",
            "cairo/cairo.h": "libcairo2-dev",
            "atk/atk.h": "libatk1.0-dev",
            "gdk-pixbuf/gdk-pixbuf.h": "libgdk-pixbuf-xlib-2.0-dev",
            "X11/Xlib.h": "libx11-dev",
            "X11/Xatom.h": "libx11-dev",
            "X11/extensions/Xrandr.h": "libxrandr-dev",
            "X11/extensions/Xinerama.h": "libxinerama-dev",
            "X11/Xcursor/Xcursor.h": "libxcursor-dev",
            "X11/extensions/XInput.h": "libxi-dev",
            "X11/extensions/XTest.h": "libxtst-dev",
            "X11/Intrinsic.h": "libxt-dev",
            "X11/Xft/Xft.h": "libxft-dev",
            "alsa/asoundlib.h": "libasound2-dev",
            "pulse/pulseaudio.h": "libpulse-dev",
            "jack/jack.h": "libjack-dev",
            "freetype/freetype.h": "libfreetype6-dev",
            "fontconfig/fontconfig.h": "libfontconfig1-dev",
            "harfbuzz/hb.h": "libharfbuzz-dev",
            "libusb-1.0/libusb.h": "libusb-1.0-0-dev",
            "libinput.h": "libinput-dev",
            "wayland-client.h": "libwayland-dev",
            "EGL/egl.h": "libegl1-mesa-dev",
            "GLES3/gl3.h": "libgles2-mesa-dev",
            "Python.h": "python3-dev",
            "lua.h": "liblua5.4-dev",
            "tcl.h": "tcl-dev",
            "tk.h": "tk-dev",
            "ncurses.h": "libncurses-dev",
            "readline/readline.h": "libreadline-dev",
            "zstd.h": "libzstd-dev",
        }

        for err in errors:
            match_header = re.search(r'(?:fatal )?error: ([a-zA-Z0-9_/\.\-]+): No such file', err)
            match_lib = re.search(r'cannot find -l([a-zA-Z0-9_]+)', err)
            match_pkg_config = re.search(r'Package\s+([a-zA-Z0-9_\-]+)\s+was\s+not\s+found', err)

            missing_item = None
            rescue_type = "apt"
            if match_header:
                missing_item = match_header.group(1)
            elif match_lib:
                missing_item = f"lib{match_lib.group(1)}.so"
            elif match_pkg_config:
                missing_item = match_pkg_config.group(1)

            if missing_item and rescue_type == "apt":
                if self._rescue_apt(missing_item, RESCUE_DICTIONARY):
                    return True
                continue

            py_match = re.search(r"(?:ModuleNotFoundError|ImportError):\s*(?:No module named\s+)?['\"]?(\w+(?:\.\w+)*)['\"]?", err)
            if py_match:
                pkg = py_match.group(1).split('.')[0]
                UI.log(UI.MAGENTA, "AI-RESCUE   ", f"Missing Python module: {pkg}")
                if self.dep_mgr.pip_install(pkg):
                    UI.log(UI.GREEN, "AI-RESCUE   ", f"Installed Python package: {pkg}")
                    return True
                continue

            node_match = re.search(r"Error:\s+Cannot find module ['\"]([^'\"]+)['\"]", err)
            if not node_match:
                node_match = re.search(r"MODULE_NOT_FOUND.*['\"]?([a-zA-Z0-9_\-@/]+)['\"]?", err)
            if node_match:
                pkg = node_match.group(1).split('/')[0]
                if pkg.startswith('@'):
                    pkg = node_match.group(1).split('/')[0] + '/' + node_match.group(1).split('/')[1]
                UI.log(UI.MAGENTA, "AI-RESCUE   ", f"Missing Node.js module: {pkg}")
                if self.dep_mgr.npm_install(pkg):
                    UI.log(UI.GREEN, "AI-RESCUE   ", f"Installed Node.js package: {pkg}")
                    return True
                continue

            ruby_match = re.search(r"cannot load such file -- (\S+)", err)
            if ruby_match:
                pkg = ruby_match.group(1).split('/')[0]
                UI.log(UI.MAGENTA, "AI-RESCUE   ", f"Missing Ruby library: {pkg}")
                if self.dep_mgr.gem_install(pkg):
                    UI.log(UI.GREEN, "AI-RESCUE   ", f"Installed Ruby gem: {pkg}")
                    return True
                continue

            go_match = re.search(r"package\s+([a-zA-Z0-9_\-/]+)\s+is\s+not\s+in\s+GOROOT", err)
            if go_match:
                pkg = go_match.group(1).split('/')[0]
                UI.log(UI.MAGENTA, "AI-RESCUE   ", f"Missing Go package: {pkg}")
                try:
                    subprocess.run(["go", "get", pkg], capture_output=True, text=True, timeout=30)
                    UI.log(UI.GREEN, "AI-RESCUE   ", f"Fetched Go package: {pkg}")
                    return True
                except Exception:
                    pass
                continue

            rust_match = re.search(r"can't find crate for ['\`]([a-zA-Z0-9_\-]+)['\`]", err)
            if rust_match:
                pkg = rust_match.group(1)
                UI.log(UI.MAGENTA, "AI-RESCUE   ", f"Missing Rust crate dependency: {pkg}")
                try:
                    subprocess.run(["cargo", "install", pkg], capture_output=True, text=True, timeout=60)
                    UI.log(UI.GREEN, "AI-RESCUE   ", f"Fetched Rust crate: {pkg}")
                    return True
                except Exception:
                    pass
                continue

        return False

    def _rescue_apt(self, missing_item, rescue_dict):
        UI.log(UI.MAGENTA, "AI-RESCUE   ", f"Missing dependency detected: {missing_item}")

        if missing_item in rescue_dict:
            pkg = rescue_dict[missing_item]
            UI.log(UI.CYAN, "AI-RESCUE   ", f"Installing known package: {pkg}")
            return self.dep_mgr.trigger_install(pkg)

        UI.log(UI.CYAN, "AI-RESCUE   ", f"Searching Ubuntu database for {missing_item}...")
        try:
            res = subprocess.run(["apt-file", "search", missing_item], capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout:
                lines = [line for line in res.stdout.split('\n') if line and '-dev' in line.split(':')[0]]
                if not lines:
                    lines = [line for line in res.stdout.split('\n') if line]
                if lines:
                    pkg = lines[0].split(':')[0].strip()
                    UI.log(UI.CYAN, "AI-RESCUE   ", f"Found matching package: {pkg}")
                    return self.dep_mgr.trigger_install(pkg)
        except Exception:
            pass
        return False

    def process(self, root: Path, files: list, plugin: dict) -> None:
        # Walk up to find the actual project root if this is a subdirectory match
        if plugin.get("wrapper") or plugin.get("cmd_system", "").startswith("gradle"):
            for ancestor in [root] + list(root.parents):
                if ancestor == self.src_root.parent: break
                if any((ancestor / bf).exists() for bf in ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"]):
                    root = ancestor
                    break
        elif "mvn" in plugin.get("tool", "") or plugin.get("cmd_system", "").startswith("mvn"):
            for ancestor in [root] + list(root.parents):
                if ancestor == self.src_root.parent: break
                if (ancestor / "pom.xml").exists():
                    root = ancestor
                    break

        name = root.name if root.name != "src" else "Root-Workspace"
        if root == self.src_root and self.project_name:
            name = self.project_name
        req = self.dep_mgr.inspect_version(root, plugin["tool"])
        UI.log(UI.BLUE, t('act_detected'), f"{name} [{plugin['name']}] ({t('req_msg', req=req)})")

        if not self.check_ready(plugin, root):
            self.stats["skipped"] += 1
            return

        if self.test_mode:
            cmd = self.test_cmd(root, plugin)
            if cmd:
                UI.log(UI.CYAN, t('act_test'), cmd)
                ok, errs = self.run_cmd(cmd, root)
                if not ok:
                    UI.log(UI.RED, t('act_fail'), t('test_fail_abort', name=name))
                    for e in errs: print(f"   {UI.RED}↳ {e}{UI.RESET}")
                    self.stats["failed"] += 1
                    return
                UI.log(UI.GREEN, t('act_verify'), t('test_success'))

        t0 = time.time()
        attempt = 0
        build_ok = False
        build_errs = []
        while attempt < 3:
            attempt += 1
            cmd = self.build_cmd(root, plugin, attempt)
            UI.log(UI.CYAN, t('act_build'), f"Strategy {attempt}/3...")
            ok, errs = self.run_cmd(cmd, root)
            build_errs = errs
            if ok:
                dur = round(time.time() - t0, 1)
                UI.log(UI.GREEN, t('act_ready'), f"{name} in {dur}s")
                artifacts = self.harvest(name, root, plugin)
                if artifacts:
                    self.manifest_data["projects"].append({"name": name, "lang": plugin["name"], "time": dur, "items": artifacts})
                    self.stats["success"] += 1
                    return
                build_ok = True
                break
            else:
                kind = self.classify_errors(errs)
                if kind == "upstream_outage":
                    UI.warn("Dependency repository unreachable (HTTP 5xx). "
                            "This is an upstream outage, not a local problem - skipping retries.")
                    build_errs = errs
                    break
                # Go projects with ungenerated embedded assets: retry once with stub tag
                if (plugin.get("tool") == "go" and not self._go_noassets
                        and re.search(r"undefined: auto\.\w+", "\n".join(errs))):
                    UI.warn("Generated Go assets missing (undefined: auto.*). "
                            "Retrying once with build tag 'noassets'...")
                    self._go_noassets = True
                    attempt -= 1
                    continue
                if kind == "oom":
                    UI.warn("Build ran out of memory. Consider closing apps or lowering gradle jvmargs.")
                if self.parse_and_rescue(errs):
                    UI.log(UI.GREEN, "AI-RESCUE   ", "Dependency installed successfully! Retrying build...")
                    attempt -= 1
                    continue

                UI.error(t('err_output_title'))
                for e in errs[-5:]:
                    print(f"   {UI.RED}• {e}{UI.RESET}")
            UI.log(UI.YELLOW, t('act_retry'), t('fallback_msg'))

        if build_ok:
            dur = round(time.time() - t0, 1)
            candidates = [root]
            if root.parent and root.parent != root:
                candidates.append(root.parent)
            for root_candidate in candidates:
                scripts = self._detect_entry_scripts(root_candidate)
                if scripts:
                    UI.log(UI.GREEN, "Entry points", f"found {len(scripts)} script(s)")
                    for s in scripts:
                        src = root_candidate / s["name"]
                        if not src.exists():
                            continue
                        dest = self.out_root / f"{name}_{s['name']}"
                        shutil.copy2(src, dest)
                        s["path"] = str(dest)
                        UI.log(UI.GREEN, t('act_saved'), f"Script -> {dest.name}")
                    proj_dest = self.out_root / f"{name}_source"
                    if not proj_dest.exists():
                        shutil.copytree(root_candidate, proj_dest, ignore=shutil.ignore_patterns('.*', 'node_modules', 'venv', '__pycache__', 'build', 'target', 'dist', 'bin'), dirs_exist_ok=True)
                        UI.log(UI.GREEN, t('act_saved'), f"Source -> {proj_dest.name}")
                    self.manifest_data["projects"].append({"name": name, "lang": plugin["name"], "time": dur, "items": scripts, "runtime_deps": plugin.get("runtime_deps", [])})
                    self.stats["success"] += 1
                    return
            self.stats["failed"] += 1
            return

        UI.log(UI.RED, t('act_fail'), t('compile_fail'))
        self.stats["failed"] += 1

    def _detect_entry_scripts(self, root: Path) -> list:
        found = []
        for f in root.iterdir():
            if not f.is_file() or f.name.startswith('.'):
                continue
            if f.suffix in ('.sh', '.bash', '.py', '.pl', '.rb', '.lua', '.js', '.ts'):
                found.append({"name": f.name, "kind": "script"})
            elif f.suffix == '' and not f.name.startswith('.'):
                try:
                    with open(f, 'rb') as fh:
                        head = fh.read(64)
                    if head.startswith(b'#!'):
                        found.append({"name": f.name, "kind": "script"})
                except Exception:
                    pass
        return found

    def _has_parent_marker(self, root: str, detect_list: list) -> bool:
        cur = Path(root).resolve()
        src = Path(self.src_root).resolve()
        while cur != src and cur != cur.parent:
            cur = cur.parent
            if cur < src:
                break
            try:
                parent_files = set(f.name for f in cur.iterdir() if f.is_file())
            except Exception:
                continue
            for d in detect_list:
                if "*" in d:
                    pat = d.replace("*", "")
                    if any(f.endswith(pat) or f == pat for f in parent_files):
                        return True
                elif d in parent_files:
                    return True
        return False

    def run(self, filter_name: str = "") -> bool:
        t0 = time.time()
        UI.log(UI.BLUE, t('act_scan'), f"{self.src_root.resolve()}")

        ws = self._find_workspace_root(self.src_root)
        ws_children = set()
        if ws:
            for d in ws["root"].iterdir():
                if d.is_dir() and (d / "package.json").exists():
                    ws_children.add(str(d.resolve()))

        for root, dirs, files in os.walk(str(self.src_root)):
            root_p = str(Path(root).resolve())
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ["node_modules", "target", "build", "dist", "bin", "venv", "__pycache__", "BUILD_ARTIFACTS", "_git_cache"]]

            if ws and root_p in ws_children:
                continue

            best = None
            best_spec = -1
            for p in self.plugins:
                if filter_name and p["name"] != filter_name:
                    continue
                if any(f in files for f in p["detect"]) or any(any(f.endswith(d.replace('*', '')) for f in files) for d in p["detect"] if '*' in d):
                    spec = p.get("specificity", 0)
                    if spec > best_spec:
                        best = p
                        best_spec = spec

            if best:
                if not self._has_parent_marker(root, best["detect"]):
                    self.process(Path(root), files, best)
                else:
                    self.stats["skipped"] += 1

        Path(self.manifest_file).write_text(json.dumps(self.manifest_data, indent=4), encoding="utf-8")

        elapsed = round(time.time() - t0, 1)
        report_line = t('report_status', green=UI.GREEN, success=self.stats['success'], red=UI.RED, failed=self.stats['failed'], yellow=UI.YELLOW, skipped=self.stats['skipped'], reset=UI.RESET, time=elapsed)
        UI.draw_panel(t('report_header'), [report_line], color=UI.CYAN)
        self.dep_mgr.cleanup()
        return self.stats['failed'] == 0

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--test", action="store_true")
    p.add_argument("--auto-install", action="store_true")
    p.add_argument("--lang", default="en")
    p.add_argument("--filter", default="")
    p.add_argument("--name", default="")
    args = p.parse_args()

    core.set_lang(args.lang)
    ok = Engine(Path(args.src), Path(args.out), args.test, auto_install=args.auto_install,
                project_name=args.name).run(filter_name=args.filter)
    sys.exit(0 if ok else 1)
