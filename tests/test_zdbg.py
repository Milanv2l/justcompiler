import json, os
from pathlib import Path
import pytest
import justcompiler as jc

def test_dbg(tmp_path):
    out = tmp_path / "EXECUTABLE" / "f_1"
    out.mkdir(parents=True)
    (out / "build_log.txt").write_text("line\n")
    summ = {"status": "build_failed", "error_class": "oom",
            "target": "T", "artifacts": [], "possible_runtime_deps": []}
    # bypass the silent except: inline-copy of the risky parts
    import traceback
    try:
        rp = jc._write_failure_report(out, summ)
        assert rp, "returned None"
    except AssertionError:
        raise
    except Exception:
        traceback.print_exc()
        raise
