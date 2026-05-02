"""
build.py -- PyInstaller wrapper for the simplified MoE Network app
====================================================================
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

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "MoE-Network",
        "--add-data", f"{experts_dir}{';' if sys.platform == 'win32' else ':'}experts",
        str(main_py),
    ]

    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
