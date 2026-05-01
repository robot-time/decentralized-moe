"""
main.py -- App Entry Point
===========================
Single entry point for the bundled app.
Enforces one instance only, runs the wizard on first launch, then the tray.
"""

import socket
import sys

from app_paths import copy_default_experts, ensure_dirs
from setup_wizard import run_if_needed
from tray import TrayApp

# ── Single instance lock ───────────────────────────────────────────────────
# Bind a local socket so a second launch detects the first and exits quietly.
_LOCK_PORT = 47193
_lock_socket = None

def _acquire_lock() -> bool:
    global _lock_socket
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        _lock_socket.bind(("127.0.0.1", _LOCK_PORT))
        return True
    except OSError:
        return False   # already running


def main() -> None:
    if not _acquire_lock():
        # Another instance is already running -- just exit silently
        sys.exit(0)

    ensure_dirs()
    copy_default_experts()

    # Show setup wizard on first run; skip once sentinel file exists
    run_if_needed()

    # Launch the tray app
    TrayApp(autostart_nodes=True).run()


if __name__ == "__main__":
    main()
