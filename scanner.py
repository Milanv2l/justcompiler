"""Project/target detection (extracted from justcompiler.py, v2.12.0).

Scans a repository for buildable targets via plugins.json, classifies
platforms/modloaders, auto-selects the best target, reads .justcompiler.json
overrides and detects the Java toolchain version. TUI-free by design.
"""
import json
import os
import re
from pathlib import Path

from core import UI

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


def _plugins_path() -> Path:
    return Path(__file__).resolve().parent / "plugins.json"


def _load_custom_plugins() -> list:
    """Load user-defined plugins from ~/.justcompiler/plugins.d/*.json."""
    pdir = Path.home() / ".justcompiler" / "plugins.d"
    if not pdir.is_dir():
        return []
    extra = []
    for f in sorted(pdir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                extra.extend(data)
            elif isinstance(data, dict):
                extra.append(data)
        except Exception:
            pass
    return extra


def _scan_targets(root: Path) -> list:
    """Walk project and return detected build targets with platform/modloader info."""
    targets = []
    seen_plugins = set()
    plugins_path = _plugins_path()
    if not plugins_path.exists():
        return targets
    try:
        plugins = json.loads(plugins_path.read_text())
    except Exception:
        return targets
    plugins.extend(_load_custom_plugins())

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
        pdata = json.loads(_plugins_path().read_text())
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
            names = {pl["name"] for pl in json.loads(_plugins_path().read_text())}
            if cfg["target"] not in names:
                UI.warn(f".justcompiler.json target '{cfg['target']}' is not a known plugin; ignoring")
                cfg.pop("target")
        except Exception:
            pass
    if "env" in cfg and not isinstance(cfg["env"], dict):
        UI.warn(".justcompiler.json env must be an object; ignoring")
        cfg.pop("env")
    return cfg


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
