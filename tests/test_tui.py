"""Pilot tests for the Textual TUI (skipped when textual isn't installed)."""
import json
from pathlib import Path

import pytest

pytest.importorskip("textual")

import justcompiler as jc
import tui


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
    app = tui.JustCompilerApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, tui.HomeScreen)
        table = app.screen.query_one("#recent", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_full_build_flow_via_form(tmp_path, monkeypatch):
    from textual.widgets import RichLog
    out_dir = tmp_path / "EXECUTABLE" / "fake_2026"
    out_dir.mkdir(parents=True)

    def fake_execute(src, branch=None, target_override=None, lang="en"):
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
    async with app.run_test(size=(110, 44)) as pilot:
        await pilot.pause()
        app.start_build("/tmp/fake-src")
        ok = await _wait_for(lambda: getattr(app.run_screen, "finished", False)
                             if app.run_screen else False)
        assert ok, "build worker never finished"
        log = app.run_screen.query_one("#run-log", RichLog)
        assert any("simulated line" in str(s) for s in log.lines[-3:])
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
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(tui.SettingsScreen())
        await pilot.pause()
        sw = app.screen.query_one("#sw-tests")
        # programmatic set doesn't emit Changed in all versions: invoke handler
        app.screen.on_switch_changed(SimpleNamespace(switch=sw, value=True))
        await pilot.pause()
        assert jc.load_config()["run_tests"] is True
