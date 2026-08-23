import os
import sys
import subprocess
import shutil
import platform
import getpass
import urllib.request
import time
import json
import hashlib
import re
import datetime
from pathlib import Path
import core
from core import UI, t
import docker_manager
import hostdeps

VERSION = "2.6.1"
CURRENT_STATUS = "Standby"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
UPDATE_FILES = ["justcompiler.py", "core.py", "engine.py", "docker_manager.py", "tui.py", "hostdeps.py", "plugins.json", "checksums.txt"]

def verify_checksum(file_path: str, expected_hash: str) -> bool:
    sha256 = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                sha256.update(chunk)
        return sha256.hexdigest() == expected_hash
    except Exception:
        return False

def load_checksums(file_path: str) -> dict:
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

def load_config() -> dict:
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

def save_config(**updates: dict) -> dict:
    config = load_config()
    config.update(updates)
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=4), encoding="utf-8")
    except Exception:
        pass
    return config

def set_current_status(msg: str) -> None:
    global CURRENT_STATUS
    CURRENT_STATUS = msg

def init_terminal_colors() -> None:
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

def check_for_updates() -> None:
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
                   _WALK_SKIP_DIRS + ["BUILD_ARTIFACTS"]]
        files = set(os.listdir(dirpath))
        # within a directory, specific ecosystem markers must be tried before
        # generic manifests (a root Makefile must not mask a pyproject.toml)
        order = sorted(range(len(plugins)),
                       key=lambda i: (any(d in GENERIC_DETECT for d in plugins[i]["detect"]), i))
        for idx in order:
            p = plugins[idx]
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
    specs = {}
    hits = {}   # target -> {(detect, is_root)} unique marker locations
    for f in project_root.rglob("*"):
        if not f.is_file():
            continue
        for t in targets:
            plugin = pdata[t["plugin_idx"]]
            specs[t["name"]] = plugin.get("specificity", 0)
            for d in plugin.get("detect", []):
                if "*" in d:
                    pat = d.replace("*", "")
                    if not (f.name.endswith(pat) or f.name == pat):
                        continue
                elif "/" not in d:
                    if f.name != d:
                        continue
                else:
                    rel = str(f.relative_to(project_root)).replace("\\", "/")
                    if rel != d:
                        continue
                # repeated identical markers across a monorepo count ONCE
                hits.setdefault(t["name"], set()).add((d, f.parent == project_root))
    for name, keys in hits.items():
        score = 0
        for d, is_root in keys:
            s = 1 if "*" in d else 2
            if is_root:
                s += 3
            if d in GENERIC_DETECT:
                s -= 4
            score += s
        markers[name] = score
    if markers:
        # plugin specificity breaks ties/weight once, not per marker-hit
        return max(markers, key=lambda n: (markers[n] + specs.get(n, 0), markers[n]))
    return targets[0]["name"]

JAVA_DECL_PATTERNS = [
    r"sourceCompatibility\s*=\s*['\"]?(\d{1,2})(?:\.\d+)?['\"]?",
    r"sourceCompatibility\s*=\s*JavaVersion\.VERSION_(\d{1,2})",
    r"targetCompatibility\s*=\s*['\"]?(\d{1,2})(?:\.\d+)?['\"]?",
    r"targetCompatibility\s*=\s*JavaVersion\.VERSION_(\d{1,2})",
    r"JavaLanguageVersion\.of\(\s*(\d{1,2})\s*\)",
    r"jvmToolchain\(\s*(\d{1,2})\s*\)",
    r"<maven\.compiler\.release>(\d{1,2})</maven\.compiler\.release>",
    r"<maven\.compiler\.source>(\d{1,2})</maven\.compiler\.source>",
    r"<maven\.compiler\.target>(\d{1,2})</maven\.compiler\.target>",
    r"<java\.version>(\d{1,2})</java\.version>",
    r"<release>(\d{1,2})</release>",
]
_JAVA_BUILD_FILES = {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "pom.xml"}
GENERIC_DETECT = {
    "package.json", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "pom.xml", "CMakeLists.txt", "Makefile",
    "makefile", "GNUmakefile", "meson.build", "Cargo.toml", "go.mod", "BUILD",
}
_WALK_SKIP_DIRS = ["node_modules", "target", "build", "dist", "bin", "venv", "__pycache__", ".git", ".gradle", "_git_cache",
                   # tooling/CI helpers rarely contain the real project
                   # (tests/ is NOT skipped: it often carries real markers)
                   "support", "ci", "scripts", "tools", "docs", "examples"]

_PROJECT_CFG_ALLOWED = {"target", "java_version", "profile", "network",
                        "memory_limit", "cpu_limit", "env", "run_tests"}

def load_project_config(root: Path) -> dict:
    """Load and validate repo-owned .justcompiler.json overrides (B2).
    Unknown keys are ignored; target is validated against plugins.json."""
    p = root / ".justcompiler.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        UI.warn(f"Ignoring invalid .justcompiler.json: {e}")
        return {}
    if not isinstance(data, dict):
        UI.warn("Ignoring .justcompiler.json: top level must be an object")
        return {}
    cfg = {k: v for k, v in data.items() if k in _PROJECT_CFG_ALLOWED}
    dropped = sorted(set(data) - set(cfg))
    if dropped:
        UI.warn(f"Ignoring unknown .justcompiler.json keys: {', '.join(dropped)}")
    if "target" in cfg:
        try:
            names = {pl["name"] for pl in json.loads(
                (Path(__file__).resolve().parent / "plugins.json").read_text())}
            if cfg["target"] not in names:
                UI.warn(f".justcompiler.json target '{cfg['target']}' is not a known plugin; ignoring")
                cfg.pop("target")
        except Exception:
            pass
    if "env" in cfg and not isinstance(cfg["env"], dict):
        UI.warn(".justcompiler.json env must be an object; ignoring")
        cfg.pop("env")
    return cfg

def _is_git_url(s: str) -> bool:
    s = s.strip()
    return (s.startswith(("http://", "https://", "git@", "ssh://"))
            or (s.endswith(".git") and "/" in s)
            or s.startswith("github.com/"))

def _cache_dest_for(url: str) -> Path:
    """Stable cache path for a repo URL (independent of branch)."""
    repo_name = url.rstrip("/").split("/")[-1].replace(".git", "").lower() or "repo"
    h = hashlib.sha256(url.strip().lower().rstrip("/").encode()).hexdigest()[:12]
    return Path.home() / ".justcompiler" / "repos" / f"{repo_name}-{h}"

def _clone_to_cache(url: str, branch: str | None = None) -> tuple:
    """Shallow-clone `url` (branch optional) into the shared cache, reusing and
    fast-forwarding an existing clone. Returns (path, branch_used, commit)."""
    dest = _cache_dest_for(url)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if dest.exists() and (dest / ".git").exists():
        subprocess.run(["git", "fetch", "--all", "--prune", "-q"], cwd=dest,
                       capture_output=True, env=env)
        b = branch
        if not b:
            res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                 cwd=dest, capture_output=True, text=True, env=env)
            b = res.stdout.strip() or None
        if b:
            subprocess.run(["git", "checkout", "-q", b], cwd=dest, capture_output=True, env=env)
        subprocess.run(["git", "pull", "--ff-only", "-q"], cwd=dest, capture_output=True, env=env)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["-b", branch]
        cmd += [url, str(dest)]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "clone failed").strip()[-300:])
    sha_res = subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], cwd=dest,
                             capture_output=True, text=True, env=env)
    sha = sha_res.stdout.strip() if sha_res.returncode == 0 else "unknown"
    return dest, (branch or "default"), sha

def _summarize(status: str, error_class: str, target: str, java_ver,
               duration_s: float, artifacts_dir: Path, manifest: dict,
               commit: str = "") -> dict:
    """Machine-readable end-of-run summary (A3)."""
    projects = (manifest or {}).get("projects", [])
    return {
        "status": status,
        "error_class": error_class,
        "target": target,
        "toolchain": {"java": java_ver},
        "commit": commit,
        "duration_s": duration_s,
        "artifacts_dir": str(artifacts_dir),
        "artifacts": [i.get("name") for p in projects for i in p.get("items", [])],
        "logs": [str(artifacts_dir / "build.log"),
                 str(artifacts_dir / "build_log.txt")],
        "possible_runtime_deps": sorted({d.get("pkg") for p in projects
                                         for d in p.get("runtime_deps", []) if d.get("pkg")}),
    }

def _manifest_runtime_dep_tuples(build_folder: Path) -> set:
    """7-tuples of runtime_deps recorded in a build's manifest."""
    try:
        manifest = json.loads((build_folder / "build_manifest.json").read_text())
    except Exception:
        return set()
    deps = set()
    for proj in manifest.get("projects", []):
        for dep in proj.get("runtime_deps", []):
            deps.add((
                dep["pkg"], dep.get("apt", ""), dep.get("pacman", ""),
                dep.get("dnf", ""), dep.get("winget", ""),
                dep.get("choco", ""), dep.get("scoop", ""),
            ))
    return deps

def _error_class_from_log(build_folder: Path) -> str:
    try:
        log = (build_folder / "build_log.txt").read_text(errors="replace").splitlines()[-60:]
    except Exception:
        return ""
    import engine as _eng
    probe = _eng.Engine.__new__(_eng.Engine)
    probe.last_missing_tool = ""
    return probe.classify_errors(log)

def _notify(title: str, body: str):
    """Best-effort desktop notification (Linux/macOS/Windows)."""
    try:
        title = title.replace('"', "'").replace("`", "'")[:80]
        body = body.replace('"', "'").replace("`", "'")[:160]
        if sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e",
                              f'display notification "{body}" with title "{title}"'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32":
            ps = ("[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')|Out-Null;"
                  f"$n=New-Object System.Windows.Forms.NotifyIcon;"
                  f"$n.Icon=[System.Drawing.SystemIcons]::Information;"
                  f"$n.Visible=$true;$n.ShowBalloonTip(5000,'{title}','{body}','Info')")
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000)
        else:
            if shutil.which("notify-send"):
                subprocess.Popen(["notify-send", "-a", "JustCompiler", title, body],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

REPORT_MAX_BYTES = 8 * 1024 * 1024   # safety cap for clipboard/report size

def _scrub_text(text: str) -> str:
    """Remove personal data: home paths, username, hostname, e-mails."""
    try:
        home = str(Path.home())
        if home and home != "/":
            text = text.replace(home, "~")
    except Exception:
        pass
    try:
        user = getpass.getuser()
        if user:
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])",
                          "<user>", text)
    except Exception:
        pass
    try:
        node = platform.node()
        if node:
            text = re.sub(re.escape(node), "<host>", text)
    except Exception:
        pass
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                  "<email>", text)

def _copy_to_clipboard(text: str) -> bool:
    """Best-effort cross-platform clipboard copy. Returns success."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        if sys.platform == "win32":
            # clip.exe reads UTF-16LE without BOM fine
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
            return True
        for tool in ("wl-copy", "xclip", "xsel"):
            if shutil.which(tool):
                argv = {"wl-copy": ["wl-copy"],
                        "xclip": ["xclip", "-selection", "clipboard"],
                        "xsel": ["xsel", "--clipboard", "--input"]}[tool]
                subprocess.run(argv, input=text.encode("utf-8"), check=True)
                return True
    except Exception:
        pass
    return False

def _write_failure_report(build_folder: Path, summary: dict,
                          max_bytes: int | None = None) -> str | None:
    """Write failure_report.txt with FULL scrubbed crash logs. Returns path."""
    try:
        import time as _t
        max_bytes = max_bytes or REPORT_MAX_BYTES
        parts = []

        def section(title: str, body: str):
            body = _scrub_text(body.rstrip("\n"))
            parts.append(f"\n===== {title} =====\n{body}\n")

        env_line = f"{platform.system()} {platform.release()} · Python {platform.python_version()}"
        try:
            osr = Path("/etc/os-release")
            for ln in osr.read_text().splitlines():
                if ln.startswith("PRETTY_NAME="):
                    env_line += " · " + ln.split("=", 1)[1].strip('"')
                    break
        except Exception:
            pass
        try:
            dv = subprocess.run(["docker", "--version"], capture_output=True,
                                text=True, timeout=5).stdout.strip()
            if dv: env_line += " · " + dv
        except Exception:
            pass

        header = (f"JustCompiler v{VERSION} crash report\n"
                  f"date: {_t.strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"target: {summary.get('target','-')}  "
                  f"error_class: {summary.get('error_class') or '-'}\n"
                  f"status: {summary.get('status','-')}  "
                  f"duration: {summary.get('duration_s','?')}s\n"
                  f"environment: {env_line}\n"
                  f"project dir: {build_folder.name}")
        parts.append(header)

        deps = summary.get("possible_runtime_deps") or []
        if deps:
            parts.append("possible runtime deps: " + ", ".join(deps))

        cap_hit = False
        used = sum(len(p) for p in parts)
        for fname in ("run.log", "build.log", "build_log.txt"):
            fp = build_folder / fname
            if not fp.is_file():
                continue
            raw = fp.read_text(errors="replace")
            remaining = max_bytes - used - len(raw)
            if remaining < 0 and max_bytes > 0:
                keep = max(0, remaining + len(raw))
                raw = ("[... truncated to fit size cap ...]\n") + raw[-keep:] \
                      if keep > 0 else "[... skipped: size cap ...]"
                cap_hit = True
            section(f"{fname} (full)", raw)
            used = sum(len(p) for p in parts)

        report = "\n".join(parts) + ("\n[size cap reached]\n" if cap_hit else "\n")
        report = _scrub_text(report[:max_bytes]) if max_bytes else report
        out = build_folder / "failure_report.txt"
        out.write_text(report, encoding="utf-8", errors="replace")
        return str(out)
    except Exception:
        return None

def execute_build(raw_build: str, branch: str | None = None,
                  target_override: str | None = None, lang: str = "en",
                  all_targets: bool = False) -> dict:
    """Shared build job used by BOTH the TUI and headless mode.
    Accepts a local path or git URL; emits progress through core.UI (so any
    bound sink receives live events). Never calls sys.exit.

    Returns {"exit_code", "status", "summary", "artifacts_dir", "build_folder"}."""
    core.set_lang(lang)
    commit = ""
    # --- resolve input ------------------------------------------------------
    if _is_git_url(raw_build):
        set_current_status(f"Cloning {raw_build[:60]}...")
        try:
            target, _used_branch, commit = _clone_to_cache(raw_build, branch)
            UI.success(f"Cloned ({_used_branch} @ {commit})")
        except Exception as e:
            UI.error(f"Clone failed: {e}")
            summary = _summarize("invalid_input", "clone_failed", raw_build, None,
                                 0.0, Path("."), {}, commit="")
            return {"exit_code": 2, "status": "invalid_input",
                    "summary": summary, "artifacts_dir": None, "build_folder": None}
    else:
        target = Path(raw_build)

    if not target.exists():
        UI.error(t('err_dir'))
        summary = _summarize("invalid_input", "path_missing", str(raw_build), None,
                             0.0, Path("."), {}, commit=commit)
        return {"exit_code": 2, "status": "invalid_input",
                "summary": summary, "artifacts_dir": None, "build_folder": None}

    artifacts_folder = Path("./EXECUTABLE")
    artifacts_folder.mkdir(exist_ok=True)

    # --- plan ---------------------------------------------------------------
    set_current_status("Scanning project...")
    targets = _scan_targets(target)
    if all_targets:
        names = sorted({x["name"] for x in targets})
        if not names:
            UI.error("No supported projects found in this repository.")
            summary = _summarize("invalid_input", "no_targets", str(raw_build),
                                 None, 0.0, Path("."), {}, commit=commit)
            return {"exit_code": 2, "status": "invalid_input",
                    "summary": summary, "artifacts_dir": None,
                    "build_folder": None}
        target_filter = ""   # engine walks and builds every detected project
        UI.log(UI.GREEN, t('build_selected'),
               f"ALL {len(names)} target(s): {', '.join(names)}")
    else:
        target_filter = target_override or _auto_select_target(target, targets)
        if target_filter:
            UI.log(UI.GREEN, t('build_selected'), target_filter)
        else:
            UI.log(UI.YELLOW, t('build_auto'), "")

    proj_cfg = load_project_config(target)
    if proj_cfg.get("target") and not target_override:
        target_filter = proj_cfg["target"]
        UI.log(UI.GREEN, t('build_selected'), f"{target_filter} (.justcompiler.json)")
    tests = load_config().get("run_tests", False) or bool(proj_cfg.get("run_tests"))
    if tests:
        UI.info(t('test_prompt') + " " + t('settings_on'))

    java_ver = proj_cfg.get("java_version") or _detect_java_version(target)
    if java_ver:
        UI.log(UI.GREEN, "Java", f"using version {java_ver}")

    extra_env = dict(proj_cfg.get("env", {}))
    avail_gb = _available_mem_gb()
    if avail_gb is not None:
        heap = max(2, min(12, int(avail_gb * 0.7)))
        extra_env.setdefault("JC_GRADLE_HEAP", str(heap))
        UI.log(UI.DIM, "", f"Gradle heap clamped to {heap}g (host available: {avail_gb:.1f}g)")

    base_image = load_config().get("base_image", "ubuntu:24.04")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    t0_run = time.time()
    project_name = target.resolve().name
    # cloned repos live in cache dirs like 'rich-<hash>': strip the hash suffix
    # (both for URL flows and when someone passes the cache path directly)
    if re.search(r"-[0-9a-f]{12}$", project_name):
        stripped = project_name.rsplit("-", 1)[0]
        if target.resolve().parent.name == "repos" or commit:
            project_name = stripped
    build_folder = artifacts_folder / f"{project_name}_{ts}"
    build_folder.mkdir(parents=True, exist_ok=True)

    sandbox_cfg = load_config()
    for k in ("profile", "network", "memory_limit", "cpu_limit"):
        if k in proj_cfg:
            key = {"network": "sandbox_network"}.get(k, k)
            sandbox_cfg[key] = proj_cfg[k]

    # --- run ----------------------------------------------------------------
    success = docker_manager.bootstrap_sandbox(
        target_path=target,
        artifacts_path=build_folder,
        run_tests=tests,
        lang=lang,
        set_status_fn=set_current_status,
        base_image=base_image,
        target_filter=target_filter,
        java_version=java_ver,
        extra_env=extra_env,
        project_name=project_name
    )

    elapsed = round(time.time() - t0_run, 1)
    try:
        manifest = json.loads((build_folder / "build_manifest.json").read_text())
    except Exception:
        manifest = {"projects": []}
    has_artifacts = bool(manifest.get("projects"))
    if success and has_artifacts:
        status = "success"
    elif not success and has_artifacts:
        status = "partial"
    else:
        status = "build_failed"
    summary = _summarize(status, "" if status == "success" else _error_class_from_log(build_folder),
                         target_filter, java_ver, elapsed, build_folder,
                         manifest, commit=commit)

    if status == "build_failed":
        rp = _write_failure_report(build_folder, summary)
        if rp:
            summary["failure_report"] = rp

    # Headless host-dep install (opt-in): only when deps are recorded AND
    # the user opted in via config. Always leaves a rollback receipt.
    cfg_pre = load_config()
    dep_names = summary.get("possible_runtime_deps", [])
    if cfg_pre.get("auto_install_deps") and dep_names:
        try:
            tuples = _manifest_runtime_dep_tuples(build_folder)
            pm = hostdeps.detect_pm()
            if tuples and pm:
                pkgs = []
                for dep in tuples:
                    field = {"apt": dep[1], "pacman": dep[2], "dnf": dep[3],
                             "zypper": dep[3], "winget": dep[4], "choco": dep[5],
                             "scoop": dep[6]}.get(pm, "")
                    if field:
                        pkgs.extend(w for w in field.split() if w not in pkgs)
                missing = hostdeps.filter_installed(pkgs, pm)
                if missing:
                    UI.info(f"Installing host dependencies: {', '.join(missing)}")
                    receipt = hostdeps.install(missing, pm,
                                               on_line=lambda l: print(f"  {l}"))
                    summary["installed_receipt"] = receipt.get("path", "")
                    summary["installed_packages"] = receipt.get("newly_installed", [])
        except Exception as e:
            UI.warn(f"Host dependency install skipped: {e}")

    try:
        (build_folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception:
        pass

    # optional retention: keep only the newest N build folders
    cfg_end = load_config()
    try:
        keep = int(cfg_end.get("keep_builds", 0) or 0)
    except Exception:
        keep = 0
    if keep > 0:
        removed = _clean_executables(artifacts_folder, keep=keep)
        if removed:
            UI.log(UI.DIM, "", f"Retention: removed {len(removed)} old build folder(s)")

    if cfg_end.get("notify", True):
        icon = {"success": "✅", "partial": "⚠️"}.get(status, "❌")
        _notify(f"JustCompiler {icon} {status}",
                f"{project_name} · {target_filter or 'all targets'} · {elapsed}s")

    return {"exit_code": {"success": 0, "partial": 3}.get(status, 1),
            "status": status, "summary": summary,
            "artifacts_dir": str(build_folder), "build_folder": build_folder}

def _available_mem_gb() -> float | None:
    """Best-effort host available memory in GB (Linux /proc/meminfo)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except Exception:
        pass
    return None

def _detect_java_version(root: Path) -> int | None:
    """Scan build files for the Java version the project targets.
    Priority: declared version > gradle wrapper cap > None."""
    found = []
    for dirpath, dirs, files in os.walk(str(root)):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in _WALK_SKIP_DIRS]
        for fname in files:
            if fname == ".java-version":
                try:
                    v = (Path(dirpath) / fname).read_text().strip().split()[0]
                    if v.isdigit() and 8 <= int(v) <= 30:
                        found.append(int(v))
                except Exception:
                    pass
                continue
            if fname not in _JAVA_BUILD_FILES:
                continue
            try:
                text = (Path(dirpath) / fname).read_text(errors="ignore")
            except Exception:
                continue
            for pat in JAVA_DECL_PATTERNS:
                m = re.search(pat, text)
                if m and 8 <= int(m.group(1)) <= 30:
                    found.append(int(m.group(1)))
    if found:
        return max(found)
    # No explicit version: cap by gradle wrapper's supported JVM
    wrapper = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if wrapper.exists():
        try:
            text = wrapper.read_text(errors="ignore")
            m = re.search(r"gradle-(\d+)(?:\.(\d+))?", text)
            if m:
                major = int(m.group(1))
                minor = int(m.group(2) or 0)
                if major >= 9 or (major == 8 and minor >= 5):
                    return 21
                return 17
        except Exception:
            pass
    return None

def _force_update(selected_lang):
    try:
        if _do_update(ask=False, force=True):
            UI.success("JustCompiler updated! Please restart.")
            sys.exit(0)
    except Exception as e:
        UI.error(f"Update failed: {e}")
        input(f"\n{UI.CYAN}{UI.BOLD}Press Enter to return...{UI.RESET}")
        return selected_lang
    UI.info(f"You are already on the latest version ({VERSION}).")
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
    if force and remote_version == VERSION:
        # Same version: re-copying files over a running process is pointless
        set_current_status(f"Already latest ({VERSION})")
        UI.warn(f"Force update skipped: {VERSION} is already installed.")
        return False
    if ask:
        UI.info(f"New version {remote_version} available (current: {VERSION})")
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
                UI.error("Checksum verification failed. Update aborted.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return False
        for file_name in UPDATE_FILES:
            src = temp_dir / file_name
            if src.exists():
                shutil.copy2(src, current_dir / file_name)
        shutil.rmtree(temp_dir, ignore_errors=True)
        UI.success(f"JustCompiler updated to {remote_version}! Please restart.")
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

def _clean_executables(artifacts_dir: Path, keep: int = 10) -> list:
    """Delete oldest entries in artifacts_dir, keeping the `keep` newest by mtime.
    Returns the list of removed paths."""
    if not artifacts_dir.is_dir():
        return []
    try:
        entries = sorted(artifacts_dir.iterdir(),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    stale = entries[keep:] if len(entries) > keep else []
    removed = []
    for p in stale:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
            removed.append(p)
        except Exception:
            pass
    return removed

def _list_docker_jc_volumes(docker_cmd: list) -> list:
    try:
        res = subprocess.run(docker_cmd + ["volume", "ls", "--format", "{{.Name}}"],
                             capture_output=True, text=True)
        return [l.strip() for l in res.stdout.splitlines() if l.strip().startswith("justcompiler-")]
    except Exception:
        return []

def _volumes_older_than(entries: list, days: int, now: datetime.datetime | None = None) -> list:
    """Filter [(name, created_str)] to volumes older than `days`.
    created_str is docker's RFC3339-ish CreatedAt ('2026-08-01T12:00:00Z' or with offset)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    old = []
    for name, created in entries:
        try:
            ts = datetime.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            if ts < cutoff:
                old.append(name)
        except Exception:
            continue
    return old

def handle_clean():
    """Reclaim disk space: old build folders, per-project volumes, dangling images."""
    keep = 10
    if "--keep" in sys.argv:
        try:
            keep = max(0, int(sys.argv[sys.argv.index("--keep") + 1]))
        except (IndexError, ValueError):
            pass
    artifacts_dir = Path("./EXECUTABLE")
    stale = _clean_executables(artifacts_dir, keep=keep)
    if stale:
        print(f"{UI.YELLOW}[CLEAN] {len(stale)} old build folder(s) in {artifacts_dir}:{UI.RESET}")
        for p in stale:
            print(f"  - {p.name}")
    else:
        print(f"{UI.GREEN}[OK] No build folders to remove (keeping newest {keep}).{UI.RESET}")

    docker_cmd = None
    if shutil.which("docker"):
        docker_cmd = ["docker"] if platform.system() == "Windows" else ["sudo", "docker"]
    if docker_cmd:
        volumes_old_days = None
        if "--volumes-old" in sys.argv:
            try:
                volumes_old_days = max(0, int(sys.argv[sys.argv.index("--volumes-old") + 1]))
            except (IndexError, ValueError):
                pass
        volumes = _list_docker_jc_volumes(docker_cmd)
        if volumes and volumes_old_days is not None:
            entries = []
            for v in volumes:
                insp = subprocess.run(docker_cmd + ["volume", "inspect", "-f", "{{.CreatedAt}}", v],
                                      capture_output=True, text=True)
                entries.append((v, insp.stdout.strip()))
            stale_vols = _volumes_older_than(entries, volumes_old_days)
            for v in stale_vols:
                subprocess.run(docker_cmd + ["volume", "rm", "-f", v],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{UI.GREEN}[OK] Removed {len(stale_vols)} volume(s) older than {volumes_old_days} day(s).{UI.RESET}")
            volumes = [v for v in volumes if v not in stale_vols]
        if volumes:
            print(f"{UI.YELLOW}[CLEAN] {len(volumes)} JustCompiler volume(s) found:{UI.RESET}")
            for v in volumes:
                print(f"  - {v}")
            confirm = input(f"{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Remove these volumes (cached build state)? (y/N): {UI.RESET}").strip().lower()
            if confirm in ("y", "yes"):
                for v in volumes:
                    subprocess.run(docker_cmd + ["volume", "rm", "-f", v],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"{UI.GREEN}[OK] Volumes removed.{UI.RESET}")
        else:
            print(f"{UI.GREEN}[OK] No JustCompiler volumes found.{UI.RESET}")
        subprocess.run(docker_cmd + ["image", "prune", "-f"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"{UI.GREEN}[OK] Dangling Docker images pruned.{UI.RESET}")
    else:
        print(f"{UI.YELLOW}[INFO] Docker not available; skipped volume/image cleanup.{UI.RESET}")
    sys.exit(0)

def handle_uninstall():
    UI.warn("Uninstalling JustCompiler...")
    confirm = input(f"{UI.CYAN}{UI.BOLD}Are you sure you want to uninstall JustCompiler? (y/n): {UI.RESET}").strip().lower()
    if confirm not in ['j', 'ja', 'y', 'yes']:
        sys.exit(0)
    _remove_alias()
    install_dir = Path.home() / ".justcompiler"
    if shutil.which("docker"):
        docker_cmd = ["docker"] if platform.system() == "Windows" else ["sudo", "docker"]
        get_images = subprocess.run(docker_cmd + ["images", "-q", "justcompiler-engine"], capture_output=True, text=True)
        if get_images.stdout.strip():
            for img_id in get_images.stdout.splitlines():
                subprocess.run(docker_cmd + ["rmi", "-f", img_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        get_base = subprocess.run(docker_cmd + ["images", "-q", "justcompiler-base"], capture_output=True, text=True)
        if get_base.stdout.strip():
            for img_id in get_base.stdout.splitlines():
                subprocess.run(docker_cmd + ["rmi", "-f", img_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if platform.system() == "Windows":
        cmd = f"Start-Sleep -s 1; Remove-Item -Recurse -Force '{install_dir}'"
        subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=0x08000000)
    else:
        cmd = f"sleep 1 && rm -rf '{install_dir}'"
        subprocess.Popen(["sh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    UI.success("JustCompiler has been completely uninstalled.")
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
    except Exception as e:
        UI.warn(f"Could not fetch default branch from {url}: {e}")

    try:
        heads_res = subprocess.run(["git", "ls-remote", "--heads", url], capture_output=True, text=True, env=env, timeout=4)
        if heads_res.returncode == 0:
            for line in heads_res.stdout.splitlines():
                if "\trefs/heads/" in line:
                    b_name = line.split("\trefs/heads/")[-1].strip()
                    if b_name not in all_branches:
                        all_branches.append(b_name)
    except Exception as e:
        UI.warn(f"Could not list remote branches from {url}: {e}")

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
        with UI.spinner("Querying remote Git repository for available branches..."):
            default_branch, other_branches = fetch_remote_git_info(url)
        
        set_current_status("Awaiting branch selection")
        show_tui_header()
        
        branch_lines = [f" [1] 🌟 Default ({default_branch})"]
        for idx, br in enumerate(other_branches, 2):
            branch_lines.append(f" [{idx}] 🌿 {br}")
        
        UI.draw_panel("Branch Selection", branch_lines, color=UI.CYAN)
            
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

    cache_dir = Path("./_git_cache").resolve() / url.split("/")[-1].replace(".git", "")
    allowed_base = Path("./_git_cache").resolve()

    if not str(cache_dir).startswith(str(allowed_base)):
        UI.error(f"Veiligheidsfout: cache-pad buiten _git_cache: {cache_dir}")
        sys.exit(1)

    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)

    clone_cmd = ["git", "clone", "--depth", "1", "-b", branch, url, str(cache_dir)]
    result = subprocess.run(clone_cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        UI.error(t('clone_fail'))
        UI.log(UI.RED, "", result.stderr.strip()[:500])
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
            UI.info("Language")
            print("  [1] English")
            print("  [2] Nederlands")
            sys.stdout.flush()
            c = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Choice [1-2]: {UI.RESET}").strip()
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

def _detect_package_manager():
    """Delegated to hostdeps.detect_pm(); keeps the legacy tuple contract."""
    pm = hostdeps.detect_pm()
    return (pm, None) if pm else (None, None)

def _filter_installed(pkgs: list, pm_family: str) -> list:
    """Delegated to hostdeps.filter_installed()."""
    return hostdeps.filter_installed(pkgs, pm_family)

def _build_install_cmds(deps: set, pm_family: str) -> list:
    """Delegated to hostdeps: dep-tuples -> flat pkgs -> install argv(s)."""
    pkgs = []
    for pkg, apt, pacman, dnf, winget, choco, scoop in sorted(deps):
        field = {"apt": apt, "pacman": pacman, "dnf": dnf, "zypper": dnf,
                 "winget": winget, "choco": choco, "scoop": scoop}.get(pm_family, "")
        if field:
            pkgs.extend(w for w in field.split() if w not in pkgs)
    return hostdeps.build_install_cmds_from_pkgs(pkgs, pm_family)

def _norm_token(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())

_ERROR_TOKEN_EXPAND = {
    "gi": ["pygobject", "gir", "glib"],
}

def _dep_tokens(dep: tuple) -> set:
    _, apt, pacman, dnf, winget, choco, scoop = dep
    words = [dep[0]]
    for f in (apt, pacman, dnf, winget, choco, scoop):
        words.extend(f.split())
    toks = set()
    for w in words:
        n = _norm_token(w)
        if n:
            toks.add(n)
            if n.startswith("lib") and len(n) > 3:
                toks.add(n[3:])
    return toks

def _extract_error_tokens(text: str) -> set:
    toks = set()

    def add(s):
        n = _norm_token(s)
        if n:
            toks.add(n)
            toks.update(_ERROR_TOKEN_EXPAND.get(n, []))

    for m in re.finditer(r"No module named '([\w.]+)'", text):
        add(m.group(1))
    for m in re.finditer(r"ImportError: cannot import name '[\w.]+' from '([\w.]+)'", text):
        add(m.group(1))
    for m in re.finditer(r"Namespace (\w+) not available", text):
        add(m.group(1))
    for m in re.finditer(r"error while loading shared libraries: ([\w.+-]+)\.so", text):
        add(m.group(1))
    for m in re.finditer(r"(?:ImportError|OSError):\s*(?:lib)?([\w+-]+)[\w.]*(?:\.so[\w.]*)?.{0,80}(?:cannot open shared object file|No such file)", text):
        add(m.group(1))
    for m in re.finditer(r"([\w./-]+): command not found", text):
        add(Path(m.group(1)).name)
    for m in re.finditer(r"'([\w.-]+)' is not recognized", text):
        add(m.group(1))
    for m in re.finditer(r"(?:Cannot find|Could not find) module[^'\n]*'([\w.-]+)'", text):
        add(m.group(1))
    if "Unsupported class file major version" in text or "has been compiled by a more recent version" in text:
        add("java")
    return toks

def _match_error_to_deps(text: str, deps: set) -> list:
    """Return the deps whose tokens match error output tokens."""
    err_toks = _extract_error_tokens(text)
    matched = []
    for dep in sorted(deps):
        dtoks = _dep_tokens(dep)
        for et in err_toks:
            for dt in dtoks:
                if et == dt or (len(et) >= 3 and et in dt) or (len(dt) >= 4 and dt in et):
                    matched.append(dep)
                    break
            else:
                continue
            break
    return matched

def _show_runtime_hints(build_folder: Path, output_text: str = ""):
    manifest_file = build_folder / "build_manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text())
    except Exception:
        return
    deps = set()
    for proj in manifest.get("projects", []):
        for dep in proj.get("runtime_deps", []):
            deps.add((
                dep["pkg"],
                dep.get("apt", ""),
                dep.get("pacman", ""),
                dep.get("dnf", ""),
                dep.get("winget", ""),
                dep.get("choco", ""),
                dep.get("scoop", ""),
            ))
    if not deps:
        return
    matched = _match_error_to_deps(output_text, deps) if output_text else []
    shown, source_label = (matched, "matched from error output") if matched else (sorted(deps), "")
    pm_family, _ = _detect_package_manager()
    title = "Possible missing runtime dependencies"
    if matched:
        title += " (matched from error output)"
    lines = []
    for pkg, apt, pacman, dnf, winget, choco, scoop in shown:
        if not pkg:
            continue
        entry = [f"• {pkg}"]
        if apt: entry.append(f"  Debian/Ubuntu : sudo apt install {apt}")
        if pacman: entry.append(f"  Arch          : sudo pacman -S {pacman}")
        if dnf: entry.append(f"  Fedora        : sudo dnf install {dnf}")
        if winget: entry.append(f"  Windows       : winget install {winget}")
        if choco: entry.append(f"  Windows       : choco install {choco}")
        if scoop: entry.append(f"  Windows       : scoop install {scoop}")
        lines.extend(entry)
    if hasattr(UI, 'draw_panel'):
        UI.draw_panel(title, lines, color=UI.YELLOW)
    else:
        UI.warn(title)
        for l in lines: print(f"  {l}")
    if pm_family:
        gate = load_config().get("host_dep_install", "ask")
        if gate == "never":
            UI.warn("Host dependency install is disabled (config: host_dep_install=never).")
            return
        ans = "y" if gate == "always" else input(
            f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Install missing dependencies automatically? (y/N): {UI.RESET}"
        ).strip().lower()
        if ans in ('y', 'yes'):
            dep_set = set(matched) if matched else deps
            pkgs = []
            for dep in dep_set:
                field = {"apt": dep[1], "pacman": dep[2], "dnf": dep[3],
                         "zypper": dep[3], "winget": dep[4], "choco": dep[5],
                         "scoop": dep[6]}.get(pm_family, "")
                if field:
                    pkgs.extend(w for w in field.split() if w not in pkgs)
            missing = hostdeps.filter_installed(pkgs, pm_family)
            if not missing:
                UI.success(f"{', '.join(pkgs)} already installed, nothing to do")
                return
            receipt = hostdeps.install(missing, pm_family,
                                       on_line=lambda l: print(f"  {l}"))
            if receipt.get("ok"):
                UI.success(f"Installed {len(receipt['newly_installed'])} package(s). "
                           f"Receipt: {receipt.get('path')}")
            else:
                UI.error("Install failed — see output above.")
                return
            # offer immediate rollback so users can verify and revert safely
            roll = input(f"{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Undo this install now? (y/N): {UI.RESET}"
                         ).strip().lower()
            if roll in ('y', 'yes'):
                ok = hostdeps.undo(receipt, on_line=lambda l: print(f"  {l}"))
                UI.success("Rolled back.") if ok else UI.error("Rollback failed — see output above.")


import zipfile
from collections import namedtuple

ArtifactInfo = namedtuple("ArtifactInfo", ["name", "kind", "cmd", "cwd", "size", "desc", "is_main"])


def _size_str(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / 1024 ** 2:.1f} MB"


def _classify_jar(path: Path) -> str:
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


def _scan_artifacts(folder: Path) -> list[ArtifactInfo]:
    """Recursively scan build folder for runnable artifacts with metadata."""
    found: list[ArtifactInfo] = []
    is_windows = platform.system() == "Windows"
    is_macos = platform.system() == "Darwin"
    source_dirs = [d for d in folder.iterdir() if d.is_dir() and d.name.endswith("_source")]
    skipped_dirs = {"node_modules", "__pycache__", ".git", "venv", ".venv", "_source"}

    for root, dirs, files in os.walk(str(folder)):
        root_p = Path(root)
        # skip hidden dirs, noise dirs, and <proj>_source mirrors (root copies are canonical)
        dirs[:] = [d for d in dirs
                   if not d.startswith(".") and d not in skipped_dirs and not d.endswith("_source")]

        for fname in files:
            fpath = root_p / fname
            if not fpath.is_file():
                continue
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            if size == 0:
                continue

            low = fname.lower()
            kind, cmd, cwd = None, None, None
            desc = ""

            # --- JAR ---
            if fpath.suffix == ".jar":
                kind = _classify_jar(fpath)
                cmd = ["java", "-jar", str(fpath)]
                desc = f"JAR \u2014 {_size_str(size)}"

            # --- Python ---
            elif fpath.suffix == ".py":
                py_cmd = "python" if is_windows else "python3"
                src_name, src_cwd = _matching_source_dir(fpath, source_dirs)
                cmd = [py_cmd, src_name] if src_name else [py_cmd, str(fpath)]
                cwd = src_cwd
                kind = "python"
                desc = f"Python \u2014 {_size_str(size)}"

            # --- JavaScript / TypeScript ---
            elif fpath.suffix in (".js", ".mjs"):
                cmd = ["node", str(fpath)]
                kind = "node"
                desc = f"Node.js \u2014 {_size_str(size)}"
            elif fpath.suffix == ".ts" and not fname.endswith(".d.ts"):
                cmd = ["node", "--loader", "ts-node/esm", str(fpath)]
                kind = "node"
                desc = f"TypeScript \u2014 {_size_str(size)}"

            # --- Windows ---
            elif is_windows and fpath.suffix in (".exe", ".bat", ".cmd"):
                cmd = [str(fpath)]
                kind = "executable"
                desc = f"Windows executable \u2014 {_size_str(size)}"

            # --- macOS ---
            elif is_macos:
                magic = _read_magic(fpath)
                if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                             b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                             b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"):
                    cmd = [str(fpath)]
                    kind = "binary"
                    desc = f"Mach-O binary \u2014 {_size_str(size)}"
                elif fpath.suffix in (".sh", ".bash"):
                    cmd = ["bash", str(fpath)]
                    kind = "script"
                    desc = f"Shell script \u2014 {_size_str(size)}"

            # --- Linux / Unix ---
            elif not is_windows and not is_macos:
                magic = _read_magic(fpath)
                if magic == b"\x7fELF":
                    cmd = [str(fpath)]
                    kind = "binary"
                    desc = f"ELF binary \u2014 {_size_str(size)}"
                elif fpath.suffix in (".sh", ".bash"):
                    src_name, src_cwd = _matching_source_dir(fpath, source_dirs)
                    cmd = ["bash", src_name] if src_name else ["bash", str(fpath)]
                    cwd = src_cwd
                    kind = "script"
                    desc = f"Shell script \u2014 {_size_str(size)}"
                elif fpath.suffix == ".py":
                    py_cmd = "python" if is_windows else "python3"
                    src_name, src_cwd = _matching_source_dir(fpath, source_dirs)
                    cmd = [py_cmd, src_name] if src_name else [py_cmd, str(fpath)]
                    cwd = src_cwd
                    kind = "python"
                    desc = f"Python \u2014 {_size_str(size)}"
                elif not fpath.suffix:
                    head = _read_head(fpath, 64)
                    if head and head.startswith(b"#!"):
                        interp = _shebang_interpreter(head)
                        src_name, src_cwd = _matching_source_dir(fpath, source_dirs)
                        if src_name:
                            cmd = [interp or "bash", src_name]
                            cwd = src_cwd
                        else:
                            cmd = [str(fpath)]
                        kind = "script"
                        desc = f"Shebang script ({interp or 'sh'}) \u2014 {_size_str(size)}"

            if cmd is None:
                continue

            is_main = _is_main_artifact(fname, kind, size)
            found.append(ArtifactInfo(
                name=str(Path(fpath).relative_to(folder)),
                kind=kind,
                cmd=cmd,
                cwd=cwd,
                size=size,
                desc=desc,
                is_main=is_main,
            ))

    found.sort(key=lambda a: (not a.is_main, -a.size, a.name))
    return found


def _read_magic(path: Path, n_bytes: int = 4) -> bytes:
    try:
        return path.read_bytes()[:n_bytes]
    except Exception:
        return b""


def _read_head(path: Path, n_bytes: int) -> bytes | None:
    try:
        return path.read_bytes()[:n_bytes]
    except Exception:
        return None


def _shebang_interpreter(head: bytes) -> str | None:
    try:
        text = head.decode("utf-8", errors="replace")
        if "bash" in text[:32]:
            return "bash"
        if "python" in text[:32]:
            return "python3"
        if "node" in text[:32]:
            return "node"
        if "ruby" in text[:32]:
            return "ruby"
        if "perl" in text[:32]:
            return "perl"
        if "lua" in text[:32]:
            return "lua"
        return None
    except Exception:
        return None


def _matching_source_dir(script_file: Path, source_dirs: list) -> tuple:
    """Find source dir that matches this script's project prefix."""
    stem = script_file.name
    for sd in source_dirs:
        prefix = sd.name.replace("_source", "")
        if stem.startswith(prefix + "_"):
            orig_name = stem[len(prefix) + 1:]
            return (orig_name, str(sd))
    return (None, None)


def _is_main_artifact(name: str, kind: str, size: int) -> bool:
    """Determine whether an artifact is a primary executable the user cares about."""
    score = 0
    low = name.lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    main_keywords = ["main", "app", "game", "client", "server", "launcher",
                     "start", "gui", "run", "program", "release", "binary", "exec"]

    if kind in ("binary", "executable", "mod", "plugin", "bungee-plugin", "velocity-plugin"):
        score += 30
    if kind in ("mod", "plugin", "bungee-plugin", "velocity-plugin"):
        score += 20
    if size > 1024 * 1024:
        score += 15
    elif size > 100 * 1024:
        score += 5
    if any(kw in low for kw in main_keywords):
        score += 20
    if any(p in low for p in ["-sources", "-javadoc", "-doc", "-dev", "-devel",
                               "-static", "-dbg", "test_", "example", "sample",
                               "-unshaded", "-slim"]):
        score -= 50
    # CMake/Go/cargo test-suite binaries (fmt_args-test, foo.test, x.test.js…)
    if low.endswith(("-test", ".test", "_test") ) or low.startswith("test-"):
        score -= 50
    if kind in ("jar",) and score < 20:
        score -= 10
    if low.startswith("lib") and kind != "mod":
        score -= 20

    return score >= 20


def _show_artifact_selection(artifacts: list[ArtifactInfo]) -> ArtifactInfo | None:
    """Show user a menu of artifacts grouped by type."""
    mains = [a for a in artifacts if a.is_main]
    others = [a for a in artifacts if not a.is_main]

    lines = []
    idx = 1
    menu_map: dict[int, ArtifactInfo] = {}

    if mains:
        lines.append(f"  {UI.BOLD}{UI.GREEN}{t('artifact_main')}:{UI.RESET}")
        for a in mains:
            lines.append(f" [{idx}] {a.name}  {UI.DIM}({a.desc}, {a.kind}){UI.RESET}")
            menu_map[idx] = a
            idx += 1
        lines.append("")

    if others:
        lines.append(f"  {UI.BOLD}{UI.YELLOW}{t('artifact_other')}:{UI.RESET}")
        for a in others:
            lines.append(f" [{idx}] {a.name}  {UI.DIM}({a.desc}, {a.kind}){UI.RESET}")
            menu_map[idx] = a
            idx += 1
        lines.append("")

    lines.append(f"  {UI.DIM}[{idx}] {t('artifact_skip')}{UI.RESET}")

    UI.draw_panel(t('artifact_header'), lines, color=UI.CYAN)

    sys.stdout.flush()
    choice = input(f"\n{UI.CYAN}{UI.BOLD}\u279e {UI.RESET}{t('artifact_enter_choice')}{UI.RESET}").strip()

    if choice.isdigit():
        num = int(choice)
        if num in menu_map:
            return menu_map[num]

    return None

if __name__ == "__main__":
    init_terminal_colors()

    if len(sys.argv) > 1:
        if sys.argv[1].lower() == "uninstall":
            handle_uninstall()
        if sys.argv[1].lower() == "clean":
            handle_clean()
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
            UI.info("Select interface language / Kies taal")
            print("  [1] English (Default)")
            print("  [2] Nederlands")
            sys.stdout.flush()
            lang_choice = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}Choice [1-2]: {UI.RESET}").strip()
            selected_lang = "nl" if lang_choice == "2" else "en"
            save_config(lang=selected_lang)

    core.set_lang(selected_lang)
    show_tui_header()
    check_for_updates()

    # Headless mode: --build PATH|URL [--target NAME] [--branch B]
    raw_build = None
    target_override = None
    branch_arg = None
    all_targets_arg = False
    for i, arg in enumerate(sys.argv):
        if arg == "--build" and i + 1 < len(sys.argv):
            raw_build = sys.argv[i + 1]      # keep raw: Path() mangles 'https://'
        elif arg == "--target" and i + 1 < len(sys.argv):
            target_override = sys.argv[i + 1]
        elif arg == "--branch" and i + 1 < len(sys.argv):
            branch_arg = sys.argv[i + 1]
        elif arg == "--all-targets":
            all_targets_arg = True

    if raw_build:
        result = execute_build(raw_build, branch=branch_arg,
                               target_override=target_override, lang=selected_lang,
                               all_targets=all_targets_arg)
        print(json.dumps(result["summary"], indent=2))
        sys.exit(result["exit_code"])

    # Interactive mode: prefer the Textual TUI, fall back to the legacy
    # ANSI flow when textual isn't installed or stdout isn't a terminal.
    try:
        import tui as tui_mod
        if tui_mod.should_use_textual():
            sys.exit(tui_mod.launch_tui())
    except SystemExit:
        raise
    except Exception:
        pass
    if sys.stdout.isatty():
        try:
            import importlib.util as _ilu
            if _ilu.find_spec("textual") is None:
                UI.warn("Tip: install 'pip install --user textual' for the new full-screen TUI.")
        except Exception:
            pass

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

        # Interactive build: same shared job as headless/TUI, console events
        result = execute_build(str(target), target_override=None, lang=selected_lang)
        success = result["status"] in ("success", "partial")
        build_folder = Path(result["artifacts_dir"]) if result["artifacts_dir"] else None

        sys.stdout.flush()
        if success and build_folder and any(build_folder.iterdir()):
            artifacts = _scan_artifacts(build_folder)
            if artifacts:
                selected = _show_artifact_selection(artifacts)
                if selected:
                    cmd = list(selected.cmd)
                    cwd = selected.cwd
                    UI.info(f"Starting: {selected.name} ({selected.kind})")
                    UI.log(UI.DIM, "", f"$ {' '.join(cmd)}")
                    args = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{t('artifact_args')}{UI.RESET}").strip()
                    if args:
                        cmd.extend(args.split())
                    run_log = []
                    proc = subprocess.Popen(cmd, shell=platform.system() == "Windows", cwd=cwd,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, bufsize=1, errors="replace")
                    try:
                        for line in proc.stdout:
                            print(line, end="")
                            run_log.append(line)
                        proc.wait()
                    except KeyboardInterrupt:
                        proc.kill()
                        raise
                    try:
                        (build_folder / "run.log").write_text("".join(run_log), encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                    if proc.returncode != 0:
                        _show_runtime_hints(build_folder, "".join(run_log))
            ans = input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{t('open_folder')} ").strip().lower()
            if ans in ['j', 'ja', 'y', 'yes']:
                if platform.system() == "Windows":
                    subprocess.Popen(["explorer", str(build_folder.resolve())])
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", str(build_folder.resolve())])
                else:
                    subprocess.Popen(["xdg-open", str(build_folder.resolve())])
        input(f"\n{UI.CYAN}{UI.BOLD}➔ {UI.RESET}{UI.DIM}{t('press_enter')}{UI.RESET}")
