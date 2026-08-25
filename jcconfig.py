"""Configuration + checksum helpers (extracted from justcompiler.py, v2.12.0).

Import-safe everywhere: no dependency on the entry script, TUI, or Docker.
"""
import hashlib
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"


def verify_checksum(file_path: str, expected_hash: str) -> bool:
    sha256 = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                sha256.update(chunk)
        return sha256.hexdigest() == expected_hash
    except Exception:
        return False


def load_checksums(file_path: str) -> dict:
    try:
        sums = {}
        for line in Path(file_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                sums[parts[1].lstrip("*")] = parts[0]
        return sums
    except Exception:
        return {}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    default = {"check_updates": True, "run_tests": False, "base_image": "ubuntu:24.04", "theme": "default"}
    try:
        CONFIG_FILE.write_text(json.dumps(default, indent=4), encoding="utf-8")
    except Exception:
        pass
    return default


def save_config(**updates: dict) -> dict:
    config = load_config()
    config.update(updates)
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=4), encoding="utf-8")
    except Exception:
        pass
    return config
