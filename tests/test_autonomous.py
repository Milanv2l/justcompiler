"""Tests for v2.0 Autonomous Build Mode (A1/A2/A3, B1-B3)."""
import json
from pathlib import Path

import pytest

import justcompiler as jc
import docker_manager as dm


# ------------------------------------------------------------- A1: git input

def test_is_git_url_variants():
    assert jc._is_git_url("https://github.com/a/b")
    assert jc._is_git_url("https://github.com/a/b.git")
    assert jc._is_git_url("git@github.com:a/b.git")
    assert jc._is_git_url("ssh://git@host/x/y")
    assert jc._is_git_url("github.com/user/repo")
    assert not jc._is_git_url("/home/me/project")
    assert not jc._is_git_url("./relative")
    assert not jc._is_git_url("C:\\proj")


def test_cache_dest_stable_and_unique():
    a = jc._cache_dest_for("https://GitHub.com/u/Repo/")     # case/slash-insensitive
    b = jc._cache_dest_for("https://github.com/u/repo")
    c = jc._cache_dest_for("https://gitlab.com/u/repo")
    assert a == b and a != c
    assert "repo-" in a.name and a.parent.name == "repos"


def test_clone_to_cache_reuses_and_returns_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(jc, "_cache_dest_for",
                        lambda url: tmp_path / "repos" / "demo-abc")
    dest = tmp_path / "repos" / "demo-abc"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()

    calls = []
    def fake_run(argv, **kw):
        calls.append(list(argv))
        class R:
            returncode = 0; stdout = "main\n"; stderr = ""
        if argv[:3] == ["git", "rev-parse", "--short=8"]:
            R.stdout = "deadbeef\n"
        return R()
    monkeypatch.setattr(jc.subprocess, "run", fake_run)

    path, branch, sha = jc._clone_to_cache("https://x/demo", None)
    assert branch == "default" and sha == "deadbeef"
    joined = [tuple(c[:4]) for c in calls]
    assert ("git", "fetch", "--all", "--prune") in joined   # refresh existing clone
    assert ("git", "pull", "--ff-only", "-q") in joined     # fast-forward
    # no fresh `clone` on the reuse path
    assert not any(c[:2] == ["git", "clone"] for c in calls)


# ------------------------------------------------------------- A3: summary

def test_summarize_shape():
    manifest = {"projects": [{"items": [{"name": "a.bin"}],
                              "runtime_deps": [{"pkg": "GTK4"}, {"pkg": ""}]}]}
    s = jc._summarize("success", "", "T", 21, 12.5, Path("/out"), manifest,
                      commit="cafe1234")
    assert s["status"] == "success" and s["artifacts"] == ["a.bin"]
    assert s["possible_runtime_deps"] == ["GTK4"]
    assert s["toolchain"]["java"] == 21 and s["commit"] == "cafe1234"
    assert s["logs"][0].endswith("build.log")


def test_summarize_empty_manifest():
    s = jc._summarize("build_failed", "oom", "T", None, 0.0, Path("/o"), {})
    assert s["artifacts"] == [] and s["error_class"] == "oom"


def test_error_class_from_missing_log(tmp_path):
    assert jc._error_class_from_log(tmp_path) == ""

# ------------------------------------------------------------- B1: run name

def test_sandbox_flags_still_env_only():
    flags = dm._sandbox_flags(None, {}, None)
    assert "CI=true" in flags and not any("justcompiler_run_" in f for f in flags)

# ------------------------------------------------------------- B2: overrides

def _mk_proj_cfg(tmp_path, data):
    (tmp_path / ".justcompiler.json").write_text(json.dumps(data))

def test_project_config_valid_passthrough(tmp_path):
    _mk_proj_cfg(tmp_path, {"target": "Java (Gradle)", "java_version": 17,
                            "network": False, "env": {"FOO": "bar"}})
    cfg = jc.load_project_config(tmp_path)
    assert cfg["target"] == "Java (Gradle)"
    assert cfg["java_version"] == 17 and cfg["network"] is False
    assert cfg["env"] == {"FOO": "bar"}


def test_project_config_unknown_keys_dropped(tmp_path):
    _mk_proj_cfg(tmp_path, {"target": "Crystal", "bogus_key": 1})
    cfg = jc.load_project_config(tmp_path)
    assert "bogus_key" not in cfg and cfg.get("target") == "Crystal"


def test_project_config_invalid_target_ignored(tmp_path):
    _mk_proj_cfg(tmp_path, {"target": "Nope (Nope)"})
    assert "target" not in jc.load_project_config(tmp_path)


def test_project_config_bad_json_ignored(tmp_path):
    (tmp_path / ".justcompiler.json").write_text("{oops")
    assert jc.load_project_config(tmp_path) == {}


def test_project_config_absent_is_empty(tmp_path):
    assert jc.load_project_config(tmp_path) == {}


# ------------------------------------------------------------- B3: backoff

class _TimeShim:
    """Replace engine's time module ref so sleeps are recorded, not real."""
    def __init__(self, real, recorder):
        self._real = real
        self.recorder = recorder
    def sleep(self, s):
        self.recorder.append(s)
    def __getattr__(self, name):
        return getattr(self._real, name)


def test_network_down_backoff_then_success(tmp_path, make_engine):
    import engine as eng_mod
    src = tmp_path / "src" / "n"; out = tmp_path / "out"
    e = make_engine(src, out)
    (src / "build.txt").write_text("")
    plugin = {"name": "EchoTest", "detect": ["build.txt"], "tool": "echo",
              "cmd_system": "mkdir -p dist && echo x > dist/o.bin",
              "out_dirs": ["dist"], "out_exts": [".bin"], "specificity": 10}
    e.plugins = [plugin]
    sleeps = []
    monkeypatch_time = _TimeShim(eng_mod.time, sleeps)
    eng_mod.time = monkeypatch_time
    try:
        calls = []
        def fake(cmd, cwd):
            calls.append(cmd)
            if len(calls) <= 2:
                return False, ["Could not GET '...'", "Failed to connect: timed out"]
            # success also produces the artifact the real command would create
            d = Path(cwd) / "dist"
            d.mkdir(parents=True, exist_ok=True)
            (d / "o.bin").write_text("x")
            return True, []
        e.run_cmd = fake
        ok = e.run()
    finally:
        eng_mod.time = monkeypatch_time._real
    assert ok and sleeps == [5, 15] and len(calls) == 3


def test_network_down_gives_up_after_two_backoffs(tmp_path, make_engine):
    import engine as eng_mod
    src = tmp_path / "src" / "m"
    e = make_engine(src, tmp_path / "out")
    (src / "build.txt").write_text("")
    plugin = {"name": "EchoTest", "detect": ["build.txt"], "tool": "echo",
              "cmd_system": "true", "out_dirs": [], "out_exts": [],
              "specificity": 10}
    e.plugins = [plugin]
    shim = _TimeShim(eng_mod.time, [])
    eng_mod.time = shim
    try:
        calls = []
        def fake(cmd, cwd):
            calls.append(cmd)
            return False, ["Could not GET 'https://x/pom'",
                           "Failed to connect: Connection refused"]
        e.run_cmd = fake
        e.run()
    finally:
        eng_mod.time = shim._real
    # 1 initial + 2 backoff retries + strategies 2,3 => 5 attempts max
    assert 4 <= len(calls) <= 5
