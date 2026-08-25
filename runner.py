"""Build orchestration (extracted from justcompiler.py, v2.12.0).

`execute_build` is the shared build job used by the TUI, headless CLI and
the Engine API daemon. Embed it in-process:

    import core
    from runner import execute_build
    core.UI.bind(your_sink)               # optional progress events
    result = execute_build("https://github.com/user/repo")

TUI-free by design: only core/docker_manager/hostdeps/scanner/jcconfig.
"""
import datetime
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import core
from core import UI, t, VERSION, set_current_status
from jcconfig import load_config
import docker_manager
import hostdeps
from scanner import (_scan_targets, _auto_select_target, load_project_config,
                     _detect_java_version)


# ------------------------------------------------------------- git cache --

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


# ------------------------------------------------------------ summaries ---

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


def _clean_project_name(folder_name: str) -> str:
    """'mangojuice_20260824_005131' -> 'mangojuice' (strip ts suffix)."""
    return re.sub(r"_\d{8}_\d{6}$", "", folder_name)


# -------------------------------------------------- notify / clipboard ----

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


# ---------------------------------------------------------------- memory --

def _available_mem_gb() -> float | None:
    """Best-effort host available memory in GB (Linux /proc/meminfo)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1048576
    except Exception:
        pass
    return None


# -------------------------------------------------------------- retention -

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


# ------------------------------------------------------------ the build ---

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
