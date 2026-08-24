"""Schema/consistency validation for plugins.json — catches broken releases."""
import json
import re
from pathlib import Path

PLUGINS_PATH = Path(__file__).resolve().parent.parent / "plugins.json"
REQUIRED = {"name", "detect", "tool", "cmd_system", "out_dirs", "out_exts", "specificity"}
PLATFORM_KEYS = ["apt", "pacman", "dnf", "winget", "choco", "scoop"]


def _load():
    return json.loads(PLUGINS_PATH.read_text())


def test_valid_json():
    data = _load()
    assert isinstance(data, list) and len(data) >= 60


def test_names_unique():
    names = [p["name"] for p in _load()]
    assert len(names) == len(set(names))


def test_every_plugin_has_required_fields():
    for p in _load():
        missing = REQUIRED - set(p)
        assert not missing, f"{p.get('name')}: missing {missing}"


def test_detect_lists_nonempty():
    for p in _load():
        assert isinstance(p["detect"], list) and p["detect"], p["name"]
        assert all(isinstance(d, str) and d for d in p["detect"]), p["name"]


def test_specificity_is_int_in_range():
    for p in _load():
        assert isinstance(p["specificity"], int), p["name"]
        assert 1 <= p["specificity"] <= 12, p["name"]


def test_minecraft_plugins_outrank_generic_java():
    # Regression: CNNF case — a NeoForge repo must resolve to the Minecraft
    # plugin, not generic Java (Gradle), even though both match build.gradle dirs.
    by_name = {p["name"]: p["specificity"] for p in _load()}
    for mc in [n for n in by_name if n.startswith("Minecraft")]:
        assert by_name[mc] > by_name["Java (Gradle)"], mc


def test_out_fields_are_lists():
    for p in _load():
        assert isinstance(p["out_dirs"], list) and p["out_dirs"], p["name"]
        assert isinstance(p["out_exts"], list) and p["out_exts"], p["name"]


def test_runtime_deps_structure():
    count = 0
    for p in _load():
        for dep in p.get("runtime_deps", []):
            count += 1
            assert dep.get("pkg"), f"{p['name']}: runtime_dep missing pkg"
            # every dep must be installable somewhere
            assert any(dep.get(k) for k in PLATFORM_KEYS), \
                f"{p['name']}/{dep['pkg']}: no platform package defined"
            for k in PLATFORM_KEYS + ["pkg"]:
                if k in dep:
                    assert isinstance(dep[k], str), f"{p['name']}/{dep['pkg']}.{k} must be str"


def test_runtime_deps_have_windows_fields():
    # Regression: v1.3.9 added winget/choco/scoop; entries must keep them
    for p in _load():
        for dep in p.get("runtime_deps", []):
            for k in ("winget", "choco", "scoop"):
                assert k in dep, f"{p['name']}/{dep['pkg']}: missing '{k}' field"


def test_wellknown_runtimes_have_winget_ids():
    by_pkg = {}
    for p in _load():
        for dep in p.get("runtime_deps", []):
            by_pkg.setdefault(dep["pkg"], dep)
    for runtime, expected in {
        "Java Runtime (JRE)": "Microsoft.OpenJDK",
        ".NET Runtime": "Microsoft.DotNet.Runtime",
        "Node.js": "OpenJS.NodeJS",
    }.items():
        assert expected in by_pkg[runtime]["winget"], runtime


def test_java_plugins_declare_jre_dep():
    for p in _load():
        if p["tool"] in ("gradle", "mvn", "ant", "sbt"):
            pkgs = {d["pkg"] for d in p.get("runtime_deps", [])}
            assert "Java Runtime (JRE)" in pkgs, p["name"]


def test_no_placeholder_commands_left():
    for p in _load():
        cmd = p["cmd_system"]
        assert "TODO" not in cmd and "FIXME" not in cmd, p["name"]


def test_detect_patterns_are_filenames_or_globs():
    for p in _load():
        for d in p["detect"]:
            assert "*" not in d or d.count("*") == 1, p["name"]
            assert not d.startswith("/"), p["name"]
            assert "\\" not in d, p["name"]
