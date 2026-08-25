"""v2.12.0 — engine/TUI decoupling guarantees.

The engine must be embeddable WITHOUT the TUI stack: importing
runner/scanner/jcconfig must never pull in textual or tui.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def test_engine_imports_are_tui_free():
    """Subprocess check: stays valid regardless of what other tests loaded."""
    code = (
        "import sys; import jcconfig, scanner, runner; "
        "assert 'textual' not in sys.modules, 'textual leaked'; "
        "assert 'tui' not in sys.modules, 'tui leaked'; print('clean')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=_REPO,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "clean" in r.stdout


def test_shim_identity_entry_vs_modules():
    import justcompiler as jc
    import runner
    import scanner
    import jcconfig
    assert jc.execute_build is runner.execute_build
    assert jc._summarize is runner._summarize
    assert jc._scan_targets is scanner._scan_targets
    assert jc.load_config is jcconfig.load_config
    assert jc._clean_executables is runner._clean_executables


def test_runner_execute_build_invalid_input(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from runner import execute_build
    res = execute_build(str(tmp_path / "does_not_exist"))
    assert res["status"] == "invalid_input"
    assert res["exit_code"] == 2
    assert res["artifacts_dir"] is None


def test_scanner_detects_node_project(tmp_path):
    from scanner import _scan_targets, _auto_select_target
    (tmp_path / "package.json").write_text('{"name": "demo"}')
    targets = _scan_targets(tmp_path)
    names = {t["name"] for t in targets}
    assert any("Node" in n for n in names)
    sel = _auto_select_target(tmp_path, targets)
    assert sel


def test_version_single_source_in_core():
    import core
    import justcompiler as jc
    import runner
    assert core.VERSION == jc.VERSION == runner.VERSION
