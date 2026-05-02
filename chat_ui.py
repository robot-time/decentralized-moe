"""
chat_ui.py -- Chat window for the MoE Network
==============================================
A Tkinter chat window that talks to a local node's /ask endpoint.

Replaces the old terminal-based chat (ask.py) for the bundled app.  On
Windows, the bundle is built with --windowed (no console subsystem) so a
terminal-based CLI cannot read stdin -- a GUI chat is the only thing that
works reliably across all platforms when frozen.

ask.py is preserved for command-line use from source.

Standalone use:
    python chat_ui.py [http://host:port]

Or via the bundled exe:
    MoE-Network.exe --chat [http://host:port]
"""

import sys
import threading
import tkinter as tk
from tkinter import scrolledtext

import requests

DEFAULT_URL = "http://localhost:8001"

# Palette (matches setup_wizard.py / settings_ui.py)
BG          = "#0d0d0d"
SURFACE     = "#1a1a1a"
BORDER      = "#2e2e2e"
FG          = "#ffffff"
FG_DIM      = "#888888"
USER_COLOR  = "#3b82f6"
AI_COLOR    = "#22c55e"
ERROR_COLOR = "#ef4444"
INFO_COLOR  = "#888888"

FONT_BODY  = ("Helvetica", 11)
FONT_BOLD  = ("Helvetica", 11, "bold")
FONT_TITLE = ("Helvetica", 14, "bold")
FONT_SMALL = ("Helvetica", 10)


class ChatWindow(tk.Tk):
    def __init__(self, base_url: str = DEFAULT_URL):
        super().__init__()
        self.title("MoE Network — Chat")
        self.geometry("760x620")
        self.configure(bg=BG)

        self.base_url = base_url.rstrip("/")
        self.ask_url  = f"{self.base_url}/ask"
        self._busy    = False

        # Force window to the front (especially needed on Windows)
        self.lift()
        self.focus_force()
        if sys.platform == "win32":
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))

        self._build()
        self._check_node()
        self.after(100, self.entry.focus_set)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build(self):
        # Header bar
        header = tk.Frame(self, bg=SURFACE)
        header.pack(fill="x")
        tk.Label(
            header, text="MoE Network", font=FONT_TITLE,
            bg=SURFACE, fg=FG, padx=16, pady=12,
        ).pack(side="left")
        self.status_label = tk.Label(
            header, text="Connecting…", font=FONT_SMALL,
            bg=SURFACE, fg=FG_DIM, padx=16, pady=12,
        )
        self.status_label.pack(side="right")

        # Divider
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Conversation area
        self.chat = scrolledtext.ScrolledText(
            self, bg=BG, fg=FG, font=FONT_BODY,
            insertbackground=FG, relief="flat",
            padx=16, pady=16, state="disabled", wrap="word",
        )
        self.chat.pack(fill="both", expand=True)

        # Tag styles
        self.chat.tag_config("user_label", foreground=USER_COLOR, font=FONT_BOLD)
        self.chat.tag_config("user_body",  foreground=FG)
        self.chat.tag_config("ai_label",   foreground=AI_COLOR, font=FONT_BOLD)
        self.chat.tag_config("ai_body",    foreground=FG)
        self.chat.tag_config("error",      foreground=ERROR_COLOR)
        self.chat.tag_config("info",       foreground=INFO_COLOR,
                             font=("Helvetica", 10, "italic"))

        # Welcome banner
        self._append("Welcome. Type a question below and press Enter.\n\n", "info")

        # Input row
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        bar = tk.Frame(self, bg=SURFACE)
        bar.pack(fill="x")

        self.entry = tk.Entry(
            bar, bg=BG, fg=FG, font=FONT_BODY,
            insertbackground=FG, relief="flat",
        )
        self.entry.pack(side="left", fill="x", expand=True,
                        padx=(16, 8), pady=14, ipady=8)
        self.entry.bind("<Return>", lambda e: self._send())

        self.send_btn = tk.Button(
            bar, text="Send", command=self._send,
            bg="#ffffff", fg="#000000", activebackground="#e5e5e5",
            activeforeground="#000000", font=FONT_BOLD,
            relief="flat", padx=22, pady=8, cursor="hand2",
        )
        self.send_btn.pack(side="right", padx=(0, 16), pady=14)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _append(self, text: str, tag: str = "") -> None:
        self.chat.config(state="normal")
        self.chat.insert("end", text, tag)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.config(
            text=text, fg=ERROR_COLOR if error else FG_DIM
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.config(state="disabled" if busy else "normal")
        self.entry.config(state="disabled" if busy else "normal")
        if not busy:
            self.entry.focus_set()

    # ── Node connectivity ────────────────────────────────────────────────

    def _check_node(self) -> None:
        def _do():
            try:
                r = requests.get(f"{self.base_url}/health", timeout=5)
                d = r.json()
                self.after(0, lambda: self._set_status(
                    f"Connected — {d.get('specialty','?')} ({d.get('model','?')})"
                ))
            except Exception as exc:
                self.after(0, lambda: self._set_status(
                    f"No node at {self.base_url} — start the network from the tray",
                    error=True,
                ))
                self.after(0, lambda: self._append(
                    f"Cannot reach a node at {self.base_url}. "
                    f"Open the tray and click Start Network, then try again.\n\n",
                    "error",
                ))
        threading.Thread(target=_do, daemon=True).start()

    # ── Send query ───────────────────────────────────────────────────────

    def _send(self) -> None:
        if self._busy:
            return
        query = self.entry.get().strip()
        if not query:
            return
        self.entry.delete(0, "end")

        self._append("You\n",  "user_label")
        self._append(query + "\n\n", "user_body")
        self._append("Routing through the network…\n\n", "info")

        self._set_busy(True)
        threading.Thread(
            target=self._fetch, args=(query,), daemon=True
        ).start()

    def _fetch(self, query: str) -> None:
        try:
            r = requests.post(self.ask_url, json={"query": query}, timeout=300)
            r.raise_for_status()
            data    = r.json()
            answer  = data.get("answer", "(no answer)")
            experts = data.get("peers_queried", [])
            orchestrator = data.get("orchestrated_by", "?")

            def _show():
                self._append(
                    f"[Orchestrated by {orchestrator}; experts: "
                    f"{', '.join(experts) or 'none'}]\n",
                    "info",
                )
                self._append("MoE\n", "ai_label")
                self._append(answer + "\n\n", "ai_body")
                self._set_busy(False)
            self.after(0, _show)

        except Exception as exc:
            def _err():
                self._append(f"Error: {exc}\n\n", "error")
                self._set_busy(False)
            self.after(0, _err)


def run_chat_standalone(base_url: str = DEFAULT_URL) -> None:
    win = ChatWindow(base_url)
    win.mainloop()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    run_chat_standalone(url)
