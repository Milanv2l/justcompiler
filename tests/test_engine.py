import unittest
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import Engine


class TestEngineInit(unittest.TestCase):
    def setUp(self):
        self.temp_src = Path(tempfile.mkdtemp())
        self.temp_out = Path(tempfile.mkdtemp())
        (self.temp_src / "package.json").write_text('{"name": "test"}')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_src, ignore_errors=True)
        shutil.rmtree(self.temp_out, ignore_errors=True)

    def test_engine_creates_log_file(self):
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        self.assertTrue(engine.log_file.exists())
        self.assertFalse(engine.manifest_file.exists(),
                         "manifest_file wordt pas aangemaakt na run()")

    def test_engine_manifest_default(self):
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        self.assertIn("build_time_utc", engine.manifest_data)
        self.assertIn("projects", engine.manifest_data)

    def test_engine_stats_defaults(self):
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        self.assertEqual(engine.stats["success"], 0)
        self.assertEqual(engine.stats["failed"], 0)
        self.assertEqual(engine.stats["skipped"], 0)

    def test_engine_loads_plugins(self):
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        self.assertGreater(len(engine.plugins), 0)
        self.assertIn("name", engine.plugins[0])
        self.assertIn("tool", engine.plugins[0])


class TestEngineFindWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp_src = Path(tempfile.mkdtemp())
        self.temp_out = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_src, ignore_errors=True)
        shutil.rmtree(self.temp_out, ignore_errors=True)

    def test_no_workspace_file(self):
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        result = engine._find_workspace_root(self.temp_src)
        self.assertIsNone(result)

    def test_go_workspace(self):
        (self.temp_src / "go.work").write_text("go 1.21\n")
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        result = engine._find_workspace_root(self.temp_src)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "go")

    def test_pnpm_workspace(self):
        (self.temp_src / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        result = engine._find_workspace_root(self.temp_src)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "pnpm")


class TestEngineDetectEntryScripts(unittest.TestCase):
    def setUp(self):
        self.temp_src = Path(tempfile.mkdtemp())
        self.temp_out = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_src, ignore_errors=True)
        shutil.rmtree(self.temp_out, ignore_errors=True)

    def test_detect_py_script(self):
        (self.temp_src / "main.py").write_text("print('hello')\n")
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        scripts = engine._detect_entry_scripts(self.temp_src)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["name"], "main.py")

    def test_detect_sh_script(self):
        (self.temp_src / "run.sh").write_text("#!/bin/bash\necho hi\n")
        os.chmod(self.temp_src / "run.sh", 0o755)
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        scripts = engine._detect_entry_scripts(self.temp_src)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["name"], "run.sh")

    def test_ignores_dotfiles(self):
        (self.temp_src / ".hidden.py").write_text("print('hidden')\n")
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        scripts = engine._detect_entry_scripts(self.temp_src)
        self.assertEqual(len(scripts), 0)

    def test_detect_shebang_script(self):
        (self.temp_src / "custom_script").write_text("#!/usr/bin/env python3\nprint('hello')\n")
        os.chmod(self.temp_src / "custom_script", 0o755)
        engine = Engine(self.temp_src, self.temp_out, test_mode=False)
        scripts = engine._detect_entry_scripts(self.temp_src)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["name"], "custom_script")


if __name__ == "__main__":
    unittest.main()
