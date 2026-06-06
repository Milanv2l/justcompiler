import sys
import subprocess
from pathlib import Path
from core import UI

def run_bare_metal(target_path: Path, artifacts_path: Path, run_tests: bool, lang: str):
    """
    Voert de AutoBuilder engine lokaal uit op de host (bare-metal)
    in plaats van binnen een geïsoleerde Docker-container.
    """
    UI.warn("Bare-metal runtime gestart...")

    # Bepaal het pad naar engine.py
    host_dir = Path(__file__).resolve().parent
    engine_path = host_dir / "engine.py"

    if not engine_path.exists():
        UI.error(f"Kan de motor niet vinden op locatie: {engine_path}")
        sys.exit(1)

    # Bouw het lokale cross-platform commando op
    cmd = [
        sys.executable,
        str(engine_path),
        "--src", str(target_path.resolve()),
        "--out", str(artifacts_path.resolve()),
        "--lang", lang,
        "--auto-install"
    ]

    if run_tests:
        cmd.append("--test")

    try:
        # Start de engine lokaal op het OS host platform.
        subprocess.run(cmd)
    except KeyboardInterrupt:
        UI.warn("\nBare-metal build handmatig afgebroken door gebruiker.")
