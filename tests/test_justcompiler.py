import unittest
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from justcompiler import (
    verify_checksum, load_checksums, load_config, save_config,
    _classify_platform, _classify_jar, _is_main_artifact,
    _size_str, _scan_artifacts, ArtifactInfo,
    VERSION, CONFIG_FILE
)


class TestVerifyChecksum(unittest.TestCase):
    def test_valid_checksum(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            fname = f.name
        import hashlib
        expected = hashlib.sha256(b"hello world").hexdigest()
        self.assertTrue(verify_checksum(fname, expected))
        os.unlink(fname)

    def test_invalid_checksum(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            fname = f.name
        self.assertFalse(verify_checksum(fname, "invalidhash"))
        os.unlink(fname)

    def test_missing_file(self):
        self.assertFalse(verify_checksum("/nonexistent/file", "abc"))


class TestLoadChecksums(unittest.TestCase):
    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("")
            fname = f.name
        self.assertEqual(load_checksums(fname), {})
        os.unlink(fname)

    def test_valid_checksums(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("abc123  file1.py\n")
            f.write("def456 *file2.py\n")
            fname = f.name
        result = load_checksums(fname)
        self.assertEqual(result, {"file1.py": "abc123", "file2.py": "def456"})
        os.unlink(fname)

    def test_comment_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("# this is a comment\n")
            f.write("abc123  file1.py\n")
            fname = f.name
        result = load_checksums(fname)
        self.assertEqual(result, {"file1.py": "abc123"})
        os.unlink(fname)


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        self.orig_config = None
        if CONFIG_FILE.exists():
            self.orig_config = CONFIG_FILE.read_text()

    def tearDown(self):
        if self.orig_config is not None:
            CONFIG_FILE.write_text(self.orig_config)

    def test_load_config_returns_dict(self):
        config = load_config()
        self.assertIsInstance(config, dict)
        self.assertIn("check_updates", config)

    def test_load_config_default_values(self):
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
        config = load_config()
        self.assertIn("check_updates", config)
        self.assertIn("base_image", config)
        self.assertEqual(config["base_image"], "ubuntu:24.04")


class TestSaveConfig(unittest.TestCase):
    def setUp(self):
        self.orig_config = None
        if CONFIG_FILE.exists():
            self.orig_config = CONFIG_FILE.read_text()

    def tearDown(self):
        if self.orig_config is not None:
            CONFIG_FILE.write_text(self.orig_config)

    def test_save_and_load(self):
        save_config(check_updates=False)
        config = load_config()
        self.assertFalse(config["check_updates"])

    def test_save_preserves_other_keys(self):
        save_config(lang="nl")
        config = load_config()
        self.assertEqual(config["lang"], "nl")
        self.assertIn("check_updates", config)


class TestClassifyPlatform(unittest.TestCase):
    def test_unknown_tool(self):
        result = _classify_platform("/tmp", "Unknown", "")
        self.assertEqual(result, "Unknown")

    def test_nodejs_detection(self):
        result = _classify_platform("/tmp", "Node.js (npm)", "npm")
        self.assertEqual(result, "Node.js App")

    def test_go_detection(self):
        result = _classify_platform("/tmp", "Go", "go")
        self.assertEqual(result, "Go App")

    def test_rust_detection(self):
        result = _classify_platform("/tmp", "Rust (Cargo)", "cargo")
        self.assertEqual(result, "Rust App")

    def test_java_detection(self):
        result = _classify_platform("/tmp", "Java (Maven)", "mvn")
        self.assertEqual(result, "Java Library")

    def test_python_detection(self):
        result = _classify_platform("/tmp", "Python (pip)", "pip")
        self.assertEqual(result, "Python App")

    def test_kotlin_detection(self):
        result = _classify_platform("/tmp", "Kotlin (Gradle)", "gradle")
        self.assertEqual(result, "Kotlin App")

    def test_dart_flutter(self):
        result = _classify_platform("/tmp", "Flutter", "flutter")
        self.assertEqual(result, "Dart/Flutter App")


class TestVersion(unittest.TestCase):
    def test_version_string(self):
        self.assertIsInstance(VERSION, str)
        self.assertGreater(len(VERSION), 0)
        parts = VERSION.split(".")
        self.assertEqual(len(parts), 3)


class TestSizeStr(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(_size_str(500), "500 B")

    def test_kilobytes(self):
        self.assertEqual(_size_str(2048), "2.0 KB")

    def test_megabytes(self):
        self.assertEqual(_size_str(2097152), "2.0 MB")

    def test_edge_zero(self):
        self.assertEqual(_size_str(0), "0 B")


class TestClassifyJar(unittest.TestCase):
    def test_regular_jar(self):
        with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as f:
            f.write(b"PK\x03\x04")  # empty zip
            fname = f.name
        # Empty zip won't have any of the marker files
        result = _classify_jar(Path(fname))
        self.assertEqual(result, "jar")
        os.unlink(fname)

    def test_missing_file(self):
        result = _classify_jar(Path("/nonexistent.jar"))
        self.assertEqual(result, "jar")


class TestIsMainArtifact(unittest.TestCase):
    def test_binary_large_is_main(self):
        self.assertTrue(_is_main_artifact("myapp", "binary", 5 * 1024 * 1024))

    def test_library_jar_not_main(self):
        self.assertFalse(_is_main_artifact("libcommon.jar", "jar", 50000))

    def test_small_python_script_not_main(self):
        self.assertFalse(_is_main_artifact("test_helper.py", "python", 200))

    def test_plugin_jar_is_main(self):
        self.assertTrue(_is_main_artifact("myplugin.jar", "plugin", 100000))

    def test_sources_jar_not_main(self):
        self.assertFalse(_is_main_artifact("myapp-sources.jar", "jar", 2 * 1024 * 1024))

    def test_main_keyword_in_name(self):
        self.assertTrue(_is_main_artifact("myapp-main.jar", "jar", 500000))

    def test_node_app_is_main(self):
        self.assertTrue(_is_main_artifact("server.js", "node", 500 * 1024))

    def test_example_file_not_main(self):
        self.assertFalse(_is_main_artifact("example.py", "python", 3000))


class TestScanArtifacts(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_empty_folder_returns_empty(self):
        result = _scan_artifacts(self.test_dir)
        self.assertEqual(result, [])

    def test_detects_python_script(self):
        (self.test_dir / "main.py").write_text("print('hello')\n")
        result = _scan_artifacts(self.test_dir)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, "python")
        self.assertEqual(result[0].name, "main.py")

    def test_detects_multiple_artifacts(self):
        (self.test_dir / "app.py").write_text("print('hello')\n")
        (self.test_dir / "helper.py").write_text("print('helper')\n")
        result = _scan_artifacts(self.test_dir)
        self.assertGreaterEqual(len(result), 1)

    def test_skips_hidden_dirs(self):
        hidden = self.test_dir / "__pycache__"
        hidden.mkdir()
        (hidden / "cached.py").write_text("x=1\n")
        result = _scan_artifacts(self.test_dir)
        self.assertEqual(result, [])

    def test_artifact_has_correct_fields(self):
        (self.test_dir / "main.py").write_text("print('test')\n")
        result = _scan_artifacts(self.test_dir)
        self.assertEqual(len(result), 1)
        a = result[0]
        self.assertIsInstance(a.name, str)
        self.assertIsInstance(a.kind, str)
        self.assertIsInstance(a.cmd, list)
        self.assertIsInstance(a.size, int)
        self.assertIsInstance(a.desc, str)
        self.assertIsInstance(a.is_main, bool)


if __name__ == "__main__":
    unittest.main()
