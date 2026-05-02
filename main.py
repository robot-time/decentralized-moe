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

import os
import signal
import socket
import subprocess
import sys
import time
import traceback

# ── Subprocess error logging ──────────────────────────────────────────────────

def _log_dispatch_error(role: str, exc: BaseException) -> None:
    """Write a subprocess crash to APP_DIR/logs/<role>.log for later inspection."""
    try:
        from app_paths import LOG_DIR, ensure_dirs
        ensure_dirs()
        log_path = LOG_DIR / f"{role}.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            f.write(f"argv: {sys.argv}\n")
            traceback.print_exc(file=f)
    except Exception:
        pass


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
        try:
            import asyncio
            from node import ExpertNode
            asyncio.run(ExpertNode(yaml_path).start())
        except Exception as e:
            specialty = os.path.splitext(os.path.basename(yaml_path))[0]
            _log_dispatch_error(f"node_{specialty}", e)
            sys.exit(1)
        return True

    # ── Chat terminal subprocess ──────────────────────────────────────────
    if "--ask" in args:
        idx = args.index("--ask")
        sys.argv = [sys.argv[0]] + args[idx + 1:]
        try:
            from ask import main as ask_main
            ask_main()
        except Exception as e:
            _log_dispatch_error("ask", e)
            sys.exit(1)
        return True

    # ── Settings window subprocess ────────────────────────────────────────
    if "--settings" in args:
        try:
            from settings_ui import run_settings_standalone
            run_settings_standalone()
        except Exception as e:
            _log_dispatch_error("settings", e)
            sys.exit(1)
        return True

    # ── Re-run setup wizard ───────────────────────────────────────────────
    if "--wizard" in args:
        try:
            from app_paths import FIRST_RUN_SENTINEL, copy_default_experts, ensure_dirs
            from setup_wizard import run_if_needed
            FIRST_RUN_SENTINEL.unlink(missing_ok=True)
            ensure_dirs()
            copy_default_experts()
            run_if_needed()
        except Exception as e:
            _log_dispatch_error("wizard", e)
            sys.exit(1)
        return True

    return False


if _dispatch():
    sys.exit(0)


# ── Stale-process cleanup ─────────────────────────────────────────────────────
# PyInstaller-bundled subprocesses don't auto-die when the parent exits, so
# previous failed runs leave --node/--ask processes alive holding DHT ports.
# Kill them before we start fresh.

def _kill_stale_processes() -> None:
    """Kill leftover MoE-Network processes from previous runs (best-effort)."""
    me = os.getpid()

    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MoE-Network.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    pid = int(parts[1])
                    if pid != me:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=5,
                        )
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(
                ["pgrep", "-f", "MoE-Network"],
                capture_output=True, text=True, timeout=5,
            )
            pids = [int(p) for p in r.stdout.split() if p.isdigit() and int(p) != me]
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            time.sleep(1)
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass

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

    # Kill stale --node / --ask processes from previous failed runs.
    # Without this, port 8468/8469/etc. stay bound and new nodes can't start.
    _kill_stale_processes()

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
