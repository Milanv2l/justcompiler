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

### Headless / autonomous mode

```bash
python3 justcompiler.py --build <path-or-git-url> [--branch B] [--target NAME] [--all-targets]
```

`--all-targets` builds every detected subproject in one run (e.g. a Python
backend + Node frontend monorepo); the summary aggregates all of them and
exit `3` means partial success.

Give it a local path **or a repository URL** and it compiles unattended:

- URLs are shallow-cloned into `~/.justcompiler/repos/<repo>-<hash>/`; repeat
  runs reuse and fast-forward the clone (default branch is picked automatically)
- No interactive prompts — no branch menu, no artifact picker
- The run ends with a machine-readable JSON block and writes `summary.json`
  into the build folder: `{status, error_class, target, toolchain, commit,
  duration_s, artifacts[], logs[], possible_runtime_deps[]}`
- Exit codes: `0` success · `1` build failed · `2` invalid input/clone failure · `3` partial success (some artifacts despite task failures)
- Missing host runtime-dependencies are *reported* in the summary; set
  `"auto_install_deps": true` in config.json to let headless mode install them too

### Project overrides (`.justcompiler.json`)

Place next to your sources to make autonomous builds deterministic:

```jsonc
{
  "target": "Minecraft Mod (Fabric/Forge/Quilt)", // exact plugin name
  "java_version": 21,
  "profile": "slim",           // sandbox profile override
  "network": true,             // false = --network none for this project
  "memory_limit": "6g",
  "cpu_limit": 8,
  "env": { "CARGO_NET_GIT_FETCH_WITH_CLI": "true" },
  "run_tests": true
}
```

Unknown keys are ignored; an unknown `target` is reported and dropped.

After a successful interactive build you get an artifact picker; selecting one
runs it immediately. If it crashes due to a missing library, the matched
dependency panel appears with distro-specific install commands and an
auto-install prompt.

## Self-healing builds

Inside the sandbox the engine retries failures with targeted rescues:

| Failure signature | Automatic response |
|---|---|
| Missing system header/lib (`fatal error: zlib.h`, `-lz`) | apt-installs mapped dev package |
| Missing Python module / Node module / Ruby gem | pip / npm -g / gem install |
| Missing build tool (`cargo: command not found`) | apt-installs tool once, retries |
| Rust `edition2024` required (distro cargo too old) | bootstraps current rustup toolchain, retries |
| Go `undefined: auto.*` (ungenerated embedded assets) | retries once with `-tags noassets` |
| pnpm 10 build-script approval (would block forever) | sandbox runs with `CI=1` — all installs non-interactive |
| Maven repo HTTP 5xx | fails fast — upstream outage is not retried |
| Gradle heap vs host RAM | heap clamped to ~70% of available memory before launch |

Long-running steps print a heartbeat every 30s with the latest output line, so
silent phases (dependency downloads) never look like a hang.

## Interactive TUI

Running `python3 justcompiler.py` without `--build` opens a full-screen TUI
(built on [Textual](https://textual.textualize.io/)) when a terminal is
available:

- **Home** — status header, menu, and your 10 most recent builds (with status)
- **Build form** — path or git URL, branch picker for URLs, target selector: `auto`, **All detected targets**, or any specific plugin
- **Live build** — streaming log, docker-build progress bar, elapsed timer + engine status every second, `c` to cancel the sandbox
- **Artifacts** — table of harvested outputs; `r` runs one (output streams in-app), `o` opens the folder
- **Settings** — language, updates, tests, auto-install-deps, sandbox profile, force update
- **Help** — press `?` (or F1) anywhere

Everything is keyboard-first (`n` new · `s` settings · `r` refresh/run ·
`c` cancel · `esc` back · `q` quit); mouse clicks work as a bonus.

If [Textual](https://pypi.org/project/textual/) isn't installed, JustCompiler
falls back to the classic ANSI panels and prints:
`pip install --user textual` to enable the TUI. Headless mode is unaffected.

## Configuration (`config.json`)

```jsonc
{
  "check_updates": true,       // check GitHub for new releases at startup
  "lang": "en",                // interface language: "en" | "nl"
  "base_image": "ubuntu:24.04",
  "profile": "full",           // "full" = every toolchain preinstalled (~GBs)
                               // "slim" = essentials only; engine auto-installs
                               //         missing tools on demand (fast first build)
  "theme": "default",          // "default" | "minimal" (no panel borders)
  "run_tests": false,          // pass --test to the engine where supported
  "keep_builds": 0,            // retention: keep only newest N EXECUTABLE folders
  "notify": true,              // desktop notification when a build finishes

  // Optional sandbox hardening (all optional, safe defaults):
  "sandbox_network": false,    // container gets --network none (offline builds)
  "memory_limit": "4g",        // docker --memory
  "cpu_limit": 2               // docker --cpus
}
```

> Note: disabling `sandbox_network` breaks builds that need to download dependencies from the internet.
> Switching `profile` builds a second base image alongside the other profile (both are kept).

## Cleanup

```bash
python3 justcompiler.py clean                 # keep newest 10 build folders
python3 justcompiler.py clean --keep 3        # keep newest 3
python3 justcompiler.py clean --volumes-old 30 # auto-remove project volumes untouched >30 days
```

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
scripts/smoke.sh          # headless end-to-end run over 6 real-world repos
```

The unit suite covers Java detection, error→dependency matching, install command
construction, artifact classification, target scanning, the build engine,
i18n parity, image pruning, the autonomous-mode helpers (clone cache, summary
schema, project overrides) and the `plugins.json` schema — no Docker needed.
`scripts/smoke.sh` then builds six real projects (rich, fmt, vite, alacritty,
syncthing, CreateNuclearNeoForge) through the full pipeline; upstream outages
are reported as skips instead of failures.

