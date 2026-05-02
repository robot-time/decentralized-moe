"""
app.py -- MoE Network desktop app (Ollama-style)
=================================================
Single window, dark theme, chat-style UI.  This is the only entry point
for users — no tray, no wizard, no setup popups.

What it does:
  - Loads ~/.moe-network/config.yaml (creates it on first run)
  - If role is "stem" or "hass": starts that specialist server in a
    background thread (FastAPI on the configured http_port)
  - Joins the DHT to find/announce peers
  - Shows a chat window.  Each user message is fanned out to every
    discovered specialist in parallel, mismatches are filtered, and
    multiple responses are synthesised via MoA.

A single "Settings" gear in the top-right lets the user change role,
model, and bootstrap address without leaving the app.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Optional

from aggregator import aggregate, fan_out
from config import (
    CONFIG_FILE, EXPERTS_DIR, copy_default_experts, ensure_dirs,
    load, load_expert, save,
)
from network import Network
from specialist import Specialist

# ── Palette (Ollama-inspired) ─────────────────────────────────────────────────

BG          = "#0a0a0a"
SURFACE     = "#171717"
SURFACE_2   = "#1f1f1f"
BORDER      = "#262626"
FG          = "#fafafa"
FG_DIM      = "#737373"
USER_TINT   = "#3b82f6"
AI_TINT     = "#22c55e"
ERROR_TINT  = "#ef4444"
ACCENT      = "#ffffff"
ACCENT_FG   = "#000000"

FONT_BODY  = ("Helvetica", 12)
FONT_BOLD  = ("Helvetica", 12, "bold")
FONT_SMALL = ("Helvetica", 10)
FONT_TITLE = ("Helvetica", 15, "bold")

ROLE_OPTIONS = [
    ("Just chat — don't host a specialist", "user"),
    ("STEM specialist (math, science, code, engineering)", "stem"),
    ("HASS specialist (society, ethics, history, philosophy)", "hass"),
]


# ── Background asyncio thread ────────────────────────────────────────────────
# pystray-free: we run a single asyncio loop in a daemon thread.  All network
# work (specialist server, DHT, fan-out) happens there.  The Tkinter main
# thread only schedules tasks via run_coroutine_threadsafe and reads results
# back via root.after().

class AsyncWorker:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="moe-async"
        )

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        self._thread.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


# ── App ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MoE Network")
        self.geometry("860x680")
        self.configure(bg=BG)
        self.minsize(620, 480)

        ensure_dirs()
        copy_default_experts()
        self.cfg     = load()
        self.worker  = AsyncWorker()
        self.network: Optional[Network] = None
        self._busy   = False

        self._build_ui()
        self.worker.start()
        self._start_network_and_specialist()
        self._set_status("Connecting…")
        self.after(100, self.entry.focus_set)

        # Force window to front on first launch
        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Header
        header = tk.Frame(self, bg=SURFACE, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header, text="MoE Network", font=FONT_TITLE,
            bg=SURFACE, fg=FG, padx=18,
        ).pack(side="left", fill="y")

        self.role_label = tk.Label(
            header, text="", font=FONT_SMALL,
            bg=SURFACE, fg=FG_DIM, padx=12,
        )
        self.role_label.pack(side="left", fill="y")

        gear = tk.Button(
            header, text="⚙  Settings", command=self._open_settings,
            bg=SURFACE, fg=FG, activebackground=SURFACE_2,
            activeforeground=FG, relief="flat", font=FONT_SMALL,
            padx=14, cursor="hand2", borderwidth=0,
        )
        gear.pack(side="right", padx=12)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Chat
        self.chat = scrolledtext.ScrolledText(
            self, bg=BG, fg=FG, font=FONT_BODY,
            insertbackground=FG, relief="flat", wrap="word",
            padx=20, pady=18, state="disabled",
        )
        self.chat.pack(fill="both", expand=True)

        for tag, color, font in [
            ("user_label", USER_TINT, FONT_BOLD),
            ("user_body",  FG,        FONT_BODY),
            ("ai_label",   AI_TINT,   FONT_BOLD),
            ("ai_body",    FG,        FONT_BODY),
            ("info",       FG_DIM,    ("Helvetica", 10, "italic")),
            ("error",      ERROR_TINT, FONT_BODY),
        ]:
            self.chat.tag_config(tag, foreground=color, font=font)

        self._append(
            "Welcome to the MoE Network.\n"
            "Type a question below — it will be routed to specialists across "
            "the network.\n\n",
            "info",
        )

        # Input bar
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        bar = tk.Frame(self, bg=SURFACE)
        bar.pack(fill="x")

        self.entry = tk.Entry(
            bar, bg=SURFACE_2, fg=FG, insertbackground=FG,
            relief="flat", font=FONT_BODY,
        )
        self.entry.pack(side="left", fill="x", expand=True,
                        padx=(16, 8), pady=14, ipady=10)
        self.entry.bind("<Return>", lambda e: self._send())

        self.send_btn = tk.Button(
            bar, text="Send", command=self._send,
            bg=ACCENT, fg=ACCENT_FG, activebackground="#e5e5e5",
            activeforeground=ACCENT_FG, font=FONT_BOLD,
            relief="flat", padx=24, pady=10, cursor="hand2",
        )
        self.send_btn.pack(side="right", padx=(0, 16), pady=14)

        # Footer status
        self.status = tk.Label(
            self, text="", font=FONT_SMALL,
            bg=BG, fg=FG_DIM, padx=18, pady=6, anchor="w",
        )
        self.status.pack(fill="x")

        self._update_role_label()

    def _update_role_label(self) -> None:
        role = self.cfg.get("role", "user")
        if role == "user":
            self.role_label.config(text="Mode: client only")
        else:
            expert = load_expert(role) or {}
            label = expert.get("label", role.upper())
            self.role_label.config(text=f"Hosting: {label}")

    # ── Helpers ───────────────────────────────────────────────────────────

    def _append(self, text: str, tag: str = "") -> None:
        self.chat.config(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.config(text=text, fg=ERROR_TINT if error else FG_DIM)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.config(state="disabled" if busy else "normal")
        if not busy:
            self.entry.focus_set()

    # ── Network startup ───────────────────────────────────────────────────

    def _start_network_and_specialist(self) -> None:
        cfg  = self.cfg
        role = cfg.get("role", "user")
        dht_port  = int(cfg.get("dht_port",  8468))
        http_port = int(cfg.get("http_port", 8001))
        bootstrap = cfg.get("bootstrap", "") or ""

        my_specialty: Optional[str] = None
        my_label:     Optional[str] = None

        async def _bootup() -> None:
            nonlocal my_specialty, my_label

            if role in ("stem", "hass"):
                expert = load_expert(role)
                if expert:
                    spec = Specialist(expert, http_port)
                    asyncio.create_task(spec.serve_forever())
                    my_specialty = spec.specialty
                    my_label     = spec.label

            self.network = Network(
                dht_port=dht_port,
                bootstrap=bootstrap,
                my_specialty=my_specialty,
                my_label=my_label,
                my_http_port=http_port,
            )
            await self.network.start()
            self.after(0, lambda: self._set_status(self._status_summary()))
            self.after(5000, self._refresh_status)

        self.worker.submit(_bootup())

    def _refresh_status(self) -> None:
        if not self.network:
            return
        async def _do():
            peers = await self.network.discover()
            self.after(0, lambda: self._set_status(
                f"{len(peers)} specialist{'s' if len(peers) != 1 else ''} "
                f"online" + (
                    " · " + ", ".join(sorted({p.label for p in peers}))
                    if peers else ""
                )
            ))
        self.worker.submit(_do())
        self.after(5000, self._refresh_status)

    def _status_summary(self) -> str:
        cfg = self.cfg
        role = cfg.get("role", "user")
        bs   = cfg.get("bootstrap", "") or "LAN-only"
        return f"Connected · bootstrap={bs} · role={role}"

    # ── Send ──────────────────────────────────────────────────────────────

    def _send(self) -> None:
        if self._busy or self.network is None:
            return
        query = self.entry.get().strip()
        if not query:
            return
        self.entry.delete(0, "end")

        self._append("You\n", "user_label")
        self._append(query + "\n\n", "user_body")
        self._append("Routing through the network…\n\n", "info")
        self._set_busy(True)

        async def _do():
            try:
                peers = await self.network.discover()
                replies = await fan_out(peers, query)
                answer  = aggregate(
                    query, replies,
                    synthesis_model=self.cfg.get(
                        "aggregator_model", "llama3.1:8b"
                    ),
                )
                self.after(0, lambda: self._show_answer(answer))
            except Exception as exc:
                self.after(0, lambda: self._show_error(str(exc)))

        self.worker.submit(_do())

    def _show_answer(self, answer) -> None:
        if answer.consulted:
            tag_line = "[" + ", ".join(answer.consulted) + "]"
            if answer.synthesised:
                tag_line += "  (synthesised)"
            self._append(tag_line + "\n", "info")
        if answer.skipped:
            self._append(
                "(skipped: " + ", ".join(answer.skipped) + ")\n", "info"
            )
        self._append("MoE\n", "ai_label")
        self._append(answer.text + "\n\n", "ai_body")
        self._set_busy(False)

    def _show_error(self, msg: str) -> None:
        self._append(f"Error: {msg}\n\n", "error")
        self._set_busy(False)

    # ── Settings ──────────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        SettingsDialog(self, self.cfg, on_saved=self._on_settings_saved)

    def _on_settings_saved(self, new_cfg: dict) -> None:
        save(new_cfg)
        self.cfg = new_cfg
        self._update_role_label()
        self._append(
            "Settings saved.  Restart the app for role/port changes "
            "to take effect.\n\n",
            "info",
        )


# ── Settings dialog ──────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, cfg: dict, on_saved) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.geometry("520x460")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self._cfg     = dict(cfg)
        self._on_saved = on_saved

        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))

        self._build()

    def _build(self) -> None:
        tk.Frame(self, bg=BG, height=18).pack()
        tk.Label(
            self, text="Settings", font=FONT_TITLE,
            bg=BG, fg=FG, anchor="w",
        ).pack(fill="x", padx=24)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=12)

        # Role
        tk.Label(
            self, text="What does this device do?", font=FONT_BOLD,
            bg=BG, fg=FG, anchor="w",
        ).pack(fill="x", padx=24)
        self._role_var = tk.StringVar(value=self._cfg.get("role", "user"))
        for label, value in ROLE_OPTIONS:
            rb = tk.Radiobutton(
                self, text=label, variable=self._role_var, value=value,
                bg=BG, fg=FG, selectcolor=SURFACE,
                activebackground=BG, activeforeground=FG,
                font=FONT_BODY, anchor="w", padx=24,
            )
            rb.pack(fill="x")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=12)

        # Bootstrap
        tk.Label(
            self, text="Bootstrap node (host:port of any reachable peer)",
            font=FONT_BOLD, bg=BG, fg=FG, anchor="w",
        ).pack(fill="x", padx=24)
        tk.Label(
            self,
            text="Leave blank to stay LAN-only.  Used to discover other "
                 "specialists across the internet.",
            font=FONT_SMALL, bg=BG, fg=FG_DIM, anchor="w",
            wraplength=460, justify="left",
        ).pack(fill="x", padx=24, pady=(0, 4))
        self._bs_var = tk.StringVar(value=self._cfg.get("bootstrap", ""))
        tk.Entry(
            self, textvariable=self._bs_var,
            bg=SURFACE, fg=FG, insertbackground=FG,
            relief="flat", font=FONT_BODY,
        ).pack(fill="x", padx=24, ipady=6)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=12)

        # Buttons
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=24, pady=(0, 18))
        tk.Button(
            btns, text="Cancel", command=self.destroy,
            bg=SURFACE, fg=FG, activebackground=SURFACE_2,
            activeforeground=FG, relief="flat",
            font=FONT_BODY, padx=16, pady=6, cursor="hand2",
        ).pack(side="right")
        tk.Button(
            btns, text="Save", command=self._save,
            bg=ACCENT, fg=ACCENT_FG, activebackground="#e5e5e5",
            activeforeground=ACCENT_FG, relief="flat",
            font=FONT_BOLD, padx=20, pady=6, cursor="hand2",
        ).pack(side="right", padx=(0, 8))

    def _save(self) -> None:
        self._cfg["role"]      = self._role_var.get()
        self._cfg["bootstrap"] = self._bs_var.get().strip()
        self._on_saved(self._cfg)
        self.destroy()


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    # Subprocess dispatch (frozen build re-invokes itself for specialists)
    if "--specialist" in sys.argv:
        idx = sys.argv.index("--specialist")
        spec = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "stem"
        from specialist import run_specialist
        run_specialist(spec)
        return

    App().mainloop()


if __name__ == "__main__":
    main()
