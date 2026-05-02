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

    # ── Chat window subprocess ────────────────────────────────────────────
    if "--chat" in args:
        idx = args.index("--chat")
        url_args = args[idx + 1:]
        try:
            from chat_ui import run_chat_standalone
            run_chat_standalone(url_args[0] if url_args else "http://localhost:8001")
        except Exception as e:
            _log_dispatch_error("chat", e)
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

def _kill_pid(pid: int) -> None:
    """Force-kill a single PID, swallowing any error."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    else:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.3)


def _pids_using_ports(ports: list[int]) -> set[int]:
    """
    Return PIDs of processes currently bound to any of the given ports.
    Uses netstat on Windows and lsof on Unix.  Best-effort -- returns an
    empty set on any failure.
    """
    pids: set[int] = set()
    me = os.getpid()
    port_strs = [f":{p}" for p in ports]

    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                if any(ps in line for ps in port_strs):
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pid = int(parts[-1])
                        if pid != me and pid != 0:
                            pids.add(pid)
        except Exception:
            pass
    else:
        for port in ports:
            try:
                r = subprocess.run(
                    ["lsof", "-nP", "-iTCP:%d" % port, "-iUDP:%d" % port],
                    capture_output=True, text=True, timeout=5,
                )
                for line in r.stdout.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        pid = int(parts[1])
                        if pid != me:
                            pids.add(pid)
            except Exception:
                continue
    return pids


def _read_dht_ports() -> list[int]:
    """Read DHT ports from every expert YAML so we can free them on startup."""
    ports = [8468, 8469, 8471, 8472]  # known defaults if reading fails
    try:
        import yaml
        from app_paths import APP_DIR
        experts_dir = APP_DIR / "experts"
        if experts_dir.exists():
            found = []
            for f in experts_dir.glob("*.yaml"):
                try:
                    with open(f) as fh:
                        cfg = yaml.safe_load(fh) or {}
                    p = cfg.get("dht_port")
                    if isinstance(p, int):
                        found.append(p)
                except Exception:
                    pass
            if found:
                return found
    except Exception:
        pass
    return ports


def _kill_stale_processes() -> None:
    """
    Kill leftover MoE-Network processes from previous runs.

    Two passes for reliability:
      1. By executable name (catches all our subprocesses)
      2. By port (catches anything still holding our DHT ports, regardless
         of name -- could be an old version of the app, an orphan, etc.)
    """
    me = os.getpid()

    # ── Pass 1: by executable name ───────────────────────────────────────
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq MoE-Network.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    pid = int(parts[1])
                    if pid != me:
                        _kill_pid(pid)
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(
                ["pgrep", "-f", "MoE-Network"],
                capture_output=True, text=True, timeout=5,
            )
            for tok in r.stdout.split():
                if tok.isdigit() and int(tok) != me:
                    _kill_pid(int(tok))
        except Exception:
            pass

    # ── Pass 2: by port ──────────────────────────────────────────────────
    # School/managed Windows often leaves orphans that taskkill misses.
    # If anything is still bound to a DHT port we need, kill it directly.
    time.sleep(0.5)
    for pid in _pids_using_ports(_read_dht_ports()):
        _kill_pid(pid)
    time.sleep(0.5)

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
    try:
        main()
    except SystemExit:
        raise
    except BaseException as _exc:
        # Frozen --windowed apps swallow tracebacks. Persist any startup
        # crash to a file so the user can see why the app died.
        _log_dispatch_error("startup", _exc)
        raise
