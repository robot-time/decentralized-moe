"""
build.py -- PyInstaller wrapper
===============================
Usage:
    python build.py

Outputs:
    dist/MoE-Network.exe   (Windows)
    dist/MoE-Network.app   (macOS)
"""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    here = Path(__file__).parent
    main_py = here / "main.py"
    experts_dir = here / "experts"
    ui_dir = here / "ui"

    sep = ";" if sys.platform == "win32" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "MoE-Network",
        "--add-data", f"{experts_dir}{sep}experts",
        "--add-data", f"{ui_dir}{sep}ui",
        str(main_py),
    ]

    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
