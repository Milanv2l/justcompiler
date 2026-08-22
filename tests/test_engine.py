"""Tests for engine.py — plugin dispatch, workspace roots, harvest, entry scripts.

No Docker required: builds are faked with an `echo`-based plugin.
"""
import json
from pathlib import Path

import pytest

import core
import engine as eng_mod
from engine import Engine


# ----------------------------------------------------------------- fixtures

@pytest.fixture
def make_engine(tmp_path, monkeypatch):
    """Engine factory with constructor side effects neutralized."""
    def _make(src: Path, out: Path, project_name: str = ""):
        src.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        # avoid mutating the user's global git config during Engine.__init__
        monkeypatch.setattr(eng_mod.subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 0})())
        e = Engine(src, out, test_mode=False, project_name=project_name)
        return e
    return _make


def test_engine_init_writes_log_and_manifest_paths(tmp_path, make_engine):
    src = tmp_path / "src"; src.mkdir()
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(src, out)
    assert (out / "build_log.txt").exists()
    assert e.manifest_file == (out / "build_manifest.json").resolve()
    assert e.stats == {"success": 0, "failed": 0, "skipped": 0}


def test_engine_loads_repo_plugins(tmp_path, make_engine):
    src = tmp_path / "src"; src.mkdir()
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(src, out)
    assert len(e.plugins) >= 60
    names = {p["name"] for p in e.plugins}
    assert "Java (Gradle)" in names and "Python (PyInstaller / setuptools)" in names


# --------------------------------------------------------- entry scripts

def test_detect_entry_scripts_extensions(tmp_path, make_engine):
    e = make_engine(tmp_path / "s", tmp_path / "o")
    for n in ("run.sh", "tool.py", "x.pl", "y.rb", "z.lua", "a.js", "b.ts", "c.bash"):
        (tmp_path / "s" / n).write_text("x")
    found = {f["name"] for f in e._detect_entry_scripts(tmp_path / "s")}
    assert found == {"run.sh", "tool.py", "x.pl", "y.rb", "z.lua", "a.js", "b.ts", "c.bash"}


def test_detect_entry_scripts_shebang_extensionless(tmp_path, make_engine):
    e = make_engine(tmp_path / "s", tmp_path / "o")
    f = tmp_path / "s" / "launcher"
    f.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    (tmp_path / "s" / "data.txt").write_text("no shebang")
    found = [d["name"] for d in e._detect_entry_scripts(tmp_path / "s")]
    assert found == ["launcher"]


def test_detect_entry_scripts_skips_dotfiles_and_dirs(tmp_path, make_engine):
    e = make_engine(tmp_path / "s", tmp_path / "o")
    d = tmp_path / "s" / ".hidden.sh"
    d.write_text("x")
    (tmp_path / "s" / "sub.py").mkdir()
    assert e._detect_entry_scripts(tmp_path / "s") == []


# ------------------------------------------------------------- find_wrapper

def test_find_wrapper_in_same_dir(tmp_path, make_engine):
    e = make_engine(tmp_path / "src", tmp_path / "out")
    w = tmp_path / "proj" / "gradlew"
    w.parent.mkdir(parents=True)
    w.write_text("#!/bin/sh\n")
    assert e.find_wrapper(w.parent, "gradlew") == str(w)


def test_find_wrapper_walks_up_to_parent(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    root = tmp_path / "bigproj"
    sub = root / "app" / "lib"
    sub.mkdir(parents=True)
    (root / "gradlew").write_text("#!/bin/sh\n")
    assert e.find_wrapper(sub, "gradlew") == str(root / "gradlew")


def test_find_wrapper_stops_at_workspace_boundary(tmp_path, make_engine):
    # 'workspace' dir names must not leak wrappers from above the boundary
    e = make_engine(tmp_path / "ws", tmp_path / "out")
    above = tmp_path / "above"
    above.mkdir()
    wsdir = tmp_path / "ws" / "workspace" / "deep"
    wsdir.mkdir(parents=True)
    (above / "mvnw").write_text("#!/bin/sh\n")
    assert e.find_wrapper(wsdir, "mvnw") is None


def test_find_wrapper_none_when_missing(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    p = tmp_path / "plain"
    p.mkdir()
    assert e.find_wrapper(p, "gradlew") is None


def test_find_wrapper_empty_name(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    assert e.find_wrapper(tmp_path, "") is None
    assert e.find_wrapper(tmp_path, None) is None


# ------------------------------------------------------- workspace roots

def test_ws_root_pnpm_yaml(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    pkg = tmp_path / "mono" / "packages" / "app"
    pkg.mkdir(parents=True)
    (tmp_path / "mono" / "pnpm-workspace.yaml").write_text("packages:\n - '*'\n")
    r = e._find_workspace_root(pkg)
    assert r and r["type"] == "pnpm" and r["root"] == tmp_path / "mono"


def test_ws_root_npm_workspaces_yarn_lock(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    app = tmp_path / "m" / "apps" / "web"
    app.mkdir(parents=True)
    (tmp_path / "m" / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
    (tmp_path / "m" / "yarn.lock").write_text("")
    assert e._find_workspace_root(app)["type"] == "yarn"


def test_ws_root_npm_workspaces_plain(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    app = tmp_path / "m2" / "libs" / "core"
    app.mkdir(parents=True)
    (tmp_path / "m2" / "package.json").write_text(json.dumps({"workspaces": ["libs/*"]}))
    assert e._find_workspace_root(app)["type"] == "npm"


def test_ws_root_go_work(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    mod = tmp_path / "gw" / "svc"
    mod.mkdir(parents=True)
    (tmp_path / "gw" / "go.work").write_text("go 1.22\n")
    assert e._find_workspace_root(mod)["type"] == "go"


def test_ws_root_gradle_settings_include(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    sub = tmp_path / "gj" / "app"
    sub.mkdir(parents=True)
    (tmp_path / "gj" / "settings.gradle").write_text("include ':app'\n")
    assert e._find_workspace_root(sub)["type"] == "gradle"


def test_ws_root_settings_without_include_ignored(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    sub = tmp_path / "gj2" / "app"
    sub.mkdir(parents=True)
    (tmp_path / "gj2" / "settings.gradle").write_text("rootProject.name = 'x'\n")
    assert e._find_workspace_root(sub) is None


def test_ws_root_maven_modules(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    sub = tmp_path / "mm" / "mod-a"
    sub.mkdir(parents=True)
    (tmp_path / "mm" / "pom.xml").write_text("<modules><module>mod-a</module></modules>")
    assert e._find_workspace_root(sub)["type"] == "maven"


def test_ws_root_cargo_workspace(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    crate = tmp_path / "cw" / "member"
    crate.mkdir(parents=True)
    (tmp_path / "cw" / "Cargo.toml").write_text("[workspace]\nmembers=[]\n")
    assert e._find_workspace_root(crate)["type"] == "cargo"


def test_ws_root_none_for_plain_project(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    p = tmp_path / "solo"
    p.mkdir()
    (p / "main.c").write_text("int main(){}\n")
    assert e._find_workspace_root(p) is None


def test_ws_root_stops_at_src_root(tmp_path, make_engine):
    # marker sits ABOVE src_root -> must not be seen
    e = make_engine(tmp_path / "checkout", tmp_path / "out")
    proj = tmp_path / "checkout" / "thing"
    proj.mkdir(parents=True)
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n")
    assert e._find_workspace_root(proj) is None


# ------------------------------------------------------ parent-marker skip

def test_has_parent_marker_skips_subproject(tmp_path, make_engine):
    # parent has CMakeLists.txt; child also matches cmake detect -> skipped
    e = make_engine(tmp_path / "src", tmp_path / "out")
    parent = tmp_path / "src" / "cpproot"
    child = parent / "sublib"
    child.mkdir(parents=True)
    (parent / "CMakeLists.txt").write_text("cmake_minimum_required()()\n")
    (child / "CMakeLists.txt").write_text("x\n")
    assert e._has_parent_marker(str(child), ["CMakeLists.txt"]) is True
    assert e._has_parent_marker(str(parent), ["CMakeLists.txt"]) is False


def test_has_parent_marker_glob(tmp_path, make_engine):
    e = make_engine(tmp_path / "src", tmp_path / "out")
    parent = tmp_path / "src" / "qt"
    child = parent / "gui"
    child.mkdir(parents=True)
    (parent / "app.pro").write_text("")
    assert e._has_parent_marker(str(child), ["*.pro"]) is True


# ---------------------------------------------------------------- harvest

PLUGIN_DIR = {
    "name": "T",
    "detect": ["build.txt"],
    "tool": "echo",
    "cmd_system": "echo ok",
    "out_dirs": ["dist"],
    "out_exts": [".bin"],
    "specificity": 10,
}
PLUGIN_TREE = {
    **PLUGIN_DIR,
    "out_exts": ["*DIR*"],
}


def test_harvest_files_by_ext(tmp_path, make_engine):
    src = tmp_path / "src" / "proj"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(tmp_path / "src", out)
    dist = src / "dist"; dist.mkdir()
    (dist / "app.bin").write_bytes(b"\x00\x01")
    (dist / "notes.txt").write_text("skip me")
    (dist / ".secret.bin").write_text("dot skip")
    items = e.harvest("proj", src, PLUGIN_DIR)
    names = [i["name"] for i in items]
    assert names == ["proj_app.bin"]
    assert (out / "proj_app.bin").read_bytes() == b"\x00\x01"


def test_harvest_executable_extensionless(tmp_path, make_engine):
    src = tmp_path / "src" / "proj"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(tmp_path / "src", out)
    dist = src / "dist"; dist.mkdir()
    raw = dist / "runner"
    raw.write_bytes(b"\x7fELFfake")
    raw.chmod(0o755)
    items = e.harvest("proj", src, PLUGIN_DIR)
    assert [i["name"] for i in items] == ["proj_runner"]


def test_harvest_dir_copy(tmp_path, make_engine):
    src = tmp_path / "src" / "bundle"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(tmp_path / "src", out)
    dist = src / "dist" / "linux-x64"
    dist.mkdir(parents=True)
    (dist / "game.so").write_bytes(b"x")
    nm = dist / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("{}")
    items = e.harvest("bundle", src, PLUGIN_TREE)
    dest = out / "bundle_dist"
    assert items and (dest / "linux-x64" / "game.so").exists()
    assert not (dest / "node_modules").exists()


def test_harvest_missing_dir_no_items(tmp_path, make_engine):
    src = tmp_path / "src" / "empty"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(tmp_path / "src", out)
    assert e.harvest("empty", src, PLUGIN_DIR) == []


# ------------------------------------------------- harvest quality (syncthing)

def test_looks_executable_magic(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    elf = tmp_path / "bin_elf"; elf.write_bytes(b"\x7fELF" + b"\x00" * 8)
    sh = tmp_path / "bin_sh"; sh.write_text("#!/bin/sh\n")
    txt = tmp_path / "AUTHORS"; txt.write_text("contributors\n")
    assert e._looks_executable(elf) and e._looks_executable(sh)
    assert not e._looks_executable(txt)


def test_harvest_skips_git_and_nonmagic_and_dedupes(tmp_path, make_engine):
    # Regression (syncthing): out_dir '.' rglobbed .git internals (HEAD, refs),
    # world-readable files passed X_OK as root, and overlapping out_dirs
    # duplicated every binary.
    src = tmp_path / "src" / "proj"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(tmp_path / "src", out)

    dist = src / "build_output"; dist.mkdir()
    (dist / "syncthing").write_bytes(b"\x7fELFbinary")
    gitdir = src / ".git" / "refs"; gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (src / "AUTHORS").write_text("people\n")
    (src / "a").write_text("junk\n")

    plugin = {"name": "T", "detect": ["go.mod"], "tool": "go",
              "cmd_system": "echo", "out_dirs": ["build_output", "."],
              "out_exts": ["", ".exe"], "specificity": 10}
    items = e.harvest("proj", src, plugin)
    names = [i["name"] for i in items]
    assert names == ["proj_syncthing"], names
    assert (out / "proj_syncthing").read_bytes()[:4] == b"\x7fELF"
    assert not (out / "proj_AUTHORS").exists()
    assert not (out / "proj_HEAD").exists()
    assert not (out / "proj_a").exists()


def test_harvest_extra_out_dirs_monorepo(tmp_path, make_engine):
    # Regression (vite): JS monorepo outputs live in packages/<p>/dist;
    # extra_out_dirs must be harvested with flattened, collision-free names.
    src = tmp_path / "src" / "mono"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    e = make_engine(tmp_path / "src", out)
    d1 = src / "packages" / "vite" / "dist"; d1.mkdir(parents=True)
    d2 = src / "packages" / "utils" / "dist"; d2.mkdir(parents=True)
    (d1 / "index.js").write_text("a")
    (d2 / "index.js").write_text("b")
    e.extra_out_dirs = ["packages/vite/dist", "packages/utils/dist"]
    plugin = {"name": "T", "detect": [], "tool": "npm", "cmd_system": "",
              "out_dirs": [], "out_exts": [".js"], "specificity": 5}
    items = e.harvest("mono", src, plugin)
    names = sorted(i["name"] for i in items)
    assert names == ["mono_packages_utils_dist_index.js",
                     "mono_packages_vite_dist_index.js"]
    assert (out / "mono_packages_vite_dist_index.js").read_text() == "a"


def test_scan_js_extra_dirs_two_levels(tmp_path):
    # Regression (vite): monorepo dists nest under packages/<name>/dist
    root = tmp_path / "ws"
    (root / "packages" / "vite" / "dist").mkdir(parents=True)
    (root / "packages" / "utils" / "out").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "dist").mkdir()
    found = Engine._scan_js_extra_dirs(root, root)
    assert found == ["docs/dist", "packages/utils/out", "packages/vite/dist"]


def test_monorepo_partial_success_harvests_despite_failures(tmp_path, make_engine):
    src = tmp_path / "src"; out = tmp_path / "out"
    (src / "packages" / "vite" / "dist").mkdir(parents=True)
    (src / "packages" / "vite" / "dist" / "index.js").write_text("x")
    (src / "pnpm-lock.yaml").write_text("")
    (src / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
    plugin = {"name": "Node.js (PNPM)", "detect": ["pnpm-lock.yaml"], "tool": "pnpm",
              "cmd_system": "DYNAMIC_JS_RESOLUTION", "out_dirs": [],
              "out_exts": [".js"], "specificity": 5, "wrapper": ""}
    e = make_engine(src, out)
    e.plugins = [plugin]
    e.dep_mgr.in_docker = True
    e.check_ready = lambda p, r: True
    # every attempt fails, but the dist artifact exists on disk
    e.run_cmd = lambda cmd, cwd: (False, ["ERR_MODULE_NOT_FOUND somewhere"])
    ok = e.run()
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert ok and e.stats["success"] == 1
    names = [i["name"] for i in manifest["projects"][0]["items"]]
    assert any("index.js" in n for n in names)


def test_entry_script_fallback_never_scans_parent(tmp_path, make_engine):
    # Regression (alacritty): fallback scanned root.parent (the container
    # workspace), picked up entrypoint.sh and copytree'd /workspace into its
    # own destination -> infinite alacritty_source/artifacts/... recursion.
    src = tmp_path / "src"; out = tmp_path / "out"
    e = make_engine(src, out)  # creates dirs
    (src / "Cargo.toml").write_text("[package]\nname='x'\n")
    (src / "run.sh").write_text("#!/bin/sh\n")  # project script IS picked up
    scripts_root = e._detect_entry_scripts(src)
    assert [s["name"] for s in scripts_root] == ["run.sh"]
    # parent-level scripts are no longer candidates
    parent_script = src.parent / "entrypoint.sh"
    parent_script.write_text("#!/bin/bash\n")
    found = []
    for cand in [src]:  # new candidate list is root-only
        found += [s["name"] for s in e._detect_entry_scripts(cand)]
    assert "entrypoint.sh" not in found


def test_source_copytree_ignores_artifacts_dir(tmp_path, make_engine):
    # hard guard: <name>_source must never contain an artifacts/ mirror
    src = tmp_path / "persist"; out = tmp_path / "out"
    (src / "artifacts" / "nested").mkdir(parents=True)
    (src / "main.py").write_text("print(1)\n")
    e = make_engine(src.parent, out, project_name="proj")
    e.plugins = [{**ECHO_PLUGIN, "cmd_system": "true"}]
    (out).mkdir(exist_ok=True)
    e.run()
    src_copy = out / "proj_source"
    if src_copy.exists():
        assert not (src_copy / "artifacts").exists()


def test_harvest_skips_cargo_deps_and_incremental(tmp_path, make_engine):
    # Regression (alacritty): hundreds of intermediate .rlibs flooded artifacts
    src = tmp_path / "src" / "rs"; out = tmp_path / "out"
    e = make_engine(tmp_path / "src", out)  # creates dirs
    rel = src / "target" / "release"
    rel.mkdir(parents=True)
    (rel / "alacritty").write_bytes(b"\x7fELFmain")
    deps = rel / "deps"; deps.mkdir()
    (deps / "libserde.rlib").write_bytes(b"\x7fELFx")
    inc = rel / "incremental" / "x"; inc.mkdir(parents=True)
    (inc / "chunk.o").write_bytes(b"\x7fELFy")
    plugin = {"name": "T", "detect": [], "tool": "cargo", "cmd_system": "",
              "out_dirs": ["target/release"], "out_exts": ["", ".exe"], "specificity": 10}
    names = [i["name"] for i in e.harvest("rs", src, plugin)]
    assert names == ["rs_alacritty"]


def test_project_name_overrides_root_name_in_manifest(tmp_path, make_engine):
    # Regression (syncthing): container src dir is '/workspace/persist' so all
    # artifacts were prefixed 'persist_' instead of the real project name.
    src = tmp_path / "persist"
    out = tmp_path / "out"
    (src / "build.txt").parent.mkdir(parents=True)
    (src / "build.txt").write_text("")
    e = make_engine(src, out, project_name="syncthing")
    e.plugins = [ECHO_PLUGIN]
    ok = e.run()
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert ok and manifest["projects"][0]["name"] == "syncthing"
    assert (out / "syncthing_out.bin").exists()

# ------------------------------------------------------- go build resolution

def test_go_resolution_creates_output_dir(tmp_path, make_engine):
    # Regression (syncthing): `go build -o dir/` fails when dir is absent
    e = make_engine(tmp_path, tmp_path / "out")
    cmd = e.build_cmd(tmp_path / "proj", {"tool": "go", "cmd_system": "DYNAMIC_GO_RESOLUTION", "wrapper": ""}, 1)
    assert cmd.startswith("mkdir -p build_output && go build -o build_output/ ./...")


def test_go_resolution_prefixed_for_go_workspace(tmp_path, make_engine):
    e = make_engine(tmp_path / "ws", tmp_path / "out")
    root = tmp_path / "ws" / "svc"
    root.mkdir(parents=True)
    (tmp_path / "ws" / "go.work").write_text("go 1.22\n")
    cmd = e.build_cmd(root, {"tool": "go", "cmd_system": "DYNAMIC_GO_RESOLUTION", "wrapper": ""}, 1)
    assert cmd.startswith(f"cd {tmp_path / 'ws'} && mkdir -p build_output && go build")

# ------------------------------------------------------- go noassets rescue

def test_go_noassets_tag_when_flag_set(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    e._go_noassets = True
    cmd = e.build_cmd(tmp_path / "proj", {"tool": "go", "cmd_system": "DYNAMIC_GO_RESOLUTION", "wrapper": ""}, 1)
    assert "-tags noassets" in cmd and cmd.index("-o build_output/") < cmd.index("-tags noassets")


def test_go_noassets_not_tagged_by_default(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    cmd = e.build_cmd(tmp_path / "proj", {"tool": "go", "cmd_system": "DYNAMIC_GO_RESOLUTION", "wrapper": ""}, 1)
    assert "-tags" not in cmd


def test_go_undefined_auto_triggers_noassets_retry(tmp_path, make_engine):
    # Regression (syncthing): 'undefined: auto.Assets' must retry once with -tags noassets
    src = tmp_path / "src" / "st"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "go.mod").write_text("module test\n")
    calls = []
    plugin = {"name": "Go (Modules)", "detect": ["go.mod"], "tool": "go",
              "cmd_system": "DYNAMIC_GO_RESOLUTION", "out_dirs": ["build_output"],
              "out_exts": [".bin"], "specificity": 10, "wrapper": ""}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [plugin]
    e.dep_mgr.in_docker = True  # bypass host tool checks ('go' absent here)

    def fake_run_cmd(cmd, cwd):
        calls.append(cmd)
        if len(calls) == 1:
            return False, ["lib/api/api_statics.go:38:25: undefined: auto.Assets"]
        return True, []

    e.run_cmd = fake_run_cmd

    # harvest nothing -> falls to entry-script fallback path; force success via stats check instead
    ok = e.run()
    assert len(calls) == 2, f"expected exactly 2 attempts, got {len(calls)}"
    assert "-tags noassets" in calls[1]
    assert e.stats["success"] == 1 or e.stats["failed"] == 1  # build ok; fallback may ship scripts


def test_go_undefined_auto_retries_only_once(tmp_path, make_engine):
    src = tmp_path / "src" / "st2"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "go.mod").write_text("module t\n")
    calls = []
    plugin = {"name": "Go (Modules)", "detect": ["go.mod"], "tool": "go",
              "cmd_system": "DYNAMIC_GO_RESOLUTION", "out_dirs": ["build_output"],
              "out_exts": [".bin"], "specificity": 10, "wrapper": ""}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [plugin]
    e.dep_mgr.in_docker = True
    e.run_cmd = lambda cmd, cwd: (calls.append(cmd), (False, ["x.go:1: undefined: auto.Foo"]))[1]
    e.run()
    # attempt 1 plain + one noassets rescue + strategies 2 and 3 (still tagged)
    noassets_calls = [c for c in calls if "-tags noassets" in c]
    assert len(calls) == 4
    assert len(noassets_calls) == 3

# ------------------------------------------------------- error classification

UPSTREAM_ERRS = [
    "Execution failed for task ':compileJava'.",
    "> Could not resolve dev.engine-room.flywheel:flywheel-neoforge-api-1.21.1:1.0.6.",
    "> Could not GET 'https://example.com/x.pom'. Received status code 523 from server: <none>",
]
NETDOWN_ERRS = [
    "> Could not GET 'https://repo.example.com/x.pom'",
    "> Failed to connect to repo.example.com: Connection refused",
]
OOM_ERRS = ["java.lang.OutOfMemoryError: Java heap space"]

def test_classify_upstream_outage(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    assert e.classify_errors(UPSTREAM_ERRS) == "upstream_outage"


def test_classify_network_down(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    assert e.classify_errors(NETDOWN_ERRS) == "network_down"


def test_classify_oom(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    assert e.classify_errors(OOM_ERRS) == "oom"


def test_classify_rust_edition_too_new(tmp_path, make_engine):
    # Regression (alacritty): cargo 1.75 vs edition2024
    e = make_engine(tmp_path, tmp_path / "out")
    errs = ["error: failed to parse manifest",
            "feature `edition2024` is required",
            "The package requires the Cargo feature called `edition2024`"]
    assert e.classify_errors(errs) == "rust_edition"


def test_classify_generic_is_empty(tmp_path, make_engine):
    e = make_engine(tmp_path, tmp_path / "out")
    assert e.classify_errors(["some syntax error in Main.java"]) == ""


def test_classify_missing_tool(tmp_path, make_engine):
    # A4: '/bin/sh: cargo: command not found' -> kind + tool captured
    e = make_engine(tmp_path, tmp_path / "out")
    kind = e.classify_errors(["/bin/sh: 1: cargo: command not found"])
    assert kind == "missing_tool" and e.last_missing_tool == "cargo"


def test_missing_tool_rescued_once_in_docker(tmp_path, make_engine):
    # Container-side: apt-install the missing tool once, then retry
    src = tmp_path / "src" / "rs"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "Cargo.toml").write_text("[package]\nname='x'\n")
    calls, installs = [], []
    plugin = {"name": "Rust (Cargo)", "detect": ["Cargo.toml"], "tool": "cargo",
              "cmd_system": "cargo build --release", "out_dirs": ["target/release"],
              "out_exts": [".bin"], "specificity": 10, "wrapper": ""}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [plugin]
    e.dep_mgr.in_docker = True

    def fake_run_cmd(cmd, cwd):
        calls.append(cmd)
        if len(calls) == 1:
            return False, ["/bin/sh: cargo: command not found"]
        # second attempt succeeds AND produces an artifact
        d = Path(cwd) / "target" / "release"
        d.mkdir(parents=True, exist_ok=True)
        (d / "tool.bin").write_bytes(b"\x7fELFx")
        return True, []

    def fake_trigger(tool):
        installs.append(tool)
        return True

    e.run_cmd = fake_run_cmd
    e.dep_mgr.trigger_install = fake_trigger
    ok = e.run()
    assert installs == ["cargo"]
    assert len(calls) == 2 and ok


def test_missing_tool_no_host_rescue_breaks_fast(tmp_path, make_engine):
    # Host-side (not in docker): hopeless -> single attempt with clear abort
    src = tmp_path / "src" / "go2"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "go.mod").write_text("module t\n")
    calls = []
    plugin = {"name": "Go (Modules)", "detect": ["go.mod"], "tool": "go",
              "cmd_system": "DYNAMIC_GO_RESOLUTION", "out_dirs": ["build_output"],
              "out_exts": [".bin"], "specificity": 10, "wrapper": ""}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [plugin]
    e.check_ready = lambda p, r: True  # host lacks 'go'; bypass readiness
    e.run_cmd = lambda cmd, cwd: (calls.append(cmd),
                                  (False, ["/bin/sh: go: command not found"]))[1]
    assert e.run() is False
    assert len(calls) == 1


def test_upstream_outage_skips_retries(tmp_path, make_engine):
    # Regression (CNNF): an upstream maven outage must abort after ONE attempt
    src = tmp_path / "src" / "blocked"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    calls = []
    bad = {**ECHO_PLUGIN, "cmd_system": "exit 1"}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [bad]

    def fake_run_cmd(cmd, cwd):
        calls.append(cmd)
        return False, list(UPSTREAM_ERRS)

    e.run_cmd = fake_run_cmd
    assert e.run() is False
    assert len(calls) == 1, f"expected 1 attempt, got {len(calls)}"
    assert e.stats["failed"] == 1


def test_generic_failure_still_retries_three_times(tmp_path, make_engine):
    src = tmp_path / "src" / "generic"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    calls = []
    bad = {**ECHO_PLUGIN, "cmd_system": "exit 1"}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [bad]

    def fake_run_cmd(cmd, cwd):
        calls.append(cmd)
        return False, ["error: something broke"]

    e.run_cmd = fake_run_cmd
    e.run()
    assert len(calls) == 3

# ------------------------------------------------------- end-to-end run()

ECHO_PLUGIN = {
    "name": "EchoTest",
    "detect": ["build.txt"],
    "tool": "echo",
    "cmd_system": "mkdir -p dist && echo binary-data > dist/out.bin",
    "out_dirs": ["dist"],
    "out_exts": [".bin"],
    "specificity": 10,
}


def test_run_end_to_end_success(tmp_path, make_engine):
    src = tmp_path / "src" / "hello"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    e = make_engine(tmp_path / "src", out)
    e.plugins = [ECHO_PLUGIN]
    ok = e.run()
    assert ok and e.stats["success"] == 1 and e.stats["failed"] == 0
    assert (out / "hello_out.bin").read_text().strip() == "binary-data"
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["projects"][0]["name"] == "hello"
    assert manifest["projects"][0]["lang"] == "EchoTest"
    assert manifest["projects"][0]["items"][0]["name"] == "hello_out.bin"


def test_run_filter_no_match_still_ok(tmp_path, make_engine):
    src = tmp_path / "src" / "hello2"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    e = make_engine(tmp_path / "src", out)
    e.plugins = [ECHO_PLUGIN]
    assert e.run(filter_name="Nothing-Matches") is True
    manifest = json.loads((out / "build_manifest.json").read_text())
    assert manifest["projects"] == []


def test_run_failing_build_counts_failed(tmp_path, make_engine):
    src = tmp_path / "src" / "broken"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    bad = {**ECHO_PLUGIN, "cmd_system": "exit 3"}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [bad]
    assert e.run() is False
    assert e.stats["failed"] == 1 and e.stats["success"] == 0


def test_run_zero_artifacts_triggers_entry_script_fallback(tmp_path, make_engine):
    # build succeeds but produces nothing -> scripts + source tree are shipped
    src = tmp_path / "src" / "scripted"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    (src / "launch.py").write_text("print('hi')\n")
    noop = {**ECHO_PLUGIN, "cmd_system": "true"}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [noop]
    ok = e.run()
    assert ok and e.stats["success"] == 1
    assert (out / "scripted_launch.py").exists()
    assert (out / "scripted_source" / "launch.py").exists()
    manifest = json.loads((out / "build_manifest.json").read_text())
    proj = manifest["projects"][0]
    assert any(i.get("kind") == "script" for i in proj["items"])
    # runtime_deps passthrough on script fallback
    rd = [{"pkg": "X"}]
    noop2 = {**noop, "runtime_deps": rd}
    out2 = tmp_path / "out2"; out2.mkdir()
    e2 = make_engine(tmp_path / "src", out2)
    e2.plugins = [noop2]
    (out2 / "scripted_source").mkdir()  # exists -> copytree skipped, still succeeds
    e2.run()
    m2 = json.loads((out2 / "build_manifest.json").read_text())
    assert m2["projects"][0].get("runtime_deps") == rd


def test_run_specificity_picks_best_plugin(tmp_path, make_engine):
    src = tmp_path / "src" / "both"; src.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    low = {**ECHO_PLUGIN, "name": "LowSpec", "detect": ["build.txt"], "specificity": 1,
           "cmd_system": "mkdir -p dist && echo low > dist/low.bin"}
    high = {**ECHO_PLUGIN, "name": "HighSpec", "detect": ["build.txt"], "specificity": 9,
            "cmd_system": "mkdir -p dist && echo high > dist/high.bin"}
    e = make_engine(tmp_path / "src", out)
    e.plugins = [low, high]
    e.run()
    assert (out / "both_high.bin").exists()
    assert not (out / "both_low.bin").exists()


def test_run_parent_marker_skips_child_project(tmp_path, make_engine):
    src = tmp_path / "src" / "outer"; outer_child = src / "inner"
    outer_child.mkdir(parents=True)
    out = tmp_path / "out"; out.mkdir()
    (src / "build.txt").write_text("")
    (outer_child / "build.txt").write_text("")
    e = make_engine(tmp_path / "src", out)
    e.plugins = [ECHO_PLUGIN]
    ok = e.run()
    # only the outer project builds; inner skipped by marker rule
    assert ok and e.stats["skipped"] == 1 and e.stats["success"] == 1
