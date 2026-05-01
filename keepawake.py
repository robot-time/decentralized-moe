"""
keepawake.py -- Prevent the OS from sleeping while the relay is active
=======================================================================
When the local app is polling a relay server for remote queries, it needs
the machine to stay awake so the outbound connection doesn't drop.

This module calls the appropriate OS-level API on each platform:
  macOS   -- caffeinate -d -i  (built-in, no install needed)
  Windows -- SetThreadExecutionState via ctypes  (built-in, no install needed)
  Linux   -- systemd-inhibit if available, otherwise xdg-screensaver reset loop

Usage:
    from keepawake import KeepAwake
    ka = KeepAwake()
    ka.start()
    ...
    ka.stop()
"""

import ctypes
import subprocess
import sys
import threading
import time
import logging

log = logging.getLogger(__name__)

# Windows execution state flags (from WinAPI)
_ES_CONTINUOUS       = 0x80000000
_ES_SYSTEM_REQUIRED  = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """
    Cross-platform keep-awake manager.

    start() prevents the machine from sleeping.
    stop()  restores normal sleep behaviour.

    Safe to call start/stop multiple times.
    """

    def __init__(self) -> None:
        self._active    = False
        self._proc: subprocess.Popen | None = None   # macOS caffeinate process
        self._thread: threading.Thread | None = None # Linux fallback thread

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._active:
            return
        self._active = True

        if sys.platform == "darwin":
            self._start_macos()
        elif sys.platform == "win32":
            self._start_windows()
        else:
            self._start_linux()

        log.info("[keepawake] Keep-awake enabled")

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False

        if sys.platform == "darwin":
            self._stop_macos()
        elif sys.platform == "win32":
            self._stop_windows()
        # Linux thread checks self._active and exits on its own

        log.info("[keepawake] Keep-awake disabled")

    @property
    def is_active(self) -> bool:
        return self._active

    # ── macOS: caffeinate ─────────────────────────────────────────────────────

    def _start_macos(self) -> None:
        """
        caffeinate is a macOS built-in that prevents sleep.
          -i  prevent idle system sleep  (what we want)
          -s  prevent sleep when on AC power

        We intentionally omit -d (display sleep) -- the screen can
        turn off fine. We only need the CPU and network to stay alive
        so the outbound relay polling connection doesn't drop.
        """
        try:
            self._proc = subprocess.Popen(
                ["caffeinate", "-i", "-s"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.warning("[keepawake] caffeinate not found -- sleep prevention unavailable")

    def _stop_macos(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    # ── Windows: SetThreadExecutionState ─────────────────────────────────────

    def _start_windows(self) -> None:
        """
        SetThreadExecutionState tells Windows this thread needs to keep running.

        We use ES_SYSTEM_REQUIRED (keep CPU/network alive) but intentionally
        omit ES_DISPLAY_REQUIRED -- the screen can turn off normally.
        CONTINUOUS makes the flag persist until we clear it with stop().
        """
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
                # No _ES_DISPLAY_REQUIRED -- screen can sleep, system cannot
            )
        except Exception as e:
            log.warning(f"[keepawake] SetThreadExecutionState failed: {e}")

    def _stop_windows(self) -> None:
        try:
            # Passing only CONTINUOUS clears the previous flags
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception as e:
            log.warning(f"[keepawake] Failed to clear execution state: {e}")

    # ── Linux: systemd-inhibit or xdg-screensaver loop ────────────────────────

    def _start_linux(self) -> None:
        """
        Try systemd-inhibit first (most modern distros).
        --what=sleep:idle blocks system sleep but NOT display sleep,
        which is exactly what we want.
        """
        if self._try_systemd_inhibit():
            return
        # Fallback: just keep the system awake via a background process trick
        self._thread = threading.Thread(
            target=self._linux_inhibit_loop,
            daemon=True,
            name="keepawake-linux",
        )
        self._thread.start()

    def _try_systemd_inhibit(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=sleep:idle",   # block system sleep, NOT display sleep
                    "--who=MoE Network",
                    "--why=Remote relay connection active",
                    "sleep", "infinity",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError:
            return False

    def _linux_inhibit_loop(self) -> None:
        """
        Fallback for Linux systems without systemd-inhibit.
        Uses xdg-screensaver and xset to reset the idle timer periodically.
        This prevents auto-sleep triggered by inactivity without affecting
        display timeout settings.
        """
        while self._active:
            # Only reset the system idle timer, not the display timer
            for cmd in [["xset", "s", "reset"], ["xdg-screensaver", "reset"]]:
                try:
                    subprocess.run(cmd, capture_output=True, timeout=5)
                    break
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            for _ in range(55):
                if not self._active:
                    return
                time.sleep(1)
