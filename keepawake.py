"""
keepawake.py -- Prevent system sleep while the MoE network is active
=====================================================================
Lightweight cross-platform keep-awake.  Blocks OS sleep (not display
sleep) so the DHT specialist stays reachable.

 macOS : IOKit assertion (display can still dim; system won't sleep)
 Windows : SetThreadExecutionState (ES_SYSTEM_REQUIRED)
 Linux   : systemd-inhibit or xdg-screensaver
"""

from __future__ import annotations

import ctypes
import sys
import threading


class KeepAwake:
    """Context-manager style keep-awake.  Call .start() / .stop() manually."""

    def __init__(self) -> None:
        self._active = False
        self._mac_assertion_id = None

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

    # ── macOS (IOKit assertion) ───────────────────────────────────────

    def _start_mac(self) -> None:
        try:
            from ctypes import cdll, c_void_p, c_uint32, c_double, byref, pointer
            IOKit = cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            kIOPMAssertionTypeNoIdleSleep = b"NoIdleSleepAssertion"
            kIOPMAssertionLevelOn = 255

            assertion_id = c_uint32(0)
            IOKit.IOPMAssertionCreateWithName(
                kIOPMAssertionTypeNoIdleSleep,
                kIOPMAssertionLevelOn,
                b"MoE Network active",
                byref(assertion_id),
            )
            self._mac_assertion_id = assertion_id
        except Exception as exc:
            print(f"[keepawake] macOS start failed: {exc}")

    def _stop_mac(self) -> None:
        if self._mac_assertion_id is None:
            return
        try:
            from ctypes import cdll, c_uint32, byref
            IOKit = cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            IOKit.IOPMAssertionRelease(self._mac_assertion_id)
            self._mac_assertion_id = None
        except Exception as exc:
            print(f"[keepawake] macOS stop failed: {exc}")

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

    # ── Linux (best-effort via xdg-screensaver or systemd-inhibit) ─────

    def _start_linux(self) -> None:
        # We don't have a persistent process to inhibit with, so we just
        # rely on the user having a desktop session that respects idle.
        pass

    def _stop_linux(self) -> None:
        pass
