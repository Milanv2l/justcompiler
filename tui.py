"""Textual TUI for JustCompiler (interactive mode).

Keyboard-first; mouse is a bonus. Headless mode never enters this module.
Falls back to the legacy ANSI flow when textual is unavailable or stdout
is not a tty (see should_use_textual()).
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import justcompiler as jc
import core
from core import UI, t
import docker_manager


def should_use_textual() -> bool:
    """Interactive TUI only when we have a real terminal AND textual exists."""
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    return importlib.util.find_spec("textual") is not None


def _recent_builds(limit: int = 10) -> list[dict]:
    base = Path("./EXECUTABLE")
    if not base.is_dir():
        return []
    out = []
    dirs = sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs[:limit * 2]:
        if not d.is_dir():
            continue
        summary = {}
        sf = d / "summary.json"
        if sf.exists():
            try:
                summary = json.loads(sf.read_text())
            except Exception:
                summary = {}
        out.append({
            "name": d.name,
            "when": datetime.datetime.fromtimestamp(d.stat().st_mtime)
                    .strftime("%m-%d %H:%M"),
            "status": summary.get("status", "-"),
            "target": summary.get("target", ""),
            "dir": d,
        })
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ sink

class TUISink:
    """Bridges core.UI events into the running Textual app."""

    def __init__(self, app):
        self.app = app

    def __call__(self, event: dict):
        kind = event.get("event")
        app = self.app
        text = str(event.get("msg") or event.get("text") or "")
        prefix = str(event.get("prefix") or "").strip()
        line = f"{prefix}  {text}".strip() if prefix else text
        if kind == "panel":
            body = "\n".join(str(l) for l in event.get("lines", []))
            line = f"┌ {event.get('title','')}\n{body}"
        elif kind == "progress":
            try:
                app.call_from_thread(app.set_build_progress,
                                     float(event.get("pct") or 0),
                                     str(event.get("text") or ""))
            except Exception:
                pass
            return
        elif kind == "phase":
            try:
                app.call_from_thread(app.set_build_phase, text)
            except Exception:
                pass
            return
        try:
            app.call_from_thread(app.append_log_line, line)
        except Exception:
            pass


# ------------------------------------------------------------------ screens

try:
    from textual.app import App, ComposeResult
    from textual.containers import Vertical, Horizontal
    from textual.screen import Screen
    from textual.widgets import (Header, Footer, Static, ListView, ListItem,
                                Label, Input, Select, Switch, RichLog,
                                DataTable, Button, ProgressBar)
    HAS_TEXTUAL = True
except Exception:  # pragma: no cover - fallback path exercised instead
    HAS_TEXTUAL = False


if HAS_TEXTUAL:

    class HomeScreen(Screen):
        BINDINGS = [
            ("n", "new_build", "New build"),
            ("s", "settings", "Settings"),
            ("r", "refresh", "Refresh"),
            ("q", "quit_app", "Quit"),
        ]

        def compose(self) -> ComposeResult:
            cfg_version = jc.VERSION
            docker_ok = "✔ available" if __import__("shutil").which("docker") else "✖ missing"
            yield Header(show_clock=False)
            yield Static(
                f"[b magenta]JustCompiler Hub[/]  v{cfg_version}   "
                f"Docker: {'[green]' if 'available' in docker_ok else '[red]'}{docker_ok}[/]",
                id="home-head")
            yield Label("[b]" + t('title'), id="home-menu-title")
            yield ListView(
                ListItem(Label(" [cyan]n[/]  " + t('menu_1')), id="new"),
                ListItem(Label(" [cyan]s[/]  " + t('menu_3')), id="settings"),
                ListItem(Label(" [cyan]q[/]  Exit"), id="quit"),
                id="menu")
            yield Label("[b]Recent builds[/]", id="recent-title")
            yield DataTable(id="recent")
            yield Footer()

        def on_mount(self):
            table = self.query_one("#recent", DataTable)
            table.cursor_type = "row"
            if not table.columns:
                table.add_columns("When", "Project", "Status", "Target")
                table.zebra_stripes = True
            self.action_refresh()

        def action_refresh(self):
            table = self.query_one("#recent", DataTable)
            table.clear()
            for b in _recent_builds():
                color = {"success": "green", "partial": "yellow",
                         "build_failed": "red"}.get(b["status"], "dim")
                table.add_row(b["when"], b["name"],
                              f"[{color}]{b['status']}[/]", b["target"], key=str(b["dir"]))

        def on_list_view_selected(self, ev):
            item = ev.item
            name = getattr(item, "id", None) or getattr(item, "name", None)
            if name == "new":
                self.action_new_build()
            elif name == "settings":
                self.action_settings()
            elif name == "quit":
                self.app.exit()

        def on_data_table_row_selected(self, ev):
            try:
                d = Path(str(ev.row_key.value))
            except Exception:
                return
            sfile = d / "summary.json"
            data = {}
            if sfile.exists():
                try:
                    data = json.loads(sfile.read_text())
                except Exception:
                    pass
            lines = [f"[b]{d.name}[/]",
                     f"status: {data.get('status','-')}   target: {data.get('target','-')}",
                     f"duration: {data.get('duration_s','?')}s",
                     f"artifacts: {', '.join(data.get('artifacts', []) or ['-'])}",
                     "", str(d)]
            self.app.push_screen(MessageScreen("\n".join(lines), title="Build details"))

        def action_new_build(self):
            self.app.push_screen(BuildFormScreen())

        def action_settings(self):
            self.app.push_screen(SettingsScreen())

        def action_quit_app(self):
            self.app.exit()


    class BuildFormScreen(Screen):
        BINDINGS = [("escape", "back", "Back")]

        def action_back(self):
            self.app.pop_screen()

        def compose(self) -> ComposeResult:
            plugin_names = ["auto"]
            try:
                plugins = json.loads((Path(jc.__file__).parent / "plugins.json").read_text())
                plugin_names += [p["name"] for p in plugins]
            except Exception:
                pass
            yield Header(show_clock=False)
            yield Vertical(
                Label("[b]New build[/]"),
                Label("Local path or git URL"),
                Input(placeholder="/path/to/project  or  https://github.com/user/repo",
                      id="src"),
                Label("Branch (optional, URLs only)"),
                Input(placeholder="default", id="branch"),
                Label("Build target"),
                Select([(n, n) for n in plugin_names], value="auto", id="target"),
                Horizontal(
                    Button("Build  ⏎", variant="primary", id="go"),
                    Button("Cancel", id="cancel"),
                ),
                Static("", id="form-error"),
            )
            yield Footer()

        def on_button_pressed(self, ev):
            if ev.button.id == "cancel":
                self.app.pop_screen()
            elif ev.button.id == "go":
                self._submit()

        def on_input_submitted(self, ev):
            self._submit()

        def _submit(self):
            src = self.query_one("#src", Input).value.strip()
            branch = self.query_one("#branch", Input).value.strip() or None
            target = self.query_one("#target", Select).value
            err = self.query_one("#form-error", Static)
            if not src:
                err.update("[red]Enter a path or URL first.[/]")
                return
            if not jc._is_git_url(src) and not Path(src).expanduser().exists():
                err.update(f"[red]Path does not exist:[/] {src}")
                return
            err.update("")
            target_arg = None if target in ("auto", "", None) else target
            self.app.start_build(src, branch=branch, target=target_arg)


    class BuildRunScreen(Screen):
        BINDINGS = [
            ("c", "cancel_build", "Cancel"),
            ("escape", "done", "Done"),
        ]

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.finished = False
            self._t0 = time.time()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Vertical(
                Label("[b]Starting…[/]", id="run-phase"),
                Label("", id="run-timer"),
                ProgressBar(total=100.0, show_eta=False, id="run-bar"),
                RichLog(highlight=False, markup=False, wrap=True, id="run-log"),
                Horizontal(Button("Cancel build", variant="error", id="cancel")),
            )
            yield Footer()

        def on_mount(self):
            log = self.query_one("#run-log", RichLog)
            log.write("Waiting for builder output…")
            # live elapsed + engine status ticker: the screen never looks dead
            self.set_interval(1.0, self._tick)

        def _tick(self):
            if getattr(self, "finished", False):
                return
            try:
                import justcompiler as jc
                status = getattr(jc, "CURRENT_STATUS", "") or ""
            except Exception:
                status = ""
            secs = int(time.time() - self._t0)
            mm, ss = divmod(secs, 60)
            txt = f"⏱ {mm:02d}:{ss:02d}  ·  {status}" if status else f"⏱ {mm:02d}:{ss:02d}"
            try:
                self.query_one("#run-timer", Label).update(txt)
            except Exception:
                pass

        def set_phase(self, text: str):
            try:
                self.query_one("#run-phase", Label).update(f"[b]{text}[/]")
            except Exception:
                pass

        def set_progress(self, pct: float, text: str):
            try:
                bar = self.query_one("#run-bar", ProgressBar)
                bar.update(progress=max(0.0, min(100.0, pct)))
            except Exception:
                pass
            self.set_phase(text)

        def append(self, line: str):
            try:
                self.query_one("#run-log", RichLog).write(line)
            except Exception:
                pass

        def finish(self, result: dict):
            self.finished = True
            status = result["status"]
            color = {"success": "green", "partial": "yellow"}.get(status, "red")
            try:
                self.query_one("#run-phase", Label).update(
                    f"[b {color}]Finished: {status}[/]   "
                    f"(exit {result['exit_code']}) — press esc")
                btn = self.query_one("#cancel", Button)
                btn.label = "Back to home"
                btn.variant = "default"
                btn.id = "back"
            except Exception:
                pass

        def on_button_pressed(self, ev):
            if ev.button.id == "cancel":
                self.action_cancel_build()
            else:
                self.action_done()

        def action_cancel_build(self):
            if self.finished:
                self.action_done()
                return
            docker_manager.cancel_active_run()
            self.append("[cancel requested — killing sandbox…]")

        def action_done(self):
            if self.finished:
                self.app.build_finished(self.app.last_result)
            else:
                self.app.pop_screen()


    class ArtifactsScreen(Screen):
        BINDINGS = [
            ("r", "run_artifact", "Run"),
            ("o", "open_folder", "Open folder"),
            ("escape", "home", "Home"),
        ]

        def __init__(self, artifacts_dir: Path, result: dict, **kw):
            super().__init__(**kw)
            self.artifacts_dir = artifacts_dir
            self.result = result

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Label(f"[b green]✔ {self.result['status']}[/]  "
                        f"{self.artifacts_dir.name}", id="art-head")
            yield DataTable(id="art-table")
            yield Static("", id="art-hint")
            yield Footer()

        def on_mount(self):
            table = self.query_one("#art-table", DataTable)
            table.cursor_type = "row"
            table.add_columns("Name", "Kind", "Size", "Main")
            arts = jc._scan_artifacts(self.artifacts_dir)
            if not arts:
                # archives-only or dir-bundles: still show what was produced
                manifest = {}
                try:
                    mf = self.artifacts_dir / "build_manifest.json"
                    manifest = json.loads(mf.read_text())
                except Exception:
                    pass
                for proj in manifest.get("projects", []):
                    for item in proj.get("items", []):
                        n = item.get("name", "")
                        p = self.artifacts_dir / n
                        size = p.stat().st_size if p.is_file() else 0
                        from collections import namedtuple
                        AI = jc.ArtifactInfo
                        arts.append(AI(name=n, kind=item.get("kind", "artifact"),
                                       cmd=item.get("path") and [item["path"]],
                                       cwd=None, size=size,
                                       desc="", is_main=True))
            for a in arts:
                mark = "[b green]✔[/]" if a.is_main else ""
                table.add_row(a.name, a.kind, f"{a.size//1024} KiB" if a.size else "—",
                              mark, key=a.name)
            hint = self.query_one("#art-hint", Static)
            deps = self.result.get("summary", {}).get("possible_runtime_deps", [])
            if deps:
                hint.update(f"[yellow]possible runtime deps:[/] {', '.join(deps)}")

        def _selected(self):
            table = self.query_one("#art-table", DataTable)
            if table.row_count == 0:
                return None
            try:
                row = table.coordinate_to_cell_key(table.cursor_coordinate)[0]
            except Exception:
                return None
            return str(row.value) if row and row.value else None

        def action_run_artifact(self):
            name = self._selected()
            if not name:
                return
            arts = {a.name: a for a in jc._scan_artifacts(self.artifacts_dir)}
            if not arts:
                # manifest-fallback entries (bundles/archives): no runnable cmd
                self.app.run_artifact_in_modal(
                    jc.ArtifactInfo(name=name, kind="artifact", cmd=None, cwd=None,
                                    size=0, desc="", is_main=True),
                    Path(self.result["artifacts_dir"]))
                return
            art = arts.get(name)
            if not art:
                return
            self.app.run_artifact_in_modal(art, Path(self.result["artifacts_dir"]))

        def action_open_folder(self):
            p = Path(self.result["artifacts_dir"]).resolve()
            import subprocess as sp, platform as pf
            opener = ("explorer" if pf.system() == "Windows"
                      else "open" if pf.system() == "Darwin" else "xdg-open")
            try:
                sp.Popen([opener, str(p)], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
            except Exception:
                pass

        def action_home(self):
            app = self.app
            # safety net: pop everything above Home (works from any depth)
            while type(app.screen).__name__ != "HomeScreen" and len(app.screen_stack) > 1:
                try:
                    app.pop_screen()
                except Exception:
                    break


    class SettingsScreen(Screen):
        BINDINGS = [("escape", "back", "Back")]

        def action_back(self):
            self.app.pop_screen()

        def compose(self) -> ComposeResult:
            cfg = jc.load_config()
            yield Header(show_clock=False)
            yield Vertical(
                Label("[b]Settings[/]"),
                Horizontal(Label("Check updates"), Switch(value=bool(cfg.get("check_updates", True)), id="sw-updates")),
                Horizontal(Label("Run tests"), Switch(value=bool(cfg.get("run_tests", False)), id="sw-tests")),
                Horizontal(Label("Auto-install host deps (headless)"), Switch(value=bool(cfg.get("auto_install_deps", False)), id="sw-deps")),
                Horizontal(Label("Language"), Select([("English", "en"), ("Nederlands", "nl")],
                          value=cfg.get("lang", "en") if cfg.get("lang", "en") in ("en", "nl") else "en",
                          id="sel-lang")),
                Horizontal(Label("Sandbox profile"), Select([("full", "full"), ("slim", "slim")],
                          value="slim" if cfg.get("profile") == "slim" else "full", id="sel-profile")),
                Horizontal(Button("Force update now", id="force-update")),
                Static("", id="set-status"),
            )
            yield Footer()

        def _save(self, **kw):
            jc.save_config(**kw)

        def on_switch_changed(self, ev):
            mapping = {"sw-updates": ("check_updates",), "sw-tests": ("run_tests",),
                       "sw-deps": ("auto_install_deps",)}
            key = mapping.get(ev.switch.id, (None,))[0]
            if key:
                self._save(**{key: ev.value})

        def on_select_changed(self, ev):
            if ev.select.id == "sel-lang":
                code = ev.value
                self._save(lang=code)
                core.set_lang(code)
            elif ev.select.id == "sel-profile":
                self._save(profile=ev.value)

        def on_button_pressed(self, ev):
            if ev.button.id == "force-update":
                st = self.query_one("#set-status", Static)
                st.update("[yellow]Checking / updating…[/]")
                self.app.run_worker(self._do_force_update, thread=True, exclusive=True)

        def _do_force_update(self):
            def job():
                captured = []
                core.UI.bind(captured.append)
                try:
                    try:
                        done = jc._do_update(ask=False, force=True)
                        msg = "Updated! Restart to apply." if done else \
                            f"Already latest ({jc.VERSION})."
                    except Exception as e:
                        msg = f"Update failed: {e}"
                finally:
                    core.UI.unbind()
                detail = "  |  ".join(str(e.get("msg", "")) for e in captured
                                      if e.get("event") in ("info", "warn", "error"))[:200]
                self.app.call_from_thread(
                    lambda: self.query_one("#set-status", Static)
                    .update(f"{msg}  {detail}"))
            threading.Thread(target=job, daemon=True).start()


    class MessageScreen(Screen):
        BINDINGS = [("escape", "close", "Close"), ("enter", "close", "Close")]

        def __init__(self, text: str, title: str = "Message", markup: bool = False):
            super().__init__()
            self.text = text
            self.title_ = title
            self.use_markup = markup

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Vertical(Label(f"[b]{self.title_}[/]"),
                           RichLog(markup=self.use_markup, wrap=True, id="msg-body"))
            yield Footer()

        def on_mount(self):
            self.query_one("#msg-body", RichLog).write(self.text)

        def action_close(self):
            self.app.pop_screen()

    class JustCompilerApp(App[None]):
        TITLE = "JustCompiler"
        SCREENS = {"home": HomeScreen}
        CSS = """
        Screen { background: $surface; }
        #menu { height: auto; max-height: 40%; }
        #recent { height: 1fr; }
        #recent-title, #home-menu-title { margin-left: 1; }
        #home-head { padding: 0 1; background: $boost; }
        Vertical { padding: 0 1; }
        Horizontal { height: auto; }
        Label { margin: 0 1; }
        Input, Select { width: 100%; }
        #run-log { border: round $primary; height: 1fr; }
        #run-bar { margin: 0 1; }
        DataTable { height: 1fr; }
        #art-hint { color: yellow; margin: 0 1; }
        #set-status { margin: 1; color: yellow; }
        """

        def __init__(self):
            super().__init__()
            self.run_screen = None
            self.last_result = None
            self.sink = TUISink(self)

        def get_default_screen(self):
            return HomeScreen()

        # ---- UI-sink bridge -------------------------------------------------
        def append_log_line(self, line: str):
            if self.run_screen is not None:
                self.run_screen.append(line)

        def set_build_progress(self, pct: float, text: str):
            if self.run_screen is not None:
                self.run_screen.set_progress(pct, text)

        def set_build_phase(self, text: str):
            if self.run_screen is not None:
                self.run_screen.set_phase(text)

        # ---- build lifecycle -------------------------------------------------
        def start_build(self, src: str, branch=None, target=None):
            run_screen = BuildRunScreen()
            self.last_result = None
            self.run_screen = run_screen
            UI.bind(self.sink)
            self.push_screen(run_screen)

            def job():
                try:
                    result = jc.execute_build(src, branch=branch,
                                              target_override=target,
                                              lang=core._CURRENT_LANG)
                except Exception as e:
                    result = {"exit_code": 1, "status": "build_failed",
                              "summary": {"status": "build_failed",
                                          "error_class": str(e)[:200]},
                              "artifacts_dir": None}
                finally:
                    UI.unbind()
                    docker_manager.ACTIVE_RUN_NAME["name"] = None
                self.call_from_thread(self._build_done, result)

            threading.Thread(target=job, daemon=True).start()

        def _build_done(self, result: dict):
            self.last_result = result
            if self.run_screen is not None:
                self.run_screen.finish(result)

        def build_finished(self, result: dict):
            # Drop the finished BuildRunScreen from the stack first, otherwise
            # esc on Artifacts/Failed pops straight back into it and re-pushes
            # this screen forever (user could never reach Home).
            try:
                if isinstance(self.screen, BuildRunScreen):
                    self.pop_screen()
            except Exception:
                pass
            artifacts_dir = result.get("artifacts_dir")
            if artifacts_dir and Path(artifacts_dir).exists():
                self.push_screen(ArtifactsScreen(Path(artifacts_dir), result))
            else:
                err = result.get("summary", {}).get("error_class", "")
                msg = f"Build failed ({result['status']}).\nerror_class: {err}\n\nSee logs in the build folder."
                self.push_screen(MessageScreen(msg, title="Build failed", markup=False))

        # ---- artifact run -----------------------------------------------------
        def run_artifact_in_modal(self, artifact, artifacts_dir: Path):
            import subprocess
            if not artifact.cmd:
                self.push_screen(MessageScreen(
                    "This artifact has no runnable command "
                    f"(kind: {artifact.kind}).", title=f"Run: {artifact.name}"))
                return
            # plain-text output: program logs often contain [brackets] that
            # would explode RichLog markup
            screen = MessageScreen("$ starting…", title=f"Run: {artifact.name}",
                                   markup=False)
            self.push_screen(screen)

            def job():
                cmd = list(artifact.cmd)
                # never let side-effect files (certs, configs) land in the repo
                workdir = Path(artifact.cwd) if artifact.cwd \
                    else Path(artifacts_dir).resolve()
                proc = subprocess.Popen(
                    cmd, shell=(sys.platform == "win32"), cwd=str(workdir),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, errors="replace")
                for line in proc.stdout:
                    screen.app.call_from_thread(
                        lambda l=line.rstrip(): screen.query_one("#msg-body", RichLog).write(l))
                proc.wait()
                rc = proc.returncode
                self.call_from_thread(lambda: screen.query_one(
                    "#msg-body", RichLog).write(f"\n[exit {rc}]"))

            threading.Thread(target=job, daemon=True).start()

    def launch_tui() -> int:
        """Blocking entry point for interactive TUI mode."""
        app = JustCompilerApp()
        app.run()
        return 0

else:  # pragma: no cover

    def launch_tui() -> int:
        raise RuntimeError("textual not installed")

