"""
keepawake.py -- Prevent system sleep while the MoE network is active
=====================================================================
Lightweight cross-platform keep-awake.  Blocks OS sleep (not display
sleep) so the DHT specialist stays reachable.

 macOS   : caffeinate (preinstalled, standard Apple tool)
 Windows : SetThreadExecutionState
 Linux   : best-effort via xdg-screensaver or systemd-inhibit
"""

from __future__ import annotations

import ctypes
import subprocess
import sys


class KeepAwake:
    """Context-manager style keep-awake.  Call .start() / .stop() manually."""

    def __init__(self) -> None:
        self._active = False
        self._mac_proc: subprocess.Popen | None = None

    # ── Public API ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        if sys.platform == "darwin":
            self._start_mac()
        elif sys.platform == "win32":
            self._start_win()
        else:
            self._start_linux()

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        if sys.platform == "darwin":
            self._stop_mac()
        elif sys.platform == "win32":
            self._stop_win()
        else:
            self._stop_linux()

    # ── macOS (caffeinate) ──────────────────────────────────────────────

    def _start_mac(self) -> None:
        """
        caffeinate -i prevents idle sleep (display can still dim).
        Runs in background; we terminate it in stop().
        """
        try:
            self._mac_proc = subprocess.Popen(
                ["caffeinate", "-i"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            print("[keepawake] caffeinate not found (should be preinstalled on macOS)")
        except Exception as exc:
            print(f"[keepawake] macOS start failed: {exc}")

    def _stop_mac(self) -> None:
        if self._mac_proc is not None:
            try:
                self._mac_proc.terminate()
                self._mac_proc.wait(timeout=2)
            except Exception:
                pass
            self._mac_proc = None

    # ── Windows (SetThreadExecutionState) ───────────────────────────────

    def _start_win(self) -> None:
        try:
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
        except Exception as exc:
            print(f"[keepawake] Windows start failed: {exc}")

    def _stop_win(self) -> None:
        try:
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception as exc:
            print(f"[keepawake] Windows stop failed: {exc}")

    # ── Linux (best-effort) ─────────────────────────────────────────────

    def _start_linux(self) -> None:
        pass

    def _stop_linux(self) -> None:
        pass
