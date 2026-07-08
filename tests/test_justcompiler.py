import unittest
import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from justcompiler import (
    verify_checksum, load_checksums, load_config, save_config,
    _classify_platform, VERSION, CONFIG_FILE
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


if __name__ == "__main__":
    unittest.main()
