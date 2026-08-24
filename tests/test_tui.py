"""Pilot tests for the Textual TUI (skipped when textual isn't installed)."""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("textual")

import justcompiler as jc
import tui

def _no_intro(monkeypatch):
    """Suppress the welcome overlay while preserving any config stub set
    earlier in the test."""
    import justcompiler as jc
    prev = jc.load_config
    def patched():
        cfg = dict(prev())
        cfg.setdefault("seen_intro", True)
        return cfg
    monkeypatch.setattr(jc, "load_config", patched)




# ------------------------------------------------------------------ gating

def test_should_use_textual_false_without_tty(monkeypatch):
    import sys
    class FakeOut:
        def isatty(self): return False
    monkeypatch.setattr(sys, "stdout", FakeOut())
    assert tui.should_use_textual() is False


def test_should_use_textual_false_without_module(monkeypatch):
    import sys, importlib.util
    class FakeOut:
        def isatty(self): return True
    monkeypatch.setattr(sys, "stdout", FakeOut())
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert tui.should_use_textual() is False


def test_recent_builds_reads_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "EXECUTABLE"
    d1 = base / "proj_20260823_010101"; d1.mkdir(parents=True)
    (d1 / "summary.json").write_text(json.dumps(
        {"status": "success", "target": "Crystal"}))
    rec = tui._recent_builds(5)
    assert len(rec) == 1 and rec[0]["status"] == "success"
    assert rec[0]["target"] == "Crystal"


def test_tui_sink_routes_events():
    from types import SimpleNamespace
    seen = []
    class FakeApp:
        def append_log_line(self, l): seen.append(("append_log_line", l))
        def set_build_progress(self, pct, text): seen.append(("set_build_progress", (pct, text)))
        def set_build_phase(self, text): seen.append(("set_build_phase", text))
        def call_from_thread(self, fn, *a):
            return fn(*a)
    sink = tui.TUISink(FakeApp())
    sink({"event": "log", "prefix": "tag", "msg": "hello"})
    assert ("append_log_line", "tag  hello") in seen
    sink({"event": "progress", "pct": 42.0, "text": "step"})
    assert any(k == "set_build_progress" and a == (42.0, "step") for k, a in seen)
    sink({"event": "phase", "text": "Cloning…"})
    assert ("set_build_phase", "Cloning…") in seen
    seen.clear()
    sink({"event": "panel", "title": "T", "lines": ["a", "b"]})
    kinds = [k for k, _ in seen]
    assert kinds and kinds[0] == "append_log_line"
    assert "T" in seen[0][1] and "b" in seen[0][1]


# ------------------------------------------------------------- pilot: app

async def _wait_for(predicate, timeout=3.0):
    import asyncio
    deadline = __import__("time").time() + timeout
    while __import__("time").time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_app_boots_to_home_with_recent(tmp_path, monkeypatch):
    from textual.widgets import DataTable
    monkeypatch.chdir(tmp_path)
    base = tmp_path / "EXECUTABLE"
    d1 = base / "demo_1"; d1.mkdir(parents=True)
    (d1 / "summary.json").write_text(json.dumps({"status": "success"}))
    monkeypatch.setattr(jc, "load_config", lambda: {"seen_intro": True})
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, tui.HomeScreen)
        table = app.screen.query_one("#recent", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_home_menu_enter_opens_form_and_settings():
    # Regression: on_list_view_selected crashed with
    # AttributeError: 'str' object has no attribute ''
    app = tui.JustCompilerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#menu")
        menu.focus()
        await pilot.press("enter")          # first item = New build
        await pilot.pause()
        assert isinstance(app.screen, tui.BuildFormScreen)
        await pilot.press("escape"); await pilot.pause()
        menu = app.screen.query_one("#menu")
        menu.focus()
        await pilot.press("down")           # second item = Settings
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, tui.SettingsScreen)


@pytest.mark.asyncio
async def test_after_success_escape_returns_to_home(tmp_path, monkeypatch):
    # Regression: Artifacts was pushed on top of the finished BuildRunScreen,
    # so esc bounced BuildRun<->Artifacts forever and Home was unreachable.
    out_dir = tmp_path / "EXECUTABLE" / "ok_1"
    out_dir.mkdir(parents=True)
    (out_dir / "x.bin").write_bytes(b"\x7fELFx")

    def fake_execute(src, branch=None, target_override=None, lang="en", all_targets=False):
        return {"exit_code": 0, "status": "success", "artifacts_dir": str(out_dir),
                "build_folder": out_dir,
                "summary": {"status": "success", "error_class": "", "target": "T",
                            "artifacts": ["x"], "possible_runtime_deps": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/whatever")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app.screen, tui.ArtifactsScreen)
        await pilot.press("escape"); await pilot.pause()
        # THE FIX: back on Home, not bounced into finished BuildRunScreen
        assert isinstance(app.screen, tui.HomeScreen)


@pytest.mark.asyncio
async def test_after_failure_can_reach_home(tmp_path, monkeypatch):
    def fake_execute(src, branch=None, target_override=None, lang="en", all_targets=False):
        return {"exit_code": 1, "status": "build_failed", "artifacts_dir": None,
                "build_folder": None,
                "summary": {"status": "build_failed", "error_class": "oom",
                            "target": "T", "artifacts": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/whatever")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app.screen, tui.FailureScreen)  # fail panel
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app.screen, tui.HomeScreen)      # reachable now


@pytest.mark.asyncio
async def test_url_submit_shows_branch_picker_then_build(monkeypatch):
    # Regression/feature: after entering a git URL the user must get a
    # branch selection list (default first) before the build starts.
    from types import SimpleNamespace
    out_dir = tmp_dir = None
    monkeypatch.setattr(tui.jc, "fetch_remote_git_info",
                        lambda url: ("main", ["dev", "v2", "main"]))
    started = {}
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        def fake_start(src, branch=None, target=None, all_targets=False):
            started.update(src=src, branch=branch, target=target)
        monkeypatch.setattr(app, "start_build", fake_start)
        await pilot.press("n"); await pilot.pause()
        app.screen.query_one("#src").value = "https://github.com/u/repo"
        app.screen._submit()
        for _ in range(40):
            await asyncio.sleep(0.1)
            if isinstance(app.screen, tui.BranchSelectScreen):
                break
        assert isinstance(app.screen, tui.BranchSelectScreen)
        lst = app.screen.query_one("#branch-list")
        assert len(list(lst.children)) == 3          # default + dev + v2
        # select the SECOND entry (dev) and expect build with branch='dev'
        menu = app.screen.query_one("#branch-list")
        menu.focus()
        await pilot.press("down")
        await pilot.press("enter"); await pilot.pause()
        assert started.get("src") == "https://github.com/u/repo"
        assert started.get("branch") == "dev"


@pytest.mark.asyncio
async def test_url_branch_fetch_failure_falls_back(monkeypatch):
    calls = {}
    def boom(url):
        raise RuntimeError("offline")
    monkeypatch.setattr(tui.jc, "fetch_remote_git_info", boom)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        def fake_start(src, branch=None, target=None, all_targets=False):
            calls['b'] = branch
        monkeypatch.setattr(app, "start_build", fake_start)
        await pilot.press("n"); await pilot.pause()
        app.screen.query_one("#src").value = "https://github.com/u/repo"
        app.screen._submit()
        for _ in range(30):
            await asyncio.sleep(0.1)
            if 'b' in calls: break
        assert calls.get('b') is None       # default branch used


@pytest.mark.asyncio
async def test_recent_builds_refresh_on_return_home(tmp_path, monkeypatch):
    # Regression: Home stays mounted, so the recent list was stale after a
    # build — the just-finished project never appeared when going back.
    out_dir = tmp_path / "EXECUTABLE" / "fresh_1"
    def fake_execute(src, branch=None, target_override=None, lang="en", all_targets=False):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "y.bin").write_bytes(b"\x7fELF")
        return {"exit_code": 0, "status": "success", "artifacts_dir": str(out_dir),
                "build_folder": out_dir,
                "summary": {"status": "success", "error_class": "", "target": "T",
                            "artifacts": ["y"], "possible_runtime_deps": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#recent")
        rows_before = table.row_count
        app.start_build("/tmp/whatever")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        await pilot.press("escape"); await pilot.pause()   # -> Artifacts
        await pilot.press("escape"); await pilot.pause()   # -> Home
        assert isinstance(app.screen, tui.HomeScreen)
        assert table.row_count == rows_before + 1          # fresh entry visible


@pytest.mark.asyncio
async def test_artifacts_install_and_undo_flow(tmp_path, monkeypatch):
    import hostdeps as hd
    from textual.widgets import DataTable, RichLog, Button

    d = tmp_path / "EXECUTABLE" / "p_1"
    d.mkdir(parents=True)
    (d / "build_manifest.json").write_text(json.dumps(
        {"projects": [{"name": "p", "items": [],
                       "runtime_deps": [
                           {"pkg": "GTK4", "apt": "libgtk-4-dev", "pacman": "gtk4",
                            "dnf": "gtk4-devel", "winget": "", "choco": "", "scoop": ""}]}]}))
    monkeypatch.chdir(tmp_path)

    # gate: ask (default) -> confirm modal appears
    monkeypatch.setattr(jc, "load_config", lambda: {"host_dep_install": "ask"})
    # detection + missing filter
    monkeypatch.setattr(hd, "detect_pm", lambda: "dnf")
    monkeypatch.setattr(hd, "filter_installed",
                        lambda pkgs, pm: ["gtk4-devel"])

    rd = tmp_path / "receipts"
    monkeypatch.setattr(hd, "RECEIPT_DIR", rd)
    installed = {}
    def fake_install(pkgs, pm, *, runner=None, on_line=None):
        if on_line:
            on_line("Installing gtk4-devel")
        rec = {"when": "now", "pm": pm, "requested": list(pkgs),
               "newly_installed": list(pkgs), "transaction_id": 7,
               "command": f"sudo dnf install -y {' '.join(pkgs)}",
               "ok": True, "path": ""}
        r2 = hd._save_receipt(rec)
        installed.update(rec=r2)
        return r2
    monkeypatch.setattr(hd, "install", fake_install)
    undo_calls = []
    monkeypatch.setattr(hd, "undo",
                        lambda rec, runner=None, on_line=None:
                        (undo_calls.append(rec), True)[1])

    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(120, 44)) as pilot:
        await pilot.pause()
        app.push_screen(tui.ArtifactsScreen(d, {
            "status": "success", "artifacts_dir": str(d),
            "summary": {"possible_runtime_deps": ["GTK4"]}}))
        await pilot.pause()

        # --- install flow
        await pilot.press("i"); await pilot.pause()
        assert isinstance(app.screen, tui.ConfirmScreen), type(app.screen).__name__
        await pilot.press("y"); await pilot.pause()
        for _ in range(30):
            await asyncio.sleep(0.1); await pilot.pause()
            if installed.get("rec"): break
        assert installed.get("rec") and installed["rec"]["ok"]
        await pilot.press("escape"); await pilot.pause()   # close stream modal
        assert isinstance(app.screen, tui.ArtifactsScreen)

        # --- undo flow
        await pilot.press("u"); await pilot.pause()
        assert isinstance(app.screen, tui.ReceiptListScreen)
        tbl = app.screen.query_one("#receipt-table", DataTable)
        assert tbl.row_count == 1
        from types import SimpleNamespace as NS
        ev = NS(row_key=NS(value=installed["rec"]["path"]))
        app.screen.on_data_table_row_selected(ev)
        await pilot.pause()
        assert isinstance(app.screen, tui.ConfirmScreen)
        await pilot.press("y"); await asyncio.sleep(0.4); await pilot.pause()
        assert len(undo_calls) == 1 and undo_calls[0]["pm"] == "dnf"


@pytest.mark.asyncio
async def test_install_gate_never_blocks(monkeypatch, tmp_path):
    from textual.widgets import RichLog
    d = tmp_path / "EXECUTABLE" / "p_2"; d.mkdir(parents=True)
    monkeypatch.setattr(jc, "load_config", lambda: {"host_dep_install": "never"})
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        app.push_screen(tui.ArtifactsScreen(d, {"status": "success",
                                                "artifacts_dir": str(d)}))
        await pilot.pause()
        await pilot.press("i"); await pilot.pause()
        body = app.screen.query_one("#msg-body", RichLog)
        txt = " ".join(str(s) for s in list(body.lines))
        assert "disabled" in txt


@pytest.mark.asyncio
async def test_progress_pane_default_and_log_toggle(tmp_path, monkeypatch):
    from textual.widgets import Label, Static
    out_dir = tmp_path / "EXECUTABLE" / "pp_1"
    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        jc.UI.info("compiling thing A")
        import time as _t; _t.sleep(0.6)          # let timer tick + steps fire
        jc.UI.info("compiling thing B")
        return {"exit_code": 0, "status": "success", "artifacts_dir": None,
                "build_folder": None,
                "summary": {"status": "success", "error_class": "",
                            "target": "T", "artifacts": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/x")
        for _ in range(60):
            await asyncio.sleep(0.1)
            rs = app.run_screen
            if rs and getattr(rs, "finished", False):
                break
        await pilot.pause()
        rs = app.run_screen
        # default view: progress pane visible, raw log hidden
        assert not app.screen.has_class("showlog")
        steps_txt = str(rs.query_one("#run-steps").render())
        assert "Prepare" in steps_txt or "Compile" in steps_txt
        lastline = str(rs.query_one("#run-lastline").render())
        assert len(lastline) > 0
        # toggle reveals the full log (replayed from buffer)
        await pilot.press("l"); await pilot.pause()
        assert app.screen.has_class("showlog")
        log = rs.query_one("#run-log")
        blob = " ".join(str(s) for s in list(log.lines))
        assert "thing B" in blob
        # and back to progress pane
        await pilot.press("l"); await pilot.pause()
        assert not app.screen.has_class("showlog")


@pytest.mark.asyncio
async def test_checklist_shows_completed_after_finish(tmp_path, monkeypatch):
    # Regression: checklist stayed on '▶ Save' after a successful build
    from textual.widgets import Static
    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        return {"exit_code": 0, "status": "success", "artifacts_dir": None,
                "build_folder": None,
                "summary": {"status": "success", "error_class": "",
                            "target": "T", "artifacts": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/x")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        steps = str(app.run_screen.query_one("#run-steps").render())
        assert "Completed" in steps and "▶" not in steps
        assert "✔ Save" in steps or "Save" in steps


@pytest.mark.asyncio
async def test_full_build_flow_via_form(tmp_path, monkeypatch):
    from textual.widgets import RichLog
    out_dir = tmp_path / "EXECUTABLE" / "fake_2026"
    out_dir.mkdir(parents=True)

    def fake_execute(src, branch=None, target_override=None, lang="en", all_targets=False):
        jc.UI.info("simulated line")
        (out_dir / "x.bin").write_bytes(b"\x7fELFfake")
        return {"exit_code": 0, "status": "success",
                "artifacts_dir": str(out_dir), "build_folder": out_dir,
                "summary": {"status": "success", "error_class": "",
                            "target": "Crystal", "artifacts": ["x"],
                            "possible_runtime_deps": []}}

    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/fake-src")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok, "build worker never finished"
        await pilot.pause(); await pilot.pause()
        blob = " ".join(app.run_screen._raw)
        assert "simulated line" in blob, f"missing line; got {blob[:120]!r}"
        await pilot.press("escape")          # finish -> build_finished push
        await pilot.pause()
        assert isinstance(app.screen, tui.ArtifactsScreen)
        table = app.screen.query_one("#art-table")
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_settings_toggle_persists(tmp_path, monkeypatch):
    from types import SimpleNamespace
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(jc, "CONFIG_FILE", cfg_file)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(tui.SettingsScreen())
        await pilot.pause()
        sw = app.screen.query_one("#sw-tests")
        # programmatic set doesn't emit Changed in all versions: invoke handler
        app.screen.on_switch_changed(SimpleNamespace(switch=sw, value=True))
        await pilot.pause()
        assert jc.load_config()["run_tests"] is True


@pytest.mark.asyncio
async def test_finish_shows_clear_buttons_and_navigation(tmp_path, monkeypatch):
    from textual.widgets import Button
    out_dir = tmp_path / "EXECUTABLE" / "fin_1"
    out_dir.mkdir(parents=True)
    (out_dir / "z.bin").write_bytes(b"\x7fELF")
    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        return {"exit_code": 0, "status": "success", "artifacts_dir": str(out_dir),
                "build_folder": out_dir,
                "summary": {"status": "success", "error_class": "",
                            "target": "Crystal", "duration_s": 3.2,
                            "artifacts": ["z"], "possible_runtime_deps": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    _no_intro(monkeypatch)
    app = tui.JustCompilerApp()
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/x")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        # three clear buttons exist on the finish state
        for bid in ("bf-art", "bf-open", "bf-home"):
            assert app.run_screen.query_one(f"#{bid}", Button)
        # phase headline is friendly, no 'press esc' instruction anymore
        phase = str(app.run_screen.query_one("#run-phase").render())
        assert "succeeded" in phase and "esc" not in phase.lower()
        # enter = view files -> ArtifactsScreen
        await pilot.press("enter"); await pilot.pause()
        assert isinstance(app.screen, tui.ArtifactsScreen)
        # artifacts screen exposes the same actions as clickable buttons
        for bid in ("ab-run", "ab-open", "ab-home"):
            assert app.screen.query_one(f"#{bid}", Button)
        # home button returns to HomeScreen
        app.screen.query_one("#ab-home").press()
        await pilot.pause()
        assert isinstance(app.screen, tui.HomeScreen)


@pytest.mark.asyncio
async def test_welcome_intro_shows_once(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(jc, "CONFIG_FILE", cfg_file)
    monkeypatch.chdir(tmp_path)

    app1 = tui.JustCompilerApp()
    async with app1.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        # first launch: welcome overlay on top of Home
        assert isinstance(app1.screen, tui.MessageScreen)
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app1.screen, tui.HomeScreen)

    # config now records seen_intro
    assert json.loads(cfg_file.read_text()).get("seen_intro") is True

    app2 = tui.JustCompilerApp()
    async with app2.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app2.screen, tui.HomeScreen)   # no overlay again


@pytest.mark.asyncio
async def test_failure_screen_copy_to_clipboard(tmp_path, monkeypatch):
    from textual.widgets import Static
    out_dir = tmp_path / "EXECUTABLE" / "f_1"

    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "build_log.txt").write_text(f"line1\n{Path.home()}/x\nline3")
        summ = {"status": "build_failed", "error_class": "oom", "target": "T",
                "artifacts": [], "possible_runtime_deps": []}
        import traceback as _tb
        try:
            rp = jc._write_failure_report(out_dir, summ)
            assert rp, "report returned None"
        except Exception:
            _tb.print_exc(); raise
        summ["failure_report"] = rp
        (out_dir / "summary.json").write_text(json.dumps(summ))
        return {"exit_code": 1, "status": "build_failed",
                "artifacts_dir": str(out_dir), "build_folder": out_dir,
                "summary": summ}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    copied = []
    monkeypatch.setattr(jc, "_copy_to_clipboard",
                        lambda text: (copied.append(text), True)[1])

    app = tui.JustCompilerApp()
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/x")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app.screen, tui.FailureScreen)
        await pilot.press("c"); await pilot.pause()
        assert len(copied) == 1
        text = copied[0]
        assert "JustCompiler v" in text
        assert str(Path.home()) not in text and "~/" in text


@pytest.mark.asyncio
async def test_failure_report_written_and_scrubbed(tmp_path, monkeypatch):
    import getpass
    out_dir = tmp_path / "EXECUTABLE" / "f_2"

    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "build_log.txt").write_text(
            f"built by {getpass.getuser()} at {Path.home()}\n"
            "mail me: dev@example.com\n")
        summ = {"status": "build_failed", "error_class": "",
                "target": "T", "artifacts": [], "possible_runtime_deps": []}
        rp = jc._write_failure_report(out_dir, summ)
        summ["failure_report"] = rp
        (out_dir / "summary.json").write_text(json.dumps(summ))
        return {"exit_code": 1, "status": "build_failed",
                "artifacts_dir": str(out_dir), "build_folder": out_dir,
                "summary": summ}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/y")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
    rep_file = json.load(open(out_dir / "summary.json"))["failure_report"]
    text = Path(rep_file).read_text()
    assert getpass.getuser() not in text.replace("<user>", "")
    assert "dev@example.com" not in text and "<email>" in text
    assert str(Path.home()) not in text


@pytest.mark.asyncio
async def test_checklist_shows_completed_after_finish(tmp_path, monkeypatch):
    # Regression: checklist stayed on '▶ Save' after a successful build
    from textual.widgets import Static
    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        return {"exit_code": 0, "status": "success", "artifacts_dir": None,
                "build_folder": None,
                "summary": {"status": "success", "error_class": "",
                            "target": "T", "artifacts": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/x")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        steps = str(app.run_screen.query_one("#run-steps").render())
        assert "Completed" in steps and "▶" not in steps
        assert "✔ Save" in steps or "Save" in steps


@pytest.mark.asyncio
async def test_full_build_flow_via_form(tmp_path, monkeypatch):
    from textual.widgets import RichLog
    out_dir = tmp_path / "EXECUTABLE" / "fake_2026"
    out_dir.mkdir(parents=True)

    def fake_execute(src, branch=None, target_override=None, lang="en", all_targets=False):
        jc.UI.info("simulated line")
        (out_dir / "x.bin").write_bytes(b"\x7fELFfake")
        return {"exit_code": 0, "status": "success",
                "artifacts_dir": str(out_dir), "build_folder": out_dir,
                "summary": {"status": "success", "error_class": "",
                            "target": "Crystal", "artifacts": ["x"],
                            "possible_runtime_deps": []}}

    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/fake-src")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok, "build worker never finished"
        await pilot.pause(); await pilot.pause()
        blob = " ".join(app.run_screen._raw)
        assert "simulated line" in blob, f"missing line; got {blob[:120]!r}"
        await pilot.press("escape")          # finish -> build_finished push
        await pilot.pause()
        assert isinstance(app.screen, tui.ArtifactsScreen)
        table = app.screen.query_one("#art-table")
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_settings_toggle_persists(tmp_path, monkeypatch):
    from types import SimpleNamespace
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(jc, "CONFIG_FILE", cfg_file)
    app = tui.JustCompilerApp()
    if "_no_intro" in globals(): _no_intro(monkeypatch)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(tui.SettingsScreen())
        await pilot.pause()
        sw = app.screen.query_one("#sw-tests")
        # programmatic set doesn't emit Changed in all versions: invoke handler
        app.screen.on_switch_changed(SimpleNamespace(switch=sw, value=True))
        await pilot.pause()
        assert jc.load_config()["run_tests"] is True


@pytest.mark.asyncio
async def test_finish_shows_clear_buttons_and_navigation(tmp_path, monkeypatch):
    from textual.widgets import Button
    out_dir = tmp_path / "EXECUTABLE" / "fin_1"
    out_dir.mkdir(parents=True)
    (out_dir / "z.bin").write_bytes(b"\x7fELF")
    def fake_execute(src, branch=None, target_override=None, lang="en",
                     all_targets=False):
        return {"exit_code": 0, "status": "success", "artifacts_dir": str(out_dir),
                "build_folder": out_dir,
                "summary": {"status": "success", "error_class": "",
                            "target": "Crystal", "duration_s": 3.2,
                            "artifacts": ["z"], "possible_runtime_deps": []}}
    monkeypatch.setattr(jc, "execute_build", fake_execute)
    monkeypatch.chdir(tmp_path)
    _no_intro(monkeypatch)
    app = tui.JustCompilerApp()
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/x")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok
        # three clear buttons exist on the finish state
        for bid in ("bf-art", "bf-open", "bf-home"):
            assert app.run_screen.query_one(f"#{bid}", Button)
        # phase headline is friendly, no 'press esc' instruction anymore
        phase = str(app.run_screen.query_one("#run-phase").render())
        assert "succeeded" in phase and "esc" not in phase.lower()
        # enter = view files -> ArtifactsScreen
        await pilot.press("enter"); await pilot.pause()
        assert isinstance(app.screen, tui.ArtifactsScreen)
        # artifacts screen exposes the same actions as clickable buttons
        for bid in ("ab-run", "ab-open", "ab-home"):
            assert app.screen.query_one(f"#{bid}", Button)
        # home button returns to HomeScreen
        app.screen.query_one("#ab-home").press()
        await pilot.pause()
        assert isinstance(app.screen, tui.HomeScreen)


@pytest.mark.asyncio
async def test_welcome_intro_shows_once(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(jc, "CONFIG_FILE", cfg_file)
    monkeypatch.chdir(tmp_path)

    app1 = tui.JustCompilerApp()
    async with app1.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        # first launch: welcome overlay on top of Home
        assert isinstance(app1.screen, tui.MessageScreen)
        await pilot.press("escape"); await pilot.pause()
        assert isinstance(app1.screen, tui.HomeScreen)

    # config now records seen_intro
    assert json.loads(cfg_file.read_text()).get("seen_intro") is True

    app2 = tui.JustCompilerApp()
    async with app2.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app2.screen, tui.HomeScreen)   # no overlay again


@pytest.mark.asyncio
async def test_history_entry_reopens_artifacts(tmp_path, monkeypatch):
    # Regression: entering a history row must open ArtifactsScreen (not just
    # a text popup) so users can re-run artifacts from past builds.
    from textual.widgets import DataTable
    d = tmp_path / "EXECUTABLE" / "old_1"
    d.mkdir(parents=True)
    (d / "x.bin").write_bytes(b"\x7fELF")
    (d / "summary.json").write_text(json.dumps(
        {"status": "success", "target": "Crystal", "duration_s": 5,
         "artifacts": ["x.bin"], "possible_runtime_deps": []}))
    monkeypatch.chdir(tmp_path)
    app = tui.JustCompilerApp()
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        tbl = app.screen.query_one("#recent", DataTable)
        tbl.focus()
        await pilot.press("enter"); await pilot.pause()
        assert isinstance(app.screen, tui.ArtifactsScreen), \
            f"got {type(app.screen).__name__}"
