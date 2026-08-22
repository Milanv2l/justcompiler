import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def make_engine(tmp_path, monkeypatch):
    """Engine factory with constructor side effects neutralized."""
    def _make(src: Path, out: Path, project_name: str = ""):
        src.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        # avoid mutating the user's global git config during Engine.__init__
        import engine as eng_mod
        monkeypatch.setattr(eng_mod.subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 0})())
        from engine import Engine
        return Engine(src, out, test_mode=False, project_name=project_name)
    return _make
