# JustCompiler

Compile and run projects in any language inside an isolated Docker sandbox — with automatic project detection, Java version matching, runtime dependency hints, and one-command auto-install on the host.

## Features

- **62 language/platform plugins** — C/C++, Python, Rust, Go, Java, Kotlin, Minecraft mods & plugins, Node.js, .NET, Flutter, Godot, Vala, Zig, Haskell, Elixir, and more
- **Automatic target detection** — scans your project, picks the right build plugin (specificity-based), detects modloaders (Fabric/Forge/NeoForge/Quilt/Bukkit/Velocity)
- **Isolated builds** — everything compiles inside Docker; only artifacts come out
- **Smart runtime dependency hints** — when a built program fails to start, JustCompiler parses the actual error output (`ModuleNotFoundError`, `Namespace ... not available`, `error while loading shared libraries`, `command not found`) and shows *only* the dependencies that match
- **One-key auto-install** — offers to install missing host dependencies via `apt` / `pacman` / `dnf` / `zypper` (Linux) or `winget` / `choco` / `scoop` (Windows). Packages that are already installed are skipped automatically.
- **Java version auto-detection** — reads `build.gradle(.kts)` toolchains, `sourceCompatibility`, Maven `<release>`, `.java-version`, or caps by Gradle wrapper age, then boots the container with exactly that JDK (8/17/21/25 preinstalled)
- **Entry-script fallback** — if a build produces no binaries, source scripts are detected, packaged together with the full source tree, and run from the correct working directory so imports resolve
- **Build & run logs** — every build writes `build.log`; every run writes `run.log` into the build output folder
- **Content-hashed images** — base and engine Docker images rebuild automatically when their definition changes; stale old images are pruned automatically (last 2 kept)

## Install

```bash
git clone https://github.com/Milanv2l/justcompiler.git
cd justcompiler
./justcompiler.py            # or: python3 justcompiler.py
```

Requirements: Python 3.10+, [Docker](https://docs.docker.com/get-docker/) (Linux: rootless or sudo-capable; Windows/macOS: Docker Desktop).

The first launch downloads/builds the sandbox base image (~5–10 min, once per image change). Package caches (Gradle, Maven, npm, pip, cargo) persist on the host between runs.

## Usage

```
[1] Compile local workspace      → enter a project path
[2] Compile remote Git repo     → clone + build any branch
[3] Settings                    → language, updates, tests, theme, force update
[4] Exit
```

After a successful build you get an artifact picker; selecting one runs it immediately. If it crashes due to a missing library, the matched dependency panel appears with distro-specific install commands and an auto-install prompt.

## Configuration (`config.json`)

```jsonc
{
  "check_updates": true,       // check GitHub for new releases at startup
  "lang": "en",                // interface language: "en" | "nl"
  "base_image": "ubuntu:24.04",
  "theme": "default",          // "default" | "minimal" (no panel borders)
  "run_tests": false,          // pass --test to the engine where supported

  // Optional sandbox hardening (all optional, safe defaults):
  "sandbox_network": false,    // container gets --network none (offline builds)
  "memory_limit": "4g",        // docker --memory
  "cpu_limit": 2               // docker --cpus
}
```

> Note: disabling `sandbox_network` breaks builds that need to download dependencies from the internet.

## Plugin format (`plugins.json`)

Each entry:

```jsonc
{
  "name": "Vala (Meson)",
  "detect": ["meson.build"],              // files (or *.globs) that identify the project
  "tool": "valac",
  "cmd_system": "meson setup build && meson compile -C build",
  "out_dirs": ["build"],                  // where to look for outputs
  "out_exts": ["", ".exe"],               // extensions to harvest ("*DIR*" = directories)
  "specificity": 3                        // higher wins when multiple plugins match
  // "wrapper": "gradlew"                  // optional wrapper script preference
}
```

Optional `runtime_deps` — shown (and auto-installable) when the built program fails on the host:

```jsonc
"runtime_deps": [
  {
    "pkg": "GTK4",                                  // human-readable name
    "apt": "libgtk-4-dev gir1.2-gtk-4.0",
    "pacman": "gtk4",
    "dnf": "gtk4-devel",
    "winget": "",                                    // Windows package id (if one exists)
    "choco": "",
    "scoop": ""
  }
]
```

## Java version detection

Priority order:

1. Explicit declaration in build files — `JavaLanguageVersion.of(N)`, `jvmToolchain(N)`, `sourceCompatibility/targetCompatibility`, Maven `<maven.compiler.release>` / `<java.version>` / `<release>`, `.java-version`
2. Otherwise capped by Gradle wrapper age — wrapper ≥ 8.5 → JDK 21, older → JDK 17
3. Fallback default: JDK 21

The detected version is passed into the container as `JC_JAVA_VERSION`; the entrypoint sets `JAVA_HOME=/opt/jdkN`. JDKs 8, 17, 21 and 25 are preinstalled. Projects can still override via their own build configuration.

## Updating

Updates are pulled from GitHub tags and verified with SHA256 checksums. Force re-download from Settings → *Force update* (skips checksum verification for recovery scenarios).

```bash
python3 justcompiler.py --version        # show version
python3 justcompiler.py --lang nl        # override language
python3 justcompiler.py clean            # reclaim disk space (see below)
python3 justcompiler.py uninstall        # remove alias, Docker images, config
```

### `clean`

Removes old build output folders from `./EXECUTABLE` (newest 10 kept by default,
override with `clean --keep N`), lists and optionally removes per-project Docker
volumes (`justcompiler-*`, cached build state), and prunes dangling images.
Nothing is deleted without a prompt for the volumes step.


## Troubleshooting

| Symptom | Fix |
|---|---|
| `Unsupported class file major version NN` | Fixed automatically since v1.4.1+ (JDK matched to project). Update and rebuild. |
| GUI app fails after build | Check the matched-dependency panel; e.g. Fedora needs `libayatana-appindicator-gtk3` for Ayatana indicators |
| Stale files in rebuilt project | Persist volumes now mirror sources exactly (rsync --delete); delete old volume: `docker volume rm justcompiler-<hash>` |
| Base image rebuild loops | Expected after engine/Dockerfile changes; happens once per content hash |

## Platform notes

- **Linux**: full support. Auto-install uses `sudo`; packages already present are skipped via `dpkg`/`pacman`/`rpm`.
- **Windows** *(experimental)*: requires Docker Desktop. Auto-install prefers `winget` (one package per command), falls back to `choco` (needs admin terminal) or `scoop`.
- **macOS**: builds work through Docker; artifact execution supports Mach-O binaries and shell scripts.

## Project layout

```
justcompiler.py     CLI/TUI entry point, artifact scanning, dep hints/auto-install
engine.py           In-container build engine (plugin dispatch, harvesting)
docker_manager.py   Image lifecycle, sandbox options, log capture
core.py             UI primitives, i18n (en/nl)
plugins.json        Language plugin definitions + runtime_deps
tests/              pytest regression suite (host logic, engine, docker flags, schema)
```

## Development & testing

```bash
pip install pytest
python -m pytest tests/ -q
```

The suite covers Java detection, error→dependency matching, install command
construction, artifact classification, target scanning, the build engine
(workspace roots, wrapper lookup, harvest, end-to-end plugin dispatch with a
fake `echo` toolchain), i18n key parity (en/nl), image pruning and the
`plugins.json` schema — no Docker or network required.

