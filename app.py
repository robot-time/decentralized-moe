"""
app.py -- MoE Network desktop app (Ollama-style UI)
====================================================
Single-window chat app with a left sidebar and main chat area.
Mimics the Ollama desktop app layout:

  ┌──────────┬──────────────────────────────────────┐
  │ Sidebar  │  Header (model / new chat / settings)│
  │          ├──────────────────────────────────────┤
  │  New Chat│                                      │
  │          │  Welcome / Chat Canvas               │
  │  Peers   │                                      │
  │  ──────  │  ┌──────────────────────────┐       │
  │  STEM    │  │ You: question?           │       │
  │  HASS    │  └──────────────────────────┘       │
  │          │  ┌──────────────────────────┐       │
  │  [⚙]     │  │ STEM: answer...          │       │
  │          │  └──────────────────────────┘       │
  │          │                                      │
  ├──────────┼──────────────────────────────────────┤
  │          │  ┌──────────────────────────┐ [Send] │
  │          │  │  Type a message...       │        │
  │          │  └──────────────────────────┘        │
  └──────────┴──────────────────────────────────────┘

What it does:
  - Loads config.yaml
  - If role is stem/hass: starts specialist server
  - Joins DHT to find/announce peers
  - Fans out each query to all discovered specialists
  - Synthesises multiple answers via MoA
"""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable, Optional

from aggregator import aggregate, fan_out
from config import copy_default_experts, ensure_dirs, load, load_expert, save
from network import Network
from specialist import Specialist

# ── Palette (Ollama dark-mode inspired) ─────────────────────────────────────

BG           = "#0f0f0f"
SIDEBAR_BG   = "#171717"
SURFACE      = "#1a1a1a"
SURFACE_2    = "#262626"
BORDER       = "#2e2e2e"
FG           = "#e5e5e5"
FG_DIM       = "#a1a1a1"
FG_MUTED     = "#525252"
ACCENT       = "#ffffff"
ACCENT_FG    = "#000000"
USER_BG      = "#262626"
AI_BG        = "#1a1a1a"
AI_BORDER    = "#333333"
ERROR        = "#ef4444"
SUCCESS      = "#22c55e"
SPECIALIST_TINT = "#60a5fa"

FONT_TITLE   = ("Helvetica", 14, "bold")
FONT_HEAD    = ("Helvetica", 13, "bold")
FONT_BODY    = ("Helvetica", 12)
FONT_SMALL   = ("Helvetica", 11)
FONT_TINY    = ("Helvetica", 9)
FONT_BUBBLE  = ("Helvetica", 12)
FONT_BUBBLE_BOLD = ("Helvetica", 12, "bold")

SIDEBAR_W    = 220
BUBBLE_PAD   = 14
BUBBLE_R     = 14
MAX_BUBBLE_W = 520
WELCOME_TEXT = "What can I help you with?"

ROLE_OPTIONS = [
    ("Chat only", "user"),
    ("STEM specialist", "stem"),
    ("HASS specialist", "hass"),
]


# ── Async worker ────────────────────────────────────────────────────────────

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
        self.geometry("980x720")
        self.configure(bg=BG)
        self.minsize(720, 520)

        ensure_dirs()
        copy_default_experts()
        self.cfg = load()
        self.worker = AsyncWorker()
        self.network: Optional[Network] = None
        self._busy = False
        self._chat_empty = True

        self._build_ui()
        self.worker.start()
        self._start_network_and_specialist()

        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))

        self.after(100, self._input.focus_set)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Sidebar ────────────────────────────────────────────────────
        self._sidebar = tk.Frame(self, bg=SIDEBAR_BG, width=SIDEBAR_W)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        # App title
        tk.Label(
            self._sidebar, text="MoE Network", font=FONT_TITLE,
            bg=SIDEBAR_BG, fg=FG, padx=16, pady=(18, 8),
        ).pack(anchor="w")

        # New chat button
        self._btn_new = tk.Button(
            self._sidebar, text="  +  New chat", font=FONT_SMALL,
            bg=ACCENT, fg=ACCENT_FG, activebackground="#e5e5e5",
            activeforeground=ACCENT_FG, relief="flat",
            padx=12, pady=8, cursor="hand2", borderwidth=0,
            command=self._new_chat,
        )
        self._btn_new.pack(fill="x", padx=12, pady=(8, 16))

        # Divider
        tk.Frame(self._sidebar, bg=BORDER, height=1).pack(fill="x", padx=12)

        # Peers header
        self._peers_header = tk.Label(
            self._sidebar, text="Specialists", font=FONT_TINY,
            bg=SIDEBAR_BG, fg=FG_MUTED, padx=16, pady=(12, 6),
        )
        self._peers_header.pack(anchor="w")

        # Peers list container
        self._peers_frame = tk.Frame(self._sidebar, bg=SIDEBAR_BG)
        self._peers_frame.pack(fill="both", expand=True, padx=12)

        # Role info at bottom
        tk.Frame(self._sidebar, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(8, 0))
        self._role_info = tk.Label(
            self._sidebar, text="", font=FONT_TINY,
            bg=SIDEBAR_BG, fg=FG_MUTED, padx=16, pady=10, anchor="w",
        )
        self._role_info.pack(fill="x")

        # Settings button at very bottom
        self._btn_settings = tk.Button(
            self._sidebar, text="⚙  Settings", font=FONT_SMALL,
            bg=SIDEBAR_BG, fg=FG_DIM, activebackground=SURFACE_2,
            activeforeground=FG, relief="flat", cursor="hand2",
            borderwidth=0, padx=16, pady=10, anchor="w",
            command=self._open_settings,
        )
        self._btn_settings.pack(fill="x", side="bottom")

        self._update_role_info()

        # ── Main area ──────────────────────────────────────────────────
        self._main = tk.Frame(self, bg=BG)
        self._main.pack(side="left", fill="both", expand=True)

        # Header
        self._header = tk.Frame(self._main, bg=BG, height=52)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        self._header_title = tk.Label(
            self._header, text="MoE Network", font=FONT_TITLE,
            bg=BG, fg=FG, padx=20,
        )
        self._header_title.pack(side="left", fill="y")

        self._header_model = tk.Label(
            self._header, text="", font=FONT_SMALL,
            bg=BG, fg=FG_DIM, padx=8,
        )
        self._header_model.pack(side="left", fill="y")

        self._btn_settings_top = tk.Button(
            self._header, text="⚙", font=FONT_HEAD,
            bg=BG, fg=FG_DIM, activebackground=BG,
            activeforeground=FG, relief="flat", cursor="hand2",
            borderwidth=0, padx=16, command=self._open_settings,
        )
        self._btn_settings_top.pack(side="right", fill="y")

        # Divider
        tk.Frame(self._main, bg=BORDER, height=1).pack(fill="x")

        # ── Chat Canvas ────────────────────────────────────────────────
        self._chat_container = tk.Frame(self._main, bg=BG)
        self._chat_container.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            self._chat_container, bg=BG, highlightthickness=0,
        )
        self._canvas.pack(side="left", fill="both", expand=True)

        self._scrollbar = tk.Scrollbar(
            self._chat_container, orient="vertical", command=self._canvas.yview,
            bg=BG, troughcolor=BG, activebackground=SURFACE_2,
        )
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        # Inner frame for messages (placed on canvas)
        self._msg_frame = tk.Frame(self._canvas, bg=BG)
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._msg_frame, anchor="nw",
        )

        self._msg_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Welcome screen (shown when empty)
        self._welcome = tk.Frame(self._msg_frame, bg=BG)
        self._welcome.pack(fill="both", expand=True)
        tk.Label(
            self._welcome, text=WELCOME_TEXT, font=("Helvetica", 22, "bold"),
            bg=BG, fg=FG,
        ).pack(expand=True)

        # Suggested prompts
        self._suggestions = tk.Frame(self._welcome, bg=BG)
        self._suggestions.pack(pady=(12, 80))
        for text in [
            "Explain quantum computing",
            "What caused the French Revolution?",
            "Solve this equation: 2x + 5 = 15",
        ]:
            btn = tk.Button(
                self._suggestions, text=text, font=FONT_SMALL,
                bg=SURFACE, fg=FG_DIM, activebackground=SURFACE_2,
                activeforeground=FG, relief="flat", cursor="hand2",
                borderwidth=0, padx=14, pady=6,
                command=lambda t=text: self._set_input(t),
            )
            btn.pack(side="left", padx=6)

        # ── Input bar ──────────────────────────────────────────────────
        self._input_frame = tk.Frame(self._main, bg=SURFACE, height=72)
        self._input_frame.pack(fill="x", side="bottom")
        self._input_frame.pack_propagate(False)

        self._input = tk.Entry(
            self._input_frame, bg=SURFACE_2, fg=FG,
            insertbackground=FG, relief="flat", font=FONT_BODY,
        )
        self._input.pack(side="left", fill="x", expand=True,
                         padx=(18, 8), pady=14, ipady=10)
        self._input.bind("<Return>", lambda e: self._send())

        self._btn_send = tk.Button(
            self._input_frame, text="➤", font=("Helvetica", 14, "bold"),
            bg=SURFACE_2, fg=FG_DIM, activebackground=SURFACE_2,
            activeforeground=ACCENT, relief="flat", cursor="hand2",
            borderwidth=0, padx=14, pady=4, command=self._send,
        )
        self._btn_send.pack(side="right", padx=(0, 14), pady=14)

        # Status label at very bottom
        self._status = tk.Label(
            self._main, text="Connecting…", font=FONT_TINY,
            bg=BG, fg=FG_MUTED, padx=18, pady=4, anchor="w",
        )
        self._status.pack(fill="x", side="bottom")

    # ── Layout helpers ──────────────────────────────────────────────────

    def _on_frame_configure(self, _event=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        if self._chat_empty:
            self._canvas.yview_moveto(0)
        else:
            self._canvas.yview_moveto(1.0)

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfig(self._canvas_window, width=event.width)

    def _set_input(self, text: str) -> None:
        self._input.delete(0, "end")
        self._input.insert(0, text)
        self._input.focus_set()

    def _new_chat(self) -> None:
        for w in self._msg_frame.winfo_children():
            w.destroy()
        self._chat_empty = True
        self._welcome = tk.Frame(self._msg_frame, bg=BG)
        self._welcome.pack(fill="both", expand=True)
        tk.Label(
            self._welcome, text=WELCOME_TEXT,
            font=("Helvetica", 22, "bold"), bg=BG, fg=FG,
        ).pack(expand=True)
        self._suggestions = tk.Frame(self._welcome, bg=BG)
        self._suggestions.pack(pady=(12, 80))
        for text in [
            "Explain quantum computing",
            "What caused the French Revolution?",
            "Solve this equation: 2x + 5 = 15",
        ]:
            btn = tk.Button(
                self._suggestions, text=text, font=FONT_SMALL,
                bg=SURFACE, fg=FG_DIM, activebackground=SURFACE_2,
                activeforeground=FG, relief="flat", cursor="hand2",
                borderwidth=0, padx=14, pady=6,
                command=lambda t=text: self._set_input(t),
            )
            btn.pack(side="left", padx=6)
        self._on_frame_configure()

    # ── Peer sidebar ────────────────────────────────────────────────────

    def _update_peers(self, peers: list) -> None:
        for w in self._peers_frame.winfo_children():
            w.destroy()
        if not peers:
            tk.Label(
                self._peers_frame, text="No peers yet",
                font=FONT_TINY, bg=SIDEBAR_BG, fg=FG_MUTED,
            ).pack(anchor="w", pady=4)
            return
        seen = set()
        for p in peers:
            key = f"{p.label}@{p.url}"
            if key in seen:
                continue
            seen.add(key)
            row = tk.Frame(self._peers_frame, bg=SIDEBAR_BG)
            row.pack(fill="x", pady=2)
            dot = tk.Label(
                row, text="●", font=("Helvetica", 8),
                bg=SIDEBAR_BG, fg=SUCCESS,
            )
            dot.pack(side="left")
            tk.Label(
                row, text=p.label, font=FONT_SMALL,
                bg=SIDEBAR_BG, fg=FG,
            ).pack(side="left", padx=(4, 0))

    def _update_role_info(self) -> None:
        role = self.cfg.get("role", "user")
        if role == "user":
            self._role_info.config(text="Role: Chat only")
            self._header_model.config(text="")
        else:
            expert = load_expert(role) or {}
            label = expert.get("label", role.upper())
            model = expert.get("model", "?")
            self._role_info.config(text=f"Role: {label}\nModel: {model}")
            self._header_model.config(text=f"  ·  {label} ({model})")

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.config(text=text, fg=ERROR if error else FG_MUTED)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._btn_send.config(fg=FG_DIM if busy else FG)
        self._input.config(state="disabled" if busy else "normal")
        if not busy:
            self._input.focus_set()

    # ── Network startup ─────────────────────────────────────────────────

    def _start_network_and_specialist(self) -> None:
        cfg = self.cfg
        role = cfg.get("role", "user")
        dht_port = int(cfg.get("dht_port", 8468))
        http_port = int(cfg.get("http_port", 8001))
        bootstrap = cfg.get("bootstrap", "") or ""

        my_specialty: Optional[str] = None
        my_label: Optional[str] = None

        async def _bootup() -> None:
            nonlocal my_specialty, my_label

            if role in ("stem", "hass"):
                expert = load_expert(role)
                if expert:
                    spec = Specialist(expert, http_port)
                    asyncio.create_task(spec.serve_forever())
                    my_specialty = spec.specialty
                    my_label = spec.label

            self.network = Network(
                dht_port=dht_port,
                bootstrap=bootstrap,
                my_specialty=my_specialty,
                my_label=my_label,
                my_http_port=http_port,
            )
            await self.network.start()
            self.after(0, lambda: self._set_status(self._status_summary()))
            self.after(3000, self._refresh_peers)

        self.worker.submit(_bootup())

    def _refresh_peers(self) -> None:
        if not self.network:
            return

        async def _do():
            peers = await self.network.discover()
            self.after(0, lambda: self._update_peers(peers))
            self.after(0, lambda: self._set_status(
                f"{len(peers)} specialist{'s' if len(peers) != 1 else ''} online"
                + (f"  ·  {', '.join(sorted({p.label for p in peers}))}" if peers else "")
            ))

        self.worker.submit(_do())
        self.after(5000, self._refresh_peers)

    def _status_summary(self) -> str:
        role = self.cfg.get("role", "user")
        bs = self.cfg.get("bootstrap", "") or "LAN-only"
        return f"Connected  ·  bootstrap={bs}  ·  role={role}"

    # ── Chat messages ───────────────────────────────────────────────────

    def _hide_welcome(self) -> None:
        if self._chat_empty:
            self._chat_empty = False
            self._welcome.destroy()

    def _add_user_bubble(self, text: str) -> None:
        self._hide_welcome()
        container = tk.Frame(self._msg_frame, bg=BG)
        container.pack(fill="x", padx=20, pady=(16, 4))

        # Spacer to push bubble right
        tk.Frame(container, bg=BG).pack(side="left", expand=True, fill="x")

        bubble = tk.Frame(container, bg=USER_BG, padx=BUBBLE_PAD, pady=BUBBLE_PAD)
        bubble.pack(side="right")

        lbl = tk.Label(
            bubble, text=text, font=FONT_BUBBLE, bg=USER_BG, fg=FG,
            wraplength=MAX_BUBBLE_W, justify="left", anchor="w",
        )
        lbl.pack()

        self._on_frame_configure()
        self.after(50, lambda: self._canvas.yview_moveto(1.0))

    def _add_ai_bubble(self, text: str, label: str = "MoE") -> None:
        self._hide_welcome()
        container = tk.Frame(self._msg_frame, bg=BG)
        container.pack(fill="x", padx=20, pady=(16, 4))

        # Avatar + label column
        left = tk.Frame(container, bg=BG)
        left.pack(side="left")

        avatar = tk.Frame(left, bg=SPECIALIST_TINT, width=28, height=28)
        avatar.pack()
        avatar.pack_propagate(False)
        tk.Label(
            avatar, text=label[0].upper(), font=("Helvetica", 11, "bold"),
            bg=SPECIALIST_TINT, fg=BG,
        ).place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            left, text=label, font=FONT_TINY,
            bg=BG, fg=SPECIALIST_TINT,
        ).pack(anchor="w", pady=(4, 0))

        # Bubble
        bubble = tk.Frame(container, bg=AI_BG, padx=BUBBLE_PAD, pady=BUBBLE_PAD,
                          highlightbackground=AI_BORDER, highlightthickness=1)
        bubble.pack(side="left", padx=(10, 0))

        lbl = tk.Label(
            bubble, text=text, font=FONT_BUBBLE, bg=AI_BG, fg=FG,
            wraplength=MAX_BUBBLE_W, justify="left", anchor="w",
        )
        lbl.pack()

        self._on_frame_configure()
        self.after(50, lambda: self._canvas.yview_moveto(1.0))

    def _add_info_line(self, text: str) -> None:
        self._hide_welcome()
        lbl = tk.Label(
            self._msg_frame, text=text, font=FONT_TINY,
            bg=BG, fg=FG_MUTED, padx=20, pady=(4, 8),
        )
        lbl.pack(anchor="w")
        self._on_frame_configure()

    def _add_error_line(self, text: str) -> None:
        self._hide_welcome()
        lbl = tk.Label(
            self._msg_frame, text=text, font=FONT_SMALL,
            bg=BG, fg=ERROR, padx=20, pady=(8, 8),
        )
        lbl.pack(anchor="w")
        self._on_frame_configure()

    # ── Send / receive ──────────────────────────────────────────────────

    def _send(self) -> None:
        if self._busy or self.network is None:
            return
        query = self._input.get().strip()
        if not query:
            return
        self._input.delete(0, "end")

        self._add_user_bubble(query)
        self._add_info_line("Routing through the network…")
        self._set_busy(True)

        async def _do():
            try:
                peers = await self.network.discover()
                replies = await fan_out(peers, query)
                answer = aggregate(
                    query, replies,
                    synthesis_model=self.cfg.get("aggregator_model", "llama3.1:8b"),
                )
                self.after(0, lambda: self._show_answer(answer))
            except Exception as exc:
                self.after(0, lambda: self._show_error(str(exc)))

        self.worker.submit(_do())

    def _show_answer(self, answer) -> None:
        if answer.consulted:
            meta = "  ·  ".join(answer.consulted)
            if answer.synthesised:
                meta += "  ·  synthesised"
            self._add_ai_bubble(answer.text, ", ".join(answer.consulted))
        else:
            self._add_ai_bubble(answer.text, "MoE")
        if answer.skipped:
            self._add_info_line(f"Skipped: {', '.join(answer.skipped)}")
        self._set_busy(False)
        self._canvas.yview_moveto(1.0)

    def _show_error(self, msg: str) -> None:
        self._add_error_line(f"Error: {msg}")
        self._set_busy(False)

    # ── Settings ──────────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        SettingsDialog(self, self.cfg, on_saved=self._on_settings_saved)

    def _on_settings_saved(self, new_cfg: dict) -> None:
        save(new_cfg)
        self.cfg = new_cfg
        self._update_role_info()
        self._add_info_line("Settings saved. Restart the app for role/port changes to take effect.")


# ── Settings dialog ─────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, cfg: dict, on_saved: Callable) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.geometry("480x420")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self._cfg = dict(cfg)
        self._on_saved = on_saved

        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))

        self._build()

    def _build(self) -> None:
        tk.Frame(self, bg=BG, height=16).pack()
        tk.Label(
            self, text="Settings", font=FONT_HEAD,
            bg=BG, fg=FG, anchor="w",
        ).pack(fill="x", padx=24)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)

        # Role
        tk.Label(
            self, text="Role", font=FONT_BODY,
            bg=BG, fg=FG, anchor="w",
        ).pack(fill="x", padx=24)
        self._role_var = tk.StringVar(value=self._cfg.get("role", "user"))
        for label, value in ROLE_OPTIONS:
            rb = tk.Radiobutton(
                self, text=label, variable=self._role_var, value=value,
                bg=BG, fg=FG, selectcolor=SURFACE,
                activebackground=BG, activeforeground=FG,
                font=FONT_SMALL, anchor="w", padx=24,
            )
            rb.pack(fill="x")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)

        # Bootstrap
        tk.Label(
            self, text="Bootstrap peer (host:port)", font=FONT_BODY,
            bg=BG, fg=FG, anchor="w",
        ).pack(fill="x", padx=24)
        tk.Label(
            self, text="Leave blank for LAN-only. Used to discover peers across the internet.",
            font=FONT_TINY, bg=BG, fg=FG_MUTED, anchor="w", wraplength=420,
        ).pack(fill="x", padx=24, pady=(0, 4))
        self._bs_var = tk.StringVar(value=self._cfg.get("bootstrap", ""))
        tk.Entry(
            self, textvariable=self._bs_var,
            bg=SURFACE, fg=FG, insertbackground=FG,
            relief="flat", font=FONT_BODY,
        ).pack(fill="x", padx=24, ipady=6)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)

        # Buttons
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=24, pady=(0, 16))
        tk.Button(
            btns, text="Cancel", command=self.destroy,
            bg=SURFACE, fg=FG, activebackground=SURFACE_2,
            activeforeground=FG, relief="flat",
            font=FONT_BODY, padx=14, pady=6, cursor="hand2",
        ).pack(side="right")
        tk.Button(
            btns, text="Save", command=self._save,
            bg=ACCENT, fg=ACCENT_FG, activebackground="#e5e5e5",
            activeforeground=ACCENT_FG, relief="flat",
            font=FONT_BODY, padx=18, pady=6, cursor="hand2",
        ).pack(side="right", padx=(0, 8))

    def _save(self) -> None:
        self._cfg["role"] = self._role_var.get()
        self._cfg["bootstrap"] = self._bs_var.get().strip()
        self._on_saved(self._cfg)
        self.destroy()


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
