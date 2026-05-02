"""
settings_ui.py -- Settings window for the MoE Network tray app
==============================================================
Opened as a subprocess by the tray ("Open Settings") to avoid competing
with pystray's AppKit/Win32 event loop on the main thread.

Contains everything that used to live in the tray right-click menu:
  - Launch at Login
  - Keep Awake (persist sys-sleep while relay is active)
  - Remote Relay  (enable/disable, URL, API key, local node, poll timeout)

Changes are written to relay_config.yaml and the OS login-item registry
immediately on Save. The tray picks up relay/keep-awake changes the next
time the relay is started.

Standalone use:
    python settings_ui.py
Or launched by the frozen bundle with the --settings flag.
"""

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import yaml

from app_paths import APP_DIR
from updater import get_launch_at_login, set_launch_at_login

BASE_DIR = Path(__file__).parent

# ── Palette (matches setup_wizard.py) ────────────────────────────────────────
BG       = "#0d0d0d"
SURFACE  = "#1a1a1a"
BORDER   = "#2e2e2e"
FG       = "#ffffff"
FG_DIM   = "#888888"
BTN_FG   = "#000000"
BTN_BG   = "#ffffff"
BTN_SEC  = "#2a2a2a"
GOOD     = "#22c55e"

FONT_H1    = ("Helvetica", 20, "bold")
FONT_H2    = ("Helvetica", 13, "bold")
FONT_BODY  = ("Helvetica", 11)
FONT_SMALL = ("Helvetica", 10)
FONT_MONO  = ("Courier", 10)

_RELAY_DEFAULTS = {
    "enabled":        False,
    "relay_url":      "",
    "api_key":        "",
    "local_node_url": "http://localhost:8001",
    "keep_awake":     True,
    "poll_timeout":   30,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _label(parent, text, font=FONT_BODY, color=FG, **kw):
    # setdefault so callers can override bg without TypeError on macOS
    kw.setdefault("bg", parent["bg"])
    return tk.Label(parent, text=text, font=font, fg=color, **kw)


def _divider(parent):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=24, pady=12)


def _section_header(parent, title: str):
    f = tk.Frame(parent, bg=BG)
    f.pack(fill="x", padx=24, pady=(16, 2))
    _label(f, title, FONT_H2).pack(anchor="w")


def _row(parent) -> tk.Frame:
    f = tk.Frame(parent, bg=BG)
    f.pack(fill="x", padx=24, pady=(4, 0))
    return f


def _toggle_row(parent, label: str, hint: str, var: tk.BooleanVar) -> None:
    row = _row(parent)
    _label(row, label, FONT_BODY).pack(side="left")
    tk.Checkbutton(
        row, variable=var,
        bg=BG, fg=FG, selectcolor="#333333",
        activebackground=BG, activeforeground=FG,
        cursor="hand2",
    ).pack(side="right")
    hint_row = _row(parent)
    _label(hint_row, hint, FONT_SMALL, FG_DIM).pack(side="left")


def _field_row(parent, label: str, var: tk.StringVar,
               show: str = "", width: int = 26) -> None:
    row = _row(parent)
    _label(row, label, FONT_SMALL, FG_DIM, width=18, anchor="w").pack(side="left")
    tk.Entry(
        row, textvariable=var, show=show, width=width,
        bg=SURFACE, fg=FG, insertbackground=FG,
        relief="flat", font=FONT_MONO,
    ).pack(side="right")


def _btn(parent, text, command, primary=True, **kw):
    bg = BTN_BG if primary else BTN_SEC
    fg = BTN_FG if primary else FG
    b = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
        relief="flat",
        font=("Helvetica", 10, "bold" if primary else "normal"),
        padx=18, pady=7, cursor="hand2", **kw
    )
    b.bind("<Enter>", lambda e: b.config(bg="#e5e5e5" if primary else "#3a3a3a"))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


# ── Main window ───────────────────────────────────────────────────────────────

class SettingsWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MoE Network — Settings")
        self.geometry("500x580")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        # Force to front (especially needed on Windows)
        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))

        self._cfg = self._load_relay_config()
        self._build()

    # ── Config I/O ────────────────────────────────────────────────────────

    def _load_relay_config(self) -> dict:
        path = APP_DIR / "relay_config.yaml"
        if not path.exists():
            return dict(_RELAY_DEFAULTS)
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            return {**_RELAY_DEFAULTS, **data}
        except Exception:
            return dict(_RELAY_DEFAULTS)

    def _save_relay_config(self, cfg: dict) -> None:
        path = APP_DIR / "relay_config.yaml"
        with open(path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    # ── Build UI ──────────────────────────────────────────────────────────

    def _build(self):
        # Scrollable area
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _scroll(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", _scroll)

        # ── Title ─────────────────────────────────────────────────────────
        tk.Frame(inner, bg=BG, height=20).pack()
        _label(inner, "Settings", FONT_H1).pack(anchor="w", padx=24)
        tk.Frame(inner, bg=BG, height=4).pack()

        # ── System ────────────────────────────────────────────────────────
        _section_header(inner, "System")

        self._lal_var = tk.BooleanVar(value=get_launch_at_login())
        _toggle_row(
            inner,
            "Launch at Login",
            "Start MoE Network automatically when you log in",
            self._lal_var,
        )

        _divider(inner)

        # ── Network ───────────────────────────────────────────────────────
        _section_header(inner, "Network")

        self._ka_var = tk.BooleanVar(value=self._cfg.get("keep_awake", True))
        _toggle_row(
            inner,
            "Keep Awake",
            "Prevent system sleep while the remote relay is connected",
            self._ka_var,
        )

        _divider(inner)

        # ── Remote Relay ──────────────────────────────────────────────────
        _section_header(inner, "Remote Relay")

        self._relay_enabled_var = tk.BooleanVar(
            value=self._cfg.get("enabled", False)
        )
        _toggle_row(
            inner,
            "Enable Remote Relay",
            "Receive AI queries from remote devices via a relay server",
            self._relay_enabled_var,
        )

        tk.Frame(inner, bg=BG, height=8).pack()

        self._var_relay_url      = tk.StringVar(value=self._cfg.get("relay_url", ""))
        self._var_api_key        = tk.StringVar(value=self._cfg.get("api_key", ""))
        self._var_local_node_url = tk.StringVar(
            value=self._cfg.get("local_node_url", "http://localhost:8001")
        )
        self._var_poll_timeout   = tk.StringVar(
            value=str(self._cfg.get("poll_timeout", 30))
        )

        _field_row(inner, "Relay URL",        self._var_relay_url)
        _field_row(inner, "API Key",          self._var_api_key,        show="•")
        _field_row(inner, "Local Node URL",   self._var_local_node_url)
        _field_row(inner, "Poll Timeout (s)", self._var_poll_timeout,   width=6)

        tk.Frame(inner, bg=BG, height=8).pack()
        hint_row = _row(inner)
        _label(
            hint_row,
            "Relay and Keep Awake changes take effect when the relay next starts.",
            FONT_SMALL, FG_DIM, wraplength=440, justify="left",
        ).pack(anchor="w")

        tk.Frame(inner, bg=BG, height=20).pack()

        # ── Buttons ───────────────────────────────────────────────────────
        btn_bar = tk.Frame(self, bg=SURFACE, height=56)
        btn_bar.pack(side="bottom", fill="x")
        btn_bar.pack_propagate(False)

        _btn(btn_bar, "Cancel", self.destroy, primary=False).pack(
            side="right", padx=12, pady=12
        )
        _btn(btn_bar, "Save", self._apply, primary=True).pack(
            side="right", padx=(0, 4), pady=12
        )
        _btn(btn_bar, "Run Setup Wizard", self._run_wizard, primary=False).pack(
            side="left", padx=12, pady=12
        )

    # ── Save ──────────────────────────────────────────────────────────────

    def _run_wizard(self):
        """Spawn the setup wizard in a fresh process, then close settings."""
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--wizard"])
        else:
            subprocess.Popen([sys.executable, str(BASE_DIR / "main.py"), "--wizard"])
        self.destroy()

    def _apply(self):
        # Validate poll timeout
        try:
            timeout = int(self._var_poll_timeout.get().strip() or "30")
            if timeout < 5:
                timeout = 5
        except ValueError:
            messagebox.showwarning(
                "Invalid value",
                "Poll Timeout must be a whole number of seconds.",
            )
            return

        # Write relay config
        cfg = {
            "enabled":        self._relay_enabled_var.get(),
            "relay_url":      self._var_relay_url.get().strip(),
            "api_key":        self._var_api_key.get().strip(),
            "local_node_url": self._var_local_node_url.get().strip(),
            "keep_awake":     self._ka_var.get(),
            "poll_timeout":   timeout,
        }
        self._save_relay_config(cfg)

        # Apply launch-at-login immediately
        set_launch_at_login(self._lal_var.get())

        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_settings_standalone() -> None:
    """Create and run the settings window (blocking until closed)."""
    win = SettingsWindow()
    win.mainloop()


if __name__ == "__main__":
    run_settings_standalone()
