"""Host dependency install/undo with receipts (v2.4.0).

Every host install performed by JustCompiler writes a receipt so the exact
change can be reviewed and rolled back. Rollback removes ONLY packages that
were newly installed by that receipt — never anything that was already there.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RECEIPT_DIR = Path.home() / ".justcompiler" / "install_receipts"


# ------------------------------------------------------------- detection

def detect_pm():
    """Return the host package manager family name or None."""
    if sys.platform == "win32":
        for pm in ("winget", "choco", "scoop"):
            if shutil.which(pm):
                return pm
        return None
    for pm in ("apt", "pacman", "dnf", "zypper"):
        if shutil.which(pm):
            return pm
    return None


def filter_installed(pkgs, pm_family, runner=None):
    """Return only the packages NOT installed yet (Linux managers only)."""
    if not pkgs or sys.platform == "win32":
        return list(pkgs)
    checker = {"apt": ["dpkg", "-s"], "pacman": ["pacman", "-Q"],
               "dnf": ["rpm", "-q"], "zypper": ["rpm", "-q"]}.get(pm_family)
    if not checker:
        return list(pkgs)
    run = runner or (lambda argv: subprocess.run(argv, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL))
    missing = []
    for p in pkgs:
        try:
            if run(checker + [p]).returncode != 0:
                missing.append(p)
        except Exception:
            missing.append(p)
    return missing


def _base_cmd(pm_family: str, pkgs: list) -> list:
    sudo = [] if sys.platform == "win32" else ["sudo"]
    table = {
        "apt": ["apt-get", "install", "-y"],
        "pacman": ["pacman", "-S", "--noconfirm"],
        "dnf": ["dnf", "install", "-y"],
        "zypper": ["zypper", "install", "-y"],
        "choco": ["choco", "install", "-y"],
        "scoop": ["scoop", "install"],
    }
    if pm_family == "winget":
        out = []
        for p in pkgs:
            out.append(["winget", "install", "--id", p, "-e",
                        "--accept-package-agreements", "--accept-source-agreements"])
        return out
    return [sudo + table[pm_family] + list(pkgs)]


def build_install_cmds_from_pkgs(pkgs: list, pm_family: str) -> list:
    """One combined command per manager (winget: one per package)."""
    pkgs = [p for p in pkgs if p]
    if not pkgs:
        return []
    return _base_cmd(pm_family, pkgs)


# ------------------------------------------------------------ snapshots

def _installed_set(pm_family, runner=None):
    """Full set of installed package names (Linux only; empty elsewhere)."""
    argv = {"apt": ["dpkg", "-l"],
            "pacman": ["pacman", "-Qq"],
            "dnf": ["rpm", "-qa", "--qf", "%{NAME}\\n"],
            "zypper": ["rpm", "-qa", "--qf", "%{NAME}\\n"]}.get(pm_family)
    if not argv:
        return set()
    try:
        run = runner or (lambda a, **k: subprocess.run(a, capture_output=True,
                                                       text=True, timeout=30))
        res = run(argv, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return set()
        names = set()
        for ln in res.stdout.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if pm_family == "apt":
                # dpkg -l: "ii  name  version ..."
                parts = ln.split()
                if len(parts) >= 2 and parts[0] in ("ii", "hi"):
                    names.add(parts[1])
            else:
                names.add(ln)
        return names
    except Exception:
        return set()


def _default_runner(on_line=None):
    def run(argv, **kw):
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1, errors="replace")
        for line in proc.stdout:
            if on_line:
                on_line(line.rstrip("\n"))
        proc.wait()
        class R:
            returncode = proc.returncode
            stdout = ""
        return R()
    return run


def _dnf_last_transaction_id(output_text: str) -> int | None:
    m = re.findall(r"^(\d+)\s+\S.*$", output_text, re.M)
    return int(m[-1]) if m else None


def _probe_dnf_tid(runner=None) -> int | None:
    """Ask `dnf history` for the newest transaction id."""
    try:
        if runner:
            R = runner(["dnf", "history"])
            text = getattr(R, "stdout", "") or ""
        else:
            R = subprocess.run(["dnf", "history"], capture_output=True,
                               text=True, timeout=30)
            text = R.stdout or ""
        return _dnf_last_transaction_id(text)
    except Exception:
        return None


# --------------------------------------------------------------- install

def install(pkgs: list, pm_family: str, *, runner=None, on_line=None) -> dict:
    """Install `pkgs`, write a receipt, return it.

    pkgs must already be filtered to what's missing. Never raises for
    expected failures; check receipt['ok']."""
    pkgs = [p for p in pkgs if p]
    receipt = {
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pm": pm_family,
        "requested": list(pkgs),
        "newly_installed": [],
        "transaction_id": None,
        "command": "",
        "ok": False,
        "path": "",
    }
    if not pkgs or not pm_family:
        receipt["command"] = "# nothing to do"
        return _save_receipt(receipt)

    before = _installed_set(pm_family, runner)
    cmds = build_install_cmds_from_pkgs(pkgs, pm_family)
    receipt["command"] = " && ".join(" ".join(c) for c in cmds)

    run = runner or _default_runner(on_line)
    all_out = []
    ok = True
    for cmd in cmds:
        out_box = []

        def line_sink(l, _box=out_box, _all=all_out):
            _box.append(l)
            _all.append(l)
            if on_line:
                on_line(l)

        res = run(cmd, on_line=line_sink)
        rc = getattr(res, "returncode", 1)
        if rc != 0:
            ok = False
            break
        all_out.extend(out_box)

    after = _installed_set(pm_family, runner)
    newly = sorted((after - before)) if before or after else []
    if not newly and pm_family in ("winget", "choco", "scoop"):
        newly = list(pkgs)          # best effort on Windows

    if pm_family == "dnf":
        receipt["transaction_id"] = _dnf_last_transaction_id("\n".join(all_out)) \
            or _probe_dnf_tid(runner)

    receipt["newly_installed"] = newly
    receipt["ok"] = ok
    return _save_receipt(receipt)


def _save_receipt(receipt: dict) -> dict:
    try:
        RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        p = RECEIPT_DIR / f"{stamp}-{receipt['pm']}.json"
        # avoid collision within the same second
        i = 1
        while p.exists():
            p = RECEIPT_DIR / f"{stamp}-{receipt['pm']}-{i}.json"
            i += 1
        receipt["path"] = str(p)          # store BEFORE serialising
        p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    except Exception:
        pass
    return receipt


# ------------------------------------------------------------------ undo

def undo_argv(receipt: dict) -> list:
    """Exact removal command for a receipt (pure function, testable)."""
    pm = receipt.get("pm")
    sudo = [] if sys.platform == "win32" else ["sudo"]
    newly = receipt.get("newly_installed") or []
    requested = receipt.get("requested") or []
    if pm == "dnf" and receipt.get("transaction_id"):
        return sudo + ["dnf", "history", "undo",
                       str(receipt["transaction_id"]), "-y"]
    targets = newly or requested
    if not targets:
        return []
    if pm == "apt":
        return sudo + ["apt-get", "remove", "-y"] + targets
    if pm == "pacman":
        return sudo + ["pacman", "-Rs", "--noconfirm"] + targets
    if pm == "dnf":
        return sudo + ["dnf", "remove", "-y"] + targets
    if pm == "zypper":
        return sudo + ["zypper", "remove", "-y"] + targets
    if pm == "winget":
        return [["winget", "uninstall", "--id", t, "-e"] for t in targets]
    if pm == "choco":
        return ["choco", "uninstall", "-y"] + targets
    if pm == "scoop":
        return ["scoop", "uninstall"] + targets
    return []


def undo(receipt: dict, *, runner=None, on_line=None) -> bool:
    argv = undo_argv(receipt)
    if not argv:
        return False
    if isinstance(argv[0], list):          # per-package commands (winget)
        run = runner or _default_runner(on_line)
        ok = True
        for cmd in argv:
            if run(cmd, on_line=on_line).returncode != 0:
                ok = False
        return ok
    run = runner or _default_runner(on_line)
    return run(argv, on_line=on_line).returncode == 0


def load_receipts(limit: int = 5) -> list:
    """Newest-first receipts (skips unreadable ones)."""
    try:
        RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(RECEIPT_DIR.glob("*.json"), reverse=True)[:limit * 2]
        out = []
        for f in files:
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
                r.setdefault("path", str(f))
                out.append(r)
            except Exception:
                continue
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []
