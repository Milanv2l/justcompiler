"""Regression tests for justcompiler.py host-side logic (no Docker/network needed)."""
import json
import sys
import zipfile
from pathlib import Path

import pytest

import justcompiler as jc

unix_only = pytest.mark.skipif(sys.platform == "win32",
                               reason="artifact branch is Unix-only")


# --------------------------------------------------------------- java detect

def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_java_toolchain_of(tmp_path):
    _write(tmp_path, "build.gradle", "java { toolchain { languageVersion = JavaLanguageVersion.of(17) } }")
    assert jc._detect_java_version(tmp_path) == 17


def test_java_enum_version(tmp_path):
    _write(tmp_path, "build.gradle.kts", "sourceCompatibility = JavaVersion.VERSION_11")
    assert jc._detect_java_version(tmp_path) == 11


def test_java_string_compat_takes_max(tmp_path):
    _write(tmp_path, "build.gradle", 'sourceCompatibility = "1.8"\ntargetCompatibility = 21')
    assert jc._detect_java_version(tmp_path) == 21


def test_java_maven_release(tmp_path):
    _write(tmp_path, "pom.xml", "<properties><maven.compiler.release>11</maven.compiler.release></properties>")
    assert jc._detect_java_version(tmp_path) == 11


def test_java_maven_property(tmp_path):
    _write(tmp_path, "pom.xml", "<java.version>17</java.version>")
    assert jc._detect_java_version(tmp_path) == 17


def test_java_dot_java_version_file(tmp_path):
    _write(tmp_path, ".java-version", "21\n")
    assert jc._detect_java_version(tmp_path) == 21


def test_java_old_wrapper_caps_at_17(tmp_path):
    _write(tmp_path, "gradle/wrapper/gradle-wrapper.properties",
           "distributionUrl=https\\://services.gradle.org/distributions/gradle-7.6-bin.zip")
    assert jc._detect_java_version(tmp_path) == 17


def test_java_new_wrapper_caps_at_21(tmp_path):
    _write(tmp_path, "gradle/wrapper/gradle-wrapper.properties",
           "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.9-bin.zip")
    assert jc._detect_java_version(tmp_path) == 21


def test_java_no_project_returns_none(tmp_path):
    assert jc._detect_java_version(tmp_path) is None


def test_java_ignores_build_dir(tmp_path):
    # Regression: files inside build/ (skipped dir) must not influence detection
    _write(tmp_path, "build/build.gradle", "jvmToolchain(25)")
    assert jc._detect_java_version(tmp_path) is None


def test_java_max_of_multiple_declarations(tmp_path):
    _write(tmp_path, "pom.xml", "<maven.compiler.source>8</maven.compiler.source>")
    _write(tmp_path, "build.gradle", "jvmToolchain(21)")
    assert jc._detect_java_version(tmp_path) == 21


# ------------------------------------------------------------ error matching

DEPS = {
    ("GTK4", "libgtk-4-dev gir1.2-gtk-4.0", "gtk4", "gtk4-devel", "", "", ""),
    ("AyatanaAppIndicator", "gir1.2-ayatanaappindicator3-0.1",
     "libayatana-appindicator", "libayatana-appindicator-gtk3", "", "", ""),
    ("PyGObject (GTK)", "gir1.2-gtk-3.0 python3-gi", "python-gobject gtk3",
     "python3-gobject gtk3", "", "", ""),
    ("PyQt5", "python3-pyqt5", "python-pyqt5", "python3-qt5", "", "", ""),
    ("Node.js", "nodejs", "nodejs", "nodejs", "OpenJS.NodeJS.LTS", "nodejs", "nodejs"),
    ("Java Runtime (JRE)", "default-jre", "jre-openjdk", "java-latest-openjdk",
     "Microsoft.OpenJDK.17", "openjdk", "openjdk"),
    ("SDL2", "libsdl2-dev", "sdl2", "SDL2-devel", "", "", ""),
    (".NET Runtime", "dotnet-runtime-8.0", "dotnet-runtime", "dotnet-runtime-8.0",
     "Microsoft.DotNet.Runtime.8", "dotnet-runtime", "dotnet-sdk"),
}


def _match_names(text):
    return [d[0] for d in jc._match_error_to_deps(text, DEPS)]


def test_match_namespace_not_available():
    # Regression: real faugus-launcher failure
    names = _match_names("ValueError: Namespace AyatanaAppIndicator3 not available")
    assert "AyatanaAppIndicator" in names


def test_match_module_not_found_gi():
    names = _match_names("ModuleNotFoundError: No module named 'gi'")
    assert "PyGObject (GTK)" in names


def test_match_module_not_found_pyqt():
    names = _match_names("ModuleNotFoundError: No module named 'PyQt5'")
    assert "PyQt5" in names
    assert "Node.js" not in names


def test_match_shared_library_error_phrase():
    names = _match_names("error while loading shared libraries: libSDL2-2.0.so.0: cannot open shared object file")
    assert "SDL2" in names


def test_match_importerror_shared_object():
    names = _match_names("ImportError: libgtk-4.so.1: cannot open shared object file: No such file or directory")
    assert "GTK4" in names


def test_match_command_not_found_with_sh_prefix():
    names = _match_names("/bin/sh: node: command not found")
    assert "Node.js" in names


def test_match_windows_not_recognized():
    names = _match_names("'dotnet' is not recognized as an internal or external command")
    assert ".NET Runtime" in names


def test_match_class_file_major_version():
    # Regression: the Gradle/Java-25 incident
    names = _match_names("BUG! exception in phase 'semantic analysis' ... Unsupported class file major version 69")
    assert "Java Runtime (JRE)" in names


def test_match_compiled_by_more_recent_version():
    names = _match_names("class file has been compiled by a more recent version of the Java Runtime (class file version 65.0)")
    assert "Java Runtime (JRE)" in names


def test_no_match_returns_empty():
    assert _match_names("some totally unrelated error about widgets") == []


def test_error_token_expansion_gi():
    toks = jc._extract_error_tokens("No module named 'gi'")
    assert "pygobject" in toks and "gi" in toks


def test_norm_token_strips_non_letters():
    assert jc._norm_token("libgtk-4.so.1") == "libgtkso"


def test_dep_tokens_includes_lib_stripped_variant():
    toks = jc._dep_tokens(("SDL2", "libsdl2-dev", "sdl2", "SDL2-devel", "", "", ""))
    assert "sdl" in toks and "sdldev" in toks


# --------------------------------------------------------- install commands

def test_build_install_cmd_apt_combined():
    cmds = jc._build_install_cmds(DEPS, "apt")
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[0] == "sudo" and cmd[1] == "apt" and "-y" in cmd
    pkgs = [a for a in cmd if not a.startswith("-")][2:]
    assert "libgtk-4-dev" in pkgs and "nodejs" in pkgs
    # dedup: nodejs appears in only one dep but must not be repeated
    assert len(pkgs) == len(set(pkgs))


def test_build_install_cmd_winget_per_package():
    # Regression: winget cannot install multiple ids in one command
    cmds = jc._build_install_cmds(DEPS, "winget")
    ids = {c[c.index("--id") + 1] for c in cmds}
    assert ids == {"Microsoft.OpenJDK.17", "Microsoft.DotNet.Runtime.8", "OpenJS.NodeJS.LTS"}
    for c in cmds:
        assert c[0] == "winget" and "--id" in c and "-e" in c
        assert not any(x == "sudo" for x in c)


def test_build_install_cmd_pacman_noconfirm():
    cmds = jc._build_install_cmds({("SDL2", "", "sdl2", "", "", "", "")}, "pacman")
    assert cmds[0][-1] == "sdl2" and "--noconfirm" in cmds[0]


def test_build_install_cmd_empty_deps():
    assert jc._build_install_cmds(set(), "apt") == []
    assert jc._build_install_cmds({("X", "", "", "", "", "", "")}, "apt") == []


def test_filter_installed(monkeypatch):
    class R:
        def __init__(self, rc):
            self.returncode = rc
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return R(0 if argv[-1] == "bash" else 1)

    monkeypatch.setattr(jc.subprocess, "run", fake_run)
    missing = jc._filter_installed(["bash", "not-installed-pkg"], "dnf")
    assert missing == ["not-installed-pkg"]


def test_filter_installed_windows_passthrough(monkeypatch):
    monkeypatch.setattr(jc.platform, "system", lambda: "Windows")
    assert jc._filter_installed(["anything"], "winget") == ["anything"]


# --------------------------------------------------------------- artifacts

def _make_jar(path: Path, members):
    with zipfile.ZipFile(path, "w") as z:
        for m in members:
            z.writestr(m, "data")


def test_classify_jar_fabric_mod(tmp_path):
    jar = tmp_path / "x.jar"
    _make_jar(jar, ["fabric.mod.json"])
    assert jc._classify_jar(jar) == "mod"


def test_classify_jar_bukkit_plugin(tmp_path):
    jar = tmp_path / "x.jar"
    _make_jar(jar, ["plugin.yml"])
    assert jc._classify_jar(jar) == "plugin"


def test_classify_jar_bungee(tmp_path):
    jar = tmp_path / "x.jar"
    _make_jar(jar, ["bungee.yml"])
    assert jc._classify_jar(jar) == "bungee-plugin"


def test_classify_jar_velocity(tmp_path):
    # Regression: velocity-plugin.json detection
    jar = tmp_path / "x.jar"
    _make_jar(jar, ["velocity-plugin.json"])
    assert jc._classify_jar(jar) == "velocity-plugin"


def test_classify_jar_forge_mod(tmp_path):
    jar = tmp_path / "x.jar"
    _make_jar(jar, ["META-INF/mods.toml"])
    assert jc._classify_jar(jar) == "mod"


def test_classify_jar_plain(tmp_path):
    jar = tmp_path / "x.jar"
    _make_jar(jar, ["some.class"])
    assert jc._classify_jar(jar) == "jar"


def test_matching_source_dir():
    src = Path("/fake/proj_source")
    name, cwd = jc._matching_source_dir(Path("/fake/proj_faugus_run.py"), [src])
    assert name == "faugus_run.py" and cwd == str(src)


def test_matching_source_dir_no_match():
    assert jc._matching_source_dir(Path("/fake/other.py"), [Path("/fake/proj_source")]) == (None, None)


def test_scan_artifacts_python_with_source(tmp_path):
    src = tmp_path / "proj_source"
    src.mkdir()
    (src / "app.py").write_text("print('hi')\n")
    (tmp_path / "proj_app.py").write_text("print('hi')\n")
    arts = jc._scan_artifacts(tmp_path)
    py = [a for a in arts if a.kind == "python"]
    assert py and py[0].cmd[1] == "app.py" and py[0].cwd == str(src)


def test_scan_artifacts_skips_dotfiles_and_empty(tmp_path):
    # Regression: .gitignore was misdetected as a runnable script
    (tmp_path / ".gitignore").write_text("x")
    (tmp_path / "empty.py").write_text("")
    (tmp_path / "real.py").write_text("print(1)\n")
    arts = jc._scan_artifacts(tmp_path)
    names = [a.name for a in arts]
    assert ".gitignore" not in names and "empty.py" not in names
    assert "real.py" in names


@unix_only
def test_scan_artifacts_shebang_extensionless(tmp_path):
    f = tmp_path / "tool"
    f.write_text("#!/bin/bash\necho hi\n")
    f.chmod(0o755)
    arts = jc._scan_artifacts(tmp_path)
    kinds = {a.kind for a in arts if a.name == "tool"}
    assert "script" in kinds


@unix_only
def test_scan_artifacts_elf_binary(tmp_path):
    f = tmp_path / "prog"
    f.write_bytes(b"\x7fELF" + b"\x00" * 16)
    arts = jc._scan_artifacts(tmp_path)
    assert any(a.kind == "binary" and a.name == "prog" for a in arts)


def test_scan_artifacts_js(tmp_path):
    (tmp_path / "main.js").write_text("console.log(1)\n")
    arts = jc._scan_artifacts(tmp_path)
    a = next(a for a in arts if a.name == "main.js")
    assert a.cmd[0] == "node"


def test_scan_artifacts_ignores_source_dirs_themselves(tmp_path):
    src = tmp_path / "proj_source"
    src.mkdir()
    (src / "app.py").write_text("print('hi')\n")
    arts = jc._scan_artifacts(tmp_path)
    assert all(a.kind != "python" for a in arts)


# ------------------------------------------------------------ target scan

def test_scan_targets_finds_gradle(tmp_path):
    _write(tmp_path, "build.gradle", "// empty")
    targets = jc._scan_targets(tmp_path)
    assert any(t["name"] == "Java (Gradle)" for t in targets)


def test_scan_targets_prefers_minecraft_marker(tmp_path):
    # fabric.mod.json alone must trigger the Minecraft mod plugin
    _write(tmp_path, "fabric.mod.json", "{}")
    targets = jc._scan_targets(tmp_path)
    names = [t["name"] for t in targets]
    assert "Minecraft Mod (Fabric/Forge/Quilt)" in names


def test_scan_targets_gradle_and_mod_markers(tmp_path):
    # Regression (CNNF): repo with build.gradle at root + neoforge.mods.toml in
    # resources must select the Minecraft mod plugin, specificity-weighted.
    _write(tmp_path, "build.gradle", "")
    _write(tmp_path, "src/main/resources/META-INF/neoforge.mods.toml", "")
    targets = jc._scan_targets(tmp_path)
    assert jc._auto_select_target(tmp_path, targets) == "Minecraft Mod (Fabric/Forge/Quilt)"


def test_auto_select_pure_java_still_gradle(tmp_path):
    _write(tmp_path, "build.gradle", "")
    _write(tmp_path, "src/Main.java", "class Main{}")
    targets = jc._scan_targets(tmp_path)
    assert jc._auto_select_target(tmp_path, targets) == "Java (Gradle)"


def test_auto_select_pnpm_lock_beats_scattered_package_json(tmp_path):
    # Regression (vite): package.json appears in every monorepo subdir and
    # outnumbers pnpm-lock.yaml; root markers must dominate.
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "pnpm-lock.yaml", "")
    _write(tmp_path, "pnpm-workspace.yaml", "packages:\n")
    for sub in ("packages/a", "packages/b", "docs", "scripts"):
        _write(tmp_path, sub + "/package.json", "{}")
    _write(tmp_path, "packages/a/vite.config.ts", "")
    targets = jc._scan_targets(tmp_path)
    assert jc._auto_select_target(tmp_path, targets) == "Node.js (PNPM)"


def test_auto_select_single_target(tmp_path):
    _write(tmp_path, "pom.xml", "<project/>")
    targets = jc._scan_targets(tmp_path)
    assert jc._auto_select_target(tmp_path, targets) == "Java (Maven)"


def test_auto_select_by_marker_count(tmp_path):
    _write(tmp_path, "pom.xml", "<project/>")
    _write(tmp_path, "build.gradle", "")
    targets = jc._scan_targets(tmp_path)
    chosen = jc._auto_select_target(tmp_path, targets)
    assert chosen in ("Java (Maven)", "Java (Gradle)")


def test_auto_select_empty_targets():
    assert jc._auto_select_target(Path("/nonexistent"), []) == ""


# --------------------------------------------------------------- checksums

def test_checksum_roundtrip(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello world")
    digest = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    sums = tmp_path / "checksums.txt"
    sums.write_text(f"{digest}  file.bin\n")
    loaded = jc.load_checksums(sums)
    assert loaded.get("file.bin") == digest
    assert jc.verify_checksum(f, digest)
    assert not jc.verify_checksum(f, "0" * 64)


def test_load_checksums_ignores_comments_and_blank(tmp_path):
    sums = tmp_path / "c.txt"
    sums.write_text("# comment\n\n  \nabc  x.bin\n")
    assert jc.load_checksums(sums) == {"x.bin": "abc"}


# ------------------------------------------------------------- mem clamp

def test_available_mem_gb_linux(monkeypatch):
    fake = "MemTotal:       16000000 kB\nMemAvailable:    2097152 kB\nBuffers:         100000 kB\n"
    monkeypatch.setattr(Path, "read_text", lambda self: fake if self.name == "meminfo" else "")
    assert jc._available_mem_gb() == 2.0


def test_available_mem_gb_missing_file(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self: (_ for _ in ()).throw(OSError()))
    v = jc._available_mem_gb()
    assert v is None or isinstance(v, float)


def test_heap_clamp_formula():
    # mirrors the inline formula in __main__: max(2, min(12, int(avail*0.7)))
    for avail, expect in [(0.5, 2), (2.0, 2), (4.0, 2), (6.0, 4), (20.0, 12), (30.0, 12)]:
        heap = max(2, min(12, int(avail * 0.7)))
        assert heap == expect, avail

# ------------------------------------------------------- main-artifact score

def test_test_binaries_demoted_in_picker():
    # Regression (fmt): CMake test-suite binaries outranked everything
    assert jc._is_main_artifact("fmt_args-test", "binary", 500_000) is False
    assert jc._is_main_artifact("foo.test", "binary", 500_000) is False
    assert jc._is_main_artifact("alacritty", "binary", 5_000_000) is True


# ------------------------------------------------------------ version sync

def test_version_txt_matches_constant():
    # Regression: v1.3.7 shipped with version.txt and VERSION drifted apart
    ver_file = (Path(__file__).resolve().parent.parent / "version.txt").read_text().strip()
    assert ver_file == jc.VERSION, f"version.txt ({ver_file}) != VERSION ({jc.VERSION})"


def test_version_is_semver():
    parts = jc.VERSION.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts)


# ------------------------------------------------------------- config i/o

def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(jc, "CONFIG_FILE", tmp_path / "config.json")
    cfg = jc.load_config()
    assert cfg["check_updates"] is True
    jc.save_config(lang="nl", run_tests=True)
    cfg2 = jc.load_config()
    assert cfg2["lang"] == "nl" and cfg2["run_tests"] is True and cfg2["check_updates"] is True
