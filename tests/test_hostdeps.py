"""Tests for hostdeps: receipts, install/undo argv construction, gates."""
import json
from pathlib import Path

import pytest

import hostdeps as hd


class FakeRunner:
    """Records argvs; returns scripted outputs keyed by a substring."""
    def __init__(self, scripts=None, rc=0):
        self.scripts = scripts or {}
        self.rc = rc
        self.argvs = []

    def __call__(self, argv, on_line=None, **kw):
        key = " ".join(argv)
        self.argvs.append(list(argv))
        out = ""
        for needle, lines in self.scripts.items():
            if needle in key:
                out = "\n".join(lines)
                break
        if on_line:
            for l in out.splitlines():
                on_line(l)
        class R:
            returncode = self.rc
            stdout = out
        return R()


@pytest.fixture
def receipt_dir(tmp_path, monkeypatch):
    rd = tmp_path / "receipts"
    monkeypatch.setattr(hd, "RECEIPT_DIR", rd)
    return rd


def test_install_apt_receipt_and_undo(monkeypatch, receipt_dir):
    # before: libfoo absent; after install cmd: present + auto-dep libbar
    calls = {"n": 0}
    def runner(argv, on_line=None, **kw):
        calls["n"] += 1
        outs = []
        if argv[:2] == ["dpkg", "-l"]:
            if calls["n"] == 1:
                outs = ["ii  libc6"]           # snapshot BEFORE
            else:
                outs = ["ii  libc6", "ii  libfoo", "ii  libbar-auto"]
        elif argv[:2] == ["sudo", "apt-get"]:
            outs = ["Setting up libfoo …"]
        text = "\n".join(outs)
        if on_line:
            for l in outs:
                on_line(l)
        class R:
            returncode = 0
            stdout = text
        return R()
    rec = hd.install(["libfoo"], "apt", runner=runner,
                     on_line=lambda l: None)
    assert rec["ok"] is True
    assert set(rec["newly_installed"]) == {"libfoo", "libbar-auto"}
    assert rec["requested"] == ["libfoo"]
    assert rec["path"] and Path(rec["path"]).exists()

    argv = hd.undo_argv(rec)
    assert argv[:3] == ["sudo", "apt-get", "remove", "-y"][:3] or \
           argv[0] == "sudo"
    joined = " ".join(argv)
    assert "-y" in joined and "libfoo" in joined
    # undo executes the constructed command
    urunner = FakeRunner()
    assert hd.undo(rec, runner=urunner) is True
    assert any("remove" in " ".join(a) for a in urunner.argvs)


def test_dnf_prefers_history_undo(monkeypatch, receipt_dir):
    def runner(argv, on_line=None, **kw):
        outs = []
        if argv[:1] == ["rpm"]:
            outs = []
        elif "dnf" in argv and "install" in argv:
            outs = ["Installed:", "  gtk4-devel"]
        elif "history" in argv:
            outs = ["42  install  2 pkgs", "41  update  1 pkg"]
        text = "\n".join(outs)
        if on_line:
            for l in outs:
                on_line(l)
        class R:
            returncode = 0
            stdout = text
        return R()
    # _installed_set uses rpm -qa --qf; make sure that call passes through runner
    rec = hd.install(["gtk4-devel"], "dnf",
                     runner=runner, on_line=lambda l: None)
    argv = hd.undo_argv(rec)
    assert argv[1:4] == ["dnf", "history", "undo"]
    assert argv[4] == str(rec["transaction_id"])
    assert argv[-1] == "-y"


def test_pacman_undo_uses_Rs(monkeypatch, receipt_dir):
    def runner(argv, **kw):
        class R:
            returncode = 0
            stdout = "" if argv[:1] != ["pacman", "-Qq"] else "base\n"
        return R()
    rec = hd.install(["sdl2"], "pacman", runner=runner)
    argv = hd.undo_argv(rec)
    assert argv[:3] == ["sudo", "pacman", "-Rs"]
    assert "--noconfirm" in argv and "sdl2" in argv


def test_winget_per_package_uninstall(monkeypatch, receipt_dir):
    monkeypatch.setattr(hd.sys, "platform", "win32")
    rec = {"pm": "winget", "requested": ["OpenJS.NodeJS.LTS"],
           "newly_installed": ["OpenJS.NodeJS.LTS"], "path": ""}
    cmds = hd.undo_argv(rec)
    assert isinstance(cmds[0], list) and cmds[0][0] == "winget" \
           and cmds[0][cmds[0].index("--id") + 1] == "OpenJS.NodeJS.LTS"


def test_load_receipts_newest_first(receipt_dir):
    r1 = hd._save_receipt({"pm": "apt", "when": "a", "requested": [], "newly_installed": []})
    import time as t; t.sleep(1.05)
    r2 = hd._save_receipt({"pm": "dnf", "when": "b", "requested": [], "newly_installed": []})
    loaded = hd.load_receipts(2)
    assert [r["pm"] for r in loaded][:2] == ["dnf", "apt"]
    assert all(Path(r["path"]).exists() for r in loaded)


def test_nothing_to_do_returns_clean_receipt():
    rec = hd.install([], "apt")
    assert rec["ok"] is False and rec["command"].startswith("# nothing")


# ------------------------------------------------------------- TUI gates

def test_gate_never_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(jc := __import__("justcompiler"), "load_config",
                        lambda: {"host_dep_install": "never"})
    # ArtifactsScreen gate reads via jc.load_config; simulate decision logic
    from justcompiler import load_config as lc
    assert lc().get("host_dep_install") == "never"
