"""
main.py -- App Entry Point
===========================
Single entry point for the bundled app.
Enforces one instance only, runs the wizard on first launch, then the tray.

When frozen by PyInstaller the executable is also used as the Python
interpreter for every subprocess.  Special flags route execution to the
correct module so the tray can spawn nodes, open the chat terminal, open
settings, or re-run the wizard -- all without needing a separate Python
install on the user's machine.

Internal flags (all bypass the single-instance lock):
  --node <yaml>   Run a single ExpertNode for the given YAML config.
  --ask [url]     Open the interactive chat CLI (ask.py).
  --settings      Open the settings window (settings_ui.py).
  --wizard        Delete the first-run sentinel and re-run the setup wizard.
"""

import socket
import sys

# ── Internal subprocess dispatch ──────────────────────────────────────────────
# Handle these BEFORE any other imports so the subprocess starts fast and
# never contends for the single-instance lock.

def _dispatch() -> bool:
    """
    Check for internal flags and run the appropriate module.
    Returns True if a flag was handled (caller should exit), False otherwise.
    """
    args = sys.argv[1:]

    # ── Expert node subprocess ────────────────────────────────────────────
    if "--node" in args:
        idx = args.index("--node")
        yaml_path = args[idx + 1] if idx + 1 < len(args) else None
        if not yaml_path:
            print("Usage: --node <config.yaml>")
            sys.exit(1)
        import asyncio
        from node import ExpertNode
        asyncio.run(ExpertNode(yaml_path).start())
        return True

    # ── Chat terminal subprocess ──────────────────────────────────────────
    if "--ask" in args:
        idx = args.index("--ask")
        # Reconstruct argv so ask.main() sees the optional URL at argv[1]
        sys.argv = [sys.argv[0]] + args[idx + 1:]
        from ask import main as ask_main
        ask_main()
        return True

    # ── Settings window subprocess ────────────────────────────────────────
    if "--settings" in args:
        from settings_ui import run_settings_standalone
        run_settings_standalone()
        return True

    # ── Re-run setup wizard ───────────────────────────────────────────────
    if "--wizard" in args:
        from app_paths import FIRST_RUN_SENTINEL, copy_default_experts, ensure_dirs
        from setup_wizard import run_if_needed
        FIRST_RUN_SENTINEL.unlink(missing_ok=True)
        ensure_dirs()
        copy_default_experts()
        run_if_needed()
        return True

    return False


if _dispatch():
    sys.exit(0)

# ── Normal tray startup ───────────────────────────────────────────────────────

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
        sys.exit(0)

    ensure_dirs()
    copy_default_experts()

    # Show setup wizard on first run; skip once sentinel file exists.
    # Also re-runs if experts directory is empty (wizard previously failed).
    from app_paths import EXPERTS_DIR, FIRST_RUN_SENTINEL
    if not any(EXPERTS_DIR.glob("*.yaml")):
        FIRST_RUN_SENTINEL.unlink(missing_ok=True)
    run_if_needed()

    TrayApp(autostart_nodes=True).run()


if __name__ == "__main__":
    main()
