"""
main.py -- App Entry Point
===========================
This is the single file PyInstaller targets. It:
  1. Runs the setup wizard on first launch (no admin, all user-home paths)
  2. Launches the system tray app

Build the distributable:
    python build.py

Run from source:
    python main.py
"""

import sys

from app_paths import copy_default_experts, ensure_dirs
from setup_wizard import run_if_needed
from tray import TrayApp


def main() -> None:
    ensure_dirs()
    copy_default_experts()    # copy bundled YAML templates if user has none

    # Show setup wizard on first run; skip if already configured
    run_if_needed()

    # Launch the tray app (starts expert nodes automatically)
    TrayApp(autostart_nodes=True).run()


if __name__ == "__main__":
    main()
