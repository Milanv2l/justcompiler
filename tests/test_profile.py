"""Tests for the slim/full base-image profile split (C9)."""
import docker_manager as dm


def test_full_profile_has_all_toolchains():
    df = dm._base_dockerfile("ubuntu:24.04", "full")
    for pkg in ("openjdk-25-jdk", "golang", "cargo", "gradle", "valac",
                "libgtk-3-dev", "nodejs", "dotnet"):
        assert pkg in df, pkg


def test_slim_profile_is_minimal():
    df = dm._base_dockerfile("ubuntu:24.04", "slim")
    for essential in ("rsync", "python3-venv", "build-essential", "openjdk-21-jdk"):
        assert essential in df, essential
    for heavy in ("openjdk-25-jdk", "openjdk-8-jdk", "openjdk-17-jdk", "golang",
                  "gradle", "valac", "crystal", "nodejs", "dotnet",
                  "libgtk-3-dev"):
        assert heavy not in df, f"slim must not contain {heavy}"


def test_both_profiles_ship_current_rust():
    # Regression (alacritty): distro cargo is too old for edition2024;
    # both profiles must bootstrap rustup instead of apt cargo.
    for profile in ("full", "slim"):
        df = dm._base_dockerfile("ubuntu:24.04", profile)
        assert "sh.rustup.rs" in df, profile
        assert "/root/.cargo/bin" in df, profile
    full = dm._base_dockerfile("ubuntu:24.04", "full")
    assert " golang cargo" not in full  # rustup replaces distro cargo


def test_profiles_produce_different_content_and_hash_inputs():
    full = dm._base_dockerfile("ubuntu:24.04", "full")
    slim = dm._base_dockerfile("ubuntu:24.04", "slim")
    assert full != slim
    # hash is computed over (base_image + content): distinct images per profile
    h_full = __import__("hashlib").sha256(("ubuntu:24.04" + full).encode()).hexdigest()[:12]
    h_slim = __import__("hashlib").sha256(("ubuntu:24.04" + slim).encode()).hexdigest()[:12]
    assert h_full != h_slim


def test_unknown_profile_falls_back_to_full():
    assert dm._base_dockerfile("ubuntu:24.04", "bogus") == dm._base_dockerfile("ubuntu:24.04", "full")
