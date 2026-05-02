"""
wizard.py -- First-run setup wizard
====================================
Guides the user through:
  1. Welcome
  2. Ollama installation check
  3. Role selection (STEM / HASS / User-only)
  4. Pull required models with live progress
  5. Optional bootstrap peer address
  6. Done — launch the app

Called automatically from main.py when config.yaml has wizard_done=False.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from config import (
    APP_DIR,
    DEFAULTS,
    ensure_dirs,
    load,
    save,
)

# ── Palette ──────────────────────────────────────────────────────────────────

BG          = "#0f0f0f"
SURFACE     = "#1a1a1a"
SURFACE_2   = "#262626"
BORDER      = "#333333"
FG          = "#e5e5e5"
FG_DIM      = "#a1a1a1"
ACCENT      = "#ffffff"
ACCENT_FG   = "#000000"
SUCCESS     = "#22c55e"
ERROR       = "#ef4444"

FONT_TITLE  = ("Helvetica", 18, "bold")
FONT_HEAD   = ("Helvetica", 14, "bold")
FONT_BODY   = ("Helvetica", 12)
FONT_SMALL  = ("Helvetica", 10)
FONT_TINY   = ("Helvetica", 9)

ROLE_META = {
    "user": {
        "title": "Chat Only",
        "desc": "Don't host a specialist. Just chat with the network.",
        "models": ["llama3.1:8b"],
    },
    "stem": {
        "title": "STEM Specialist",
        "desc": "Host a STEM node (math, science, code, engineering).",
        "models": ["mistral:7b", "llama3.1:8b"],
    },
    "hass": {
        "title": "HASS Specialist",
        "desc": "Host a HASS node (society, ethics, history, philosophy).",
        "models": ["llama3.1:8b"],
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _label(parent, text, font=FONT_BODY, color=FG, **kw):
    kw.setdefault("bg", parent["bg"])
    return tk.Label(parent, text=text, font=font, fg=color, **kw)


def _check_ollama() -> tuple[bool, str]:
    """Return (installed, version_or_error)."""
    candidates = ["ollama"]
    if sys.platform == "darwin":
        candidates += [
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            "/Applications/Ollama.app/Contents/MacOS/ollama",
        ]

    for exe in candidates:
        try:
            r = subprocess.run(
                [exe, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return True, r.stdout.strip().splitlines()[0]
        except FileNotFoundError:
            continue
        except Exception as exc:
            return False, str(exc)
    return False, "Ollama not found in PATH"


def _ollama_exe() -> str | None:
    """Return the path to the ollama binary, or None."""
    candidates = ["ollama"]
    if sys.platform == "darwin":
        candidates += [
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
            "/Applications/Ollama.app/Contents/MacOS/ollama",
        ]
    for exe in candidates:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return exe
        except FileNotFoundError:
            continue
        except Exception:
            break
    return None


def _list_models() -> list[str]:
    """Return installed model names."""
    exe = _ollama_exe()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return []
        names = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if parts:
                names.append(parts[0])
        return names
    except Exception:
        return []


def _parse_progress(line: str) -> int:
    """Extract percentage from ollama pull output."""
    m = re.search(r"(\d+)%", line)
    return int(m.group(1)) if m else 0


# ── Wizard ───────────────────────────────────────────────────────────────────

class Wizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MoE Network — Setup")
        self.geometry("720x520")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.minsize(600, 480)

        self._step = 0
        self._cfg: dict = dict(DEFAULTS)
        self._role_var = tk.StringVar(value="user")
        self._ollama_ok = False
        self._ollama_ver = ""
        self._installed: list[str] = []
        self._pull_threads: list[threading.Thread] = []
        self._pull_done_event = threading.Event()

        ensure_dirs()

        self._build_ui()
        self._show_step(0)

        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))

    # ── UI shell ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Progress dots at top
        self._dots_frame = tk.Frame(self, bg=BG, height=28)
        self._dots_frame.pack(fill="x", padx=24, pady=(18, 0))
        self._dots: list[tk.Label] = []
        for i in range(5):
            dot = tk.Label(
                self._dots_frame, text="●", font=("Helvetica", 8),
                bg=BG, fg=FG_DIM,
            )
            dot.pack(side="left", padx=4)
            self._dots.append(dot)

        # Content area
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(fill="both", expand=True, padx=40, pady=12)

        # Bottom buttons
        self._btns = tk.Frame(self, bg=BG)
        self._btns.pack(fill="x", padx=40, pady=(0, 24))

        self._btn_back = tk.Button(
            self._btns, text="Back", command=self._prev,
            bg=SURFACE, fg=FG, activebackground=SURFACE_2,
            activeforeground=FG, relief="flat", font=FONT_BODY,
            padx=18, pady=8, cursor="hand2", borderwidth=0,
        )
        self._btn_back.pack(side="left")

        self._btn_next = tk.Button(
            self._btns, text="Next", command=self._next,
            bg=ACCENT, fg=ACCENT_FG, activebackground="#e5e5e5",
            activeforeground=ACCENT_FG, relief="flat", font=FONT_BODY,
            padx=24, pady=8, cursor="hand2", borderwidth=0,
        )
        self._btn_next.pack(side="right")

    def _show_step(self, n: int) -> None:
        self._step = n
        for w in self._content.winfo_children():
            w.destroy()

        for i, dot in enumerate(self._dots):
            dot.config(fg=ACCENT if i == n else FG_DIM)

        self._btn_back.config(state="normal" if n > 0 else "disabled")

        if n == 0:
            self._build_welcome()
        elif n == 1:
            self._build_ollama_check()
        elif n == 2:
            self._build_role()
        elif n == 3:
            self._build_models()
        elif n == 4:
            self._build_network()
        elif n == 5:
            self._build_done()

    # ── Step 0: Welcome ───────────────────────────────────────────────────

    def _build_welcome(self) -> None:
        _label(self._content, "Welcome to", FONT_TITLE, FG_DIM).pack(anchor="w")
        _label(self._content, "MoE Network", ("Helvetica", 28, "bold"), FG).pack(anchor="w", pady=(0, 12))

        body = (
            "MoE Network is a decentralised system of AI specialists.\n\n"
            "Each device can host a specialist (STEM or HASS) or simply "
            "chat with the network. Specialists discover each other over "
            "the internet via a DHT — no central server required.\n\n"
            "This wizard will check that Ollama is installed, pull the "
            "models you need, and configure your device."
        )
        _label(self._content, body, FONT_BODY, FG_DIM,
               wraplength=620, justify="left").pack(anchor="w", pady=8)

        _label(self._content, "You need Ollama installed first.",
               FONT_SMALL, FG_DIM).pack(anchor="w", pady=(18, 0))
        link = tk.Label(
            self._content, text="https://ollama.com/download",
            font=FONT_SMALL, fg="#60a5fa", bg=BG, cursor="hand2",
        )
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://ollama.com/download"))

        self._btn_next.config(text="Get Started")

    # ── Step 1: Ollama Check ──────────────────────────────────────────────

    def _build_ollama_check(self) -> None:
        _label(self._content, "Checking Ollama", FONT_HEAD, FG).pack(anchor="w")
        _label(self._content, "Looking for the Ollama CLI on your system…",
               FONT_SMALL, FG_DIM).pack(anchor="w", pady=(4, 16))

        self._ollama_status = _label(self._content, "Checking…", FONT_BODY, FG_DIM)
        self._ollama_status.pack(anchor="w", pady=4)

        self._btn_next.config(text="Continue", state="disabled")
        self.after(200, self._do_ollama_check)

    def _do_ollama_check(self) -> None:
        ok, msg = _check_ollama()
        self._ollama_ok = ok
        self._ollama_ver = msg
        if ok:
            self._ollama_status.config(
                text=f"Ollama found — {msg}", fg=SUCCESS,
            )
            self._btn_next.config(state="normal")
        else:
            self._ollama_status.config(
                text=f"Not found — {msg}", fg=ERROR,
            )
            _label(
                self._content,
                "Please install Ollama first, then click Retry.",
                FONT_SMALL, FG_DIM, wraplength=620,
            ).pack(anchor="w", pady=8)
            self._btn_next.config(text="Retry", state="normal")

    # ── Step 2: Role ──────────────────────────────────────────────────────

    def _build_role(self) -> None:
        _label(self._content, "What does this device do?", FONT_HEAD, FG).pack(anchor="w")
        _label(self._content, "Choose a role. You can change this later in Settings.",
               FONT_SMALL, FG_DIM).pack(anchor="w", pady=(4, 16))

        self._role_var.set(self._cfg.get("role", "user"))

        for key, meta in ROLE_META.items():
            card = tk.Frame(self._content, bg=SURFACE, highlightthickness=1,
                            highlightbackground=BORDER)
            card.pack(fill="x", pady=6)
            card.bind("<Button-1>", lambda e, k=key: self._set_role(k))

            rb = tk.Radiobutton(
                card, variable=self._role_var, value=key,
                bg=SURFACE, fg=FG, selectcolor=SURFACE_2,
                activebackground=SURFACE, activeforeground=FG,
                font=FONT_BODY, anchor="w", padx=12, pady=8,
                command=lambda k=key: self._set_role(k),
            )
            rb.pack(side="left")

            txt = tk.Frame(card, bg=SURFACE)
            txt.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=8)
            _label(txt, meta["title"], FONT_BODY, FG).pack(anchor="w")
            _label(txt, meta["desc"], FONT_SMALL, FG_DIM).pack(anchor="w")

        self._role_models_lbl = _label(
            self._content, "", FONT_SMALL, FG_DIM, wraplength=620,
        )
        self._role_models_lbl.pack(anchor="w", pady=(12, 0))
        self._update_role_models()

        self._btn_next.config(text="Next")

    def _set_role(self, key: str) -> None:
        self._role_var.set(key)
        self._update_role_models()

    def _update_role_models(self) -> None:
        role = self._role_var.get()
        models = ROLE_META[role]["models"]
        self._role_models_lbl.config(
            text=f"Models required:  {', '.join(models)}"
        )

    # ── Step 3: Models ────────────────────────────────────────────────────

    def _build_models(self) -> None:
        _label(self._content, "Pull Models", FONT_HEAD, FG).pack(anchor="w")
        _label(self._content,
               "Downloading the models your role needs. This may take a few minutes.",
               FONT_SMALL, FG_DIM, wraplength=620).pack(anchor="w", pady=(4, 16))

        self._model_rows: dict[str, dict] = {}
        role = self._role_var.get()
        models = ROLE_META[role]["models"]

        for m in models:
            row = tk.Frame(self._content, bg=BG)
            row.pack(fill="x", pady=6)
            name = _label(row, m, FONT_BODY, FG)
            name.pack(side="left")
            status = _label(row, "Checking…", FONT_SMALL, FG_DIM)
            status.pack(side="right")
            bar = ttk.Progressbar(self._content, mode="indeterminate", length=200)
            self._model_rows[m] = {"status": status, "bar": bar, "done": False}

        self._btn_next.config(text="Continue", state="disabled")
        self.after(200, self._start_model_pulls)

    def _start_model_pulls(self) -> None:
        self._installed = _list_models()
        role = self._role_var.get()
        needed = [m for m in ROLE_META[role]["models"] if m not in self._installed]

        if not needed:
            for m in ROLE_META[role]["models"]:
                self._model_rows[m]["status"].config(
                    text="Already installed", fg=SUCCESS,
                )
            self._btn_next.config(state="normal")
            return

        self._pull_done_event.clear()
        self._remaining_pulls = len(needed)

        for m in needed:
            self._model_rows[m]["bar"].pack(fill="x", pady=(0, 8))
            self._model_rows[m]["bar"].start(8)
            self._model_rows[m]["status"].config(text="Pulling…", fg=FG_DIM)
            t = threading.Thread(
                target=self._pull_one, args=(m,), daemon=True,
            )
            t.start()
            self._pull_threads.append(t)

    def _pull_one(self, name: str) -> None:
        exe = _ollama_exe()
        if not exe:
            self.after(0, lambda n=name: self._pull_failed(n, "Ollama not found"))
            return
        try:
            proc = subprocess.Popen(
                [exe, "pull", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            last_pct = 0
            for line in proc.stdout:
                pct = _parse_progress(line)
                if pct != last_pct and pct > 0:
                    last_pct = pct
                    self.after(0, lambda n=name, p=pct: self._update_pull_pct(n, p))
            proc.wait()
            ok = proc.returncode == 0
        except Exception as exc:
            ok = False
            self.after(0, lambda n=name, e=exc: self._pull_failed(n, str(e)))
            return

        self.after(0, lambda n=name, o=ok: self._pull_finished(n, o))

    def _update_pull_pct(self, name: str, pct: int) -> None:
        row = self._model_rows.get(name)
        if row:
            row["status"].config(text=f"Pulling… {pct}%")
            row["bar"].stop()
            row["bar"].config(mode="determinate", value=pct)

    def _pull_failed(self, name: str, err: str) -> None:
        row = self._model_rows.get(name)
        if row:
            row["status"].config(text=f"Failed: {err}", fg=ERROR)
            row["bar"].stop()

    def _pull_finished(self, name: str, ok: bool) -> None:
        row = self._model_rows.get(name)
        if row:
            row["bar"].stop()
            if ok:
                row["status"].config(text="Installed", fg=SUCCESS)
                row["done"] = True
            else:
                row["status"].config(text="Failed", fg=ERROR)

        self._remaining_pulls -= 1
        if self._remaining_pulls <= 0:
            self._btn_next.config(state="normal")

    # ── Step 4: Network ───────────────────────────────────────────────────

    def _build_network(self) -> None:
        _label(self._content, "Network Bootstrap", FONT_HEAD, FG).pack(anchor="w")
        _label(self._content,
               "To find specialists across the internet, enter the address of "
               "any reachable peer (host:port of their DHT port). Leave blank "
               "to stay LAN-only.",
               FONT_SMALL, FG_DIM, wraplength=620).pack(anchor="w", pady=(4, 16))

        _label(self._content, "Bootstrap peer (optional)", FONT_BODY, FG).pack(anchor="w")
        self._bs_entry = tk.Entry(
            self._content, bg=SURFACE, fg=FG, insertbackground=FG,
            relief="flat", font=FONT_BODY,
        )
        self._bs_entry.pack(fill="x", pady=(4, 12), ipady=8)
        self._bs_entry.insert(0, self._cfg.get("bootstrap", ""))

        _label(self._content,
               "Examples:  192.168.1.10:8468   or   my-vps.example.com:8468",
               FONT_TINY, FG_DIM).pack(anchor="w")

        self._btn_next.config(text="Finish")

    # ── Step 5: Done ──────────────────────────────────────────────────────

    def _build_done(self) -> None:
        _label(self._content, "You're all set!", FONT_HEAD, FG).pack(anchor="w", pady=(8, 12))

        role = self._role_var.get()
        meta = ROLE_META[role]
        summary = (
            f"Role:  {meta['title']}\n"
            f"Models:  {', '.join(meta['models'])}\n"
            f"Bootstrap:  {self._cfg.get('bootstrap') or 'LAN-only'}\n"
        )
        _label(self._content, summary, FONT_BODY, FG_DIM,
               wraplength=620, justify="left").pack(anchor="w", pady=8)

        _label(self._content,
               "Click Launch to open the chat interface.",
               FONT_BODY, FG).pack(anchor="w", pady=(12, 0))

        self._btn_back.pack_forget()
        self._btn_next.config(text="Launch", command=self._finish)

    # ── Navigation ────────────────────────────────────────────────────────

    def _next(self) -> None:
        if self._step == 1 and not self._ollama_ok:
            self._show_step(1)  # retry
            return
        if self._step == 2:
            self._cfg["role"] = self._role_var.get()
        if self._step == 4:
            self._cfg["bootstrap"] = self._bs_entry.get().strip()
        if self._step < 5:
            self._show_step(self._step + 1)

    def _prev(self) -> None:
        if self._step > 0:
            self._show_step(self._step - 1)

    def _finish(self) -> None:
        self._cfg["wizard_done"] = True
        save(self._cfg)
        self.destroy()


# ── Entry point ──────────────────────────────────────────────────────────────

def run_if_needed() -> bool:
    """Show wizard if first run. Returns True if wizard was shown."""
    cfg = load()
    if cfg.get("wizard_done"):
        return False
    wizard = Wizard()
    wizard.mainloop()
    return True


def force_run() -> None:
    """Always show wizard (e.g. --wizard flag)."""
    cfg = load()
    cfg["wizard_done"] = False
    save(cfg)
    run_if_needed()


if __name__ == "__main__":
    Wizard().mainloop()
