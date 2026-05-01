"""
share.py -- Package the network for sharing with friends
=========================================================
Run this from inside the decentralized-moe/ folder:

    python share.py

It creates decentralized-moe.zip containing everything your friend needs.
They unzip it, run ./setup.sh, then ./start.sh, then python ask.py.

The zip always includes whatever expert YAMLs are currently in experts/
so it reflects your actual network configuration.
"""

import os
import sys
import zipfile
from pathlib import Path

# Files and folders to include
INCLUDE = [
    "node.py",
    "coordinator.py",
    "ask.py",
    "tray.py",
    "updater.py",
    "setup.sh",
    "start.sh",
    "share.py",
    "requirements.txt",
    "version.txt",
    "experts/",          # all YAML files inside
]

# Files to skip even if inside included folders
SKIP_EXTENSIONS = {".pyc", ".zip"}
SKIP_NAMES      = {"__pycache__", ".DS_Store", ".env"}

OUTPUT_ZIP = "decentralized-moe.zip"
ZIP_ROOT   = "decentralized-moe"   # top-level folder name inside the zip


def should_skip(path: Path) -> bool:
    return (
        path.suffix in SKIP_EXTENSIONS
        or path.name in SKIP_NAMES
        or any(part in SKIP_NAMES for part in path.parts)
    )


def collect_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in INCLUDE:
        target = base / pattern.rstrip("/")
        if not target.exists():
            print(f"  [skip] {pattern} not found")
            continue
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for f in sorted(target.rglob("*")):
                if f.is_file() and not should_skip(f):
                    files.append(f)
    return files


def main() -> None:
    base = Path(__file__).parent.resolve()
    out  = base / OUTPUT_ZIP

    print()
    print("=" * 56)
    print("  Packaging Decentralized MoE Network for sharing")
    print("=" * 56)
    print()

    files = collect_files(base)
    if not files:
        print("Nothing to package. Run from inside decentralized-moe/")
        sys.exit(1)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arc_name = ZIP_ROOT / f.relative_to(base)
            zf.write(f, arc_name)
            print(f"  + {arc_name}")

    size_kb = out.stat().st_size // 1024
    print()
    print(f"Created: {out.name}  ({size_kb} KB)")
    print()
    print("Send decentralized-moe.zip to your friend.")
    print("They run:")
    print()
    print("  unzip decentralized-moe.zip")
    print("  cd decentralized-moe")
    print("  ./setup.sh          # installs deps + pulls models")
    print("  python tray.py      # tray icon appears, nodes start automatically")
    print()
    print("The tray icon sits in their taskbar/menu bar.")
    print("Right-click it to open a chat, check for updates, or quit.")
    print()
    print("For automatic updates: push to GitHub, set GITHUB_REPO")
    print("in updater.py, tag releases as v1.0.0 / v1.1.0 etc.")
    print("=" * 56)
    print()


if __name__ == "__main__":
    main()
