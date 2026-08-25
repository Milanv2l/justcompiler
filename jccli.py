"""justcompiler console entry point (pip/pipx installs).

Subcommands
-----------
serve [--port N] [--max-builds N]   headless Engine API (no TUI imports)
update [--yes] [--force]            self-updater (git-checkout installs)
build --build <path|url> [args...]  headless build (same flags as repo CLI)
tui                                 interactive interface (needs textual)
help                                this text

Bare `justcompiler` behaves like the repo's `python3 justcompiler.py`.
"""
import os
import sys


def _jc():
    import justcompiler as jc          # engine stack is TUI-free
    return jc


def _exec_entry(extra):
    """Replace process with the full repo entrypoint."""
    jc = _jc()
    os.execv(sys.executable,
             [sys.executable, str(jc.__file__)] + list(extra))


def _need_textual():
    try:
        import textual                 # noqa: F401
        return False
    except ImportError:
        print("The interactive TUI requires 'textual'.\n"
              "Install with: pipx install 'justcompiler[tui]' — or use the "
              "headless mode: justcompiler serve")
        return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0].lower() if argv else "tui"

    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    if cmd == "serve":
        port = 7400
        builds = 1
        if "--port" in argv:
            try:
                port = int(argv[argv.index("--port") + 1])
            except (IndexError, ValueError):
                pass
        if "--max-builds" in argv:
            try:
                builds = max(1, int(argv[argv.index("--max-builds") + 1]))
            except (IndexError, ValueError):
                pass
        jc = _jc()
        import daemon
        daemon.serve(port=port, execute_fn=jc.execute_build,
                     version_fn=lambda: jc.VERSION, max_concurrent=builds)
        return 0

    if cmd == "update":
        jc = _jc()
        force = "--force" in argv or "-f" in argv
        ask = not ("--yes" in argv or "-y" in argv)
        res = jc._do_update(ask=ask, force=force)
        if res is None and not force:
            print("Already up to date.")
        return 0

    if cmd == "build":
        rest = argv[1:] if len(argv) > 1 else []
        if not rest or rest[0] != "--build":
            rest = ["--build"] + rest
        _exec_entry(rest)              # never returns

    if cmd in ("tui", "gui"):
        if _need_textual():
            return 3
        _exec_entry([])

    # unknown flag starting with '-' → treat as passthrough to entry
    if cmd.startswith("-"):
        _exec_entry(argv)
    print(f"unknown command: {cmd}\n" + __doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
