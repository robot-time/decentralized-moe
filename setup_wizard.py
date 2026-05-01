"""
setup_wizard.py -- First-Run Setup Wizard
==========================================
A friendly GUI wizard that runs once when the app is first opened.
Uses only tkinter (built into Python -- no extra install needed).

Steps:
  1. Welcome + Ollama check
  2. Choose experts (which specialties to run, which model for each)
  3. Pull models (live progress)
  4. Done -- launches the tray app

All files are written to the user's home directory. No admin needed.
"""

import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import ttk
import tkinter as tk
from typing import Callable

import yaml

from app_paths import (
    APP_DIR, EXPERTS_DIR, FIRST_RUN_SENTINEL,
    copy_default_experts, ensure_dirs,
)

# ── Expert definitions ────────────────────────────────────────────────────────
# Each expert has a display name, port, and a list of recommended Ollama models.
# The user picks one model per expert in step 2.

EXPERT_DEFINITIONS = [
    {
        "specialty":   "math",
        "label":       "Math",
        "description": "Algebra, calculus, statistics, proofs",
        "http_port":   8001,
        "dht_port":    8468,
        "is_bootstrap": True,
        "models": [
            "qwen2.5-math:7b",
            "mathstral:7b",
            "deepseek-r1:7b",
            "llama3:8b",
        ],
        "domain_desc": "mathematics algebra calculus equations geometry statistics probability proofs",
        "system_prompt": (
            "You are a mathematics expert specializing in algebra, calculus, geometry, "
            "statistics, and probability. Always show step-by-step working. Be rigorous."
        ),
    },
    {
        "specialty":   "english",
        "label":       "English / Writing",
        "description": "Grammar, writing, literature, editing",
        "http_port":   8002,
        "dht_port":    8469,
        "is_bootstrap": False,
        "models": [
            "mistral:7b",
            "llama3:8b",
            "gemma2:9b",
            "phi3:medium",
        ],
        "domain_desc": "english writing grammar language literature essays poetry prose vocabulary rhetoric editing",
        "system_prompt": (
            "You are an English language and literature expert. You excel at grammar, "
            "style, composition, literary analysis, creative writing, and editing. "
            "Communicate with clarity and elegance."
        ),
    },
    {
        "specialty":   "code",
        "label":       "Coding",
        "description": "Programming, debugging, algorithms",
        "http_port":   8003,
        "dht_port":    8471,
        "is_bootstrap": False,
        "models": [
            "qwen2.5-coder:7b",
            "deepseek-coder:6.7b",
            "codellama:7b",
            "starcoder2:7b",
        ],
        "domain_desc": "coding programming software python javascript debugging algorithms data structures system design",
        "system_prompt": (
            "You are a software engineering expert. You write clean, efficient, well-commented "
            "code in any language. Always include working code examples with clear explanations."
        ),
    },
    {
        "specialty":   "science",
        "label":       "Science",
        "description": "Physics, chemistry, biology",
        "http_port":   8004,
        "dht_port":    8472,
        "is_bootstrap": False,
        "models": [
            "llama3:8b",
            "gemma2:9b",
            "mistral:7b",
            "phi4:14b",
        ],
        "domain_desc": "science physics chemistry biology research experiments scientific method empirical evidence",
        "system_prompt": (
            "You are a natural sciences expert covering physics, chemistry, and biology. "
            "Ground your answers in empirical evidence and scientific consensus."
        ),
    },
]

SYNTHESIS_MODEL_OPTIONS = ["llama3:8b", "mistral:7b", "gemma2:9b", "phi4:14b"]

# Colours
BG       = "#1e1e2e"
FG       = "#cdd6f4"
ACCENT   = "#89b4fa"
SUCCESS  = "#a6e3a1"
WARNING  = "#f9e2af"
ERROR    = "#f38ba8"
CARD_BG  = "#313244"
BTN_BG   = "#45475a"


# ── Wizard ────────────────────────────────────────────────────────────────────

class SetupWizard(tk.Tk):
    """
    Multi-step setup wizard using a simple page-switching pattern.
    Each page is a Frame that is shown/hidden by the navigator.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("MoE Network Setup")
        self.geometry("640x520")
        self.resizable(False, False)
        self.configure(bg=BG)

        # State shared across pages
        self.ollama_ok   = False
        self.expert_vars: dict[str, dict] = {}   # specialty -> {enabled, model}
        self.synthesis_var = tk.StringVar(value=SYNTHESIS_MODEL_OPTIONS[0])

        # Page container
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        # Bottom navigation bar
        self._build_nav()

        # Pages
        self.pages: list[tk.Frame] = [
            WelcomePage(self.container, self),
            ExpertPage(self.container, self),
            DownloadPage(self.container, self),
            DonePage(self.container, self),
        ]
        for page in self.pages:
            page.place(relwidth=1, relheight=1)

        self.current = 0
        self._show_page(0)

    def _build_nav(self) -> None:
        nav = tk.Frame(self, bg=CARD_BG, height=56)
        nav.pack(side="bottom", fill="x")
        nav.pack_propagate(False)

        self.back_btn = tk.Button(
            nav, text="← Back", command=self._go_back,
            bg=BTN_BG, fg=FG, relief="flat", font=("Helvetica", 11),
            padx=16, pady=6, cursor="hand2",
        )
        self.back_btn.pack(side="left", padx=16, pady=10)

        self.next_btn = tk.Button(
            nav, text="Next →", command=self._go_next,
            bg=ACCENT, fg=BG, relief="flat", font=("Helvetica", 11, "bold"),
            padx=16, pady=6, cursor="hand2",
        )
        self.next_btn.pack(side="right", padx=16, pady=10)

    def _show_page(self, index: int) -> None:
        self.pages[index].lift()
        self.pages[index].on_show()
        self.back_btn.config(state="normal" if index > 0 else "disabled")
        self.next_btn.config(
            text="Finish" if index == len(self.pages) - 1 else "Next →",
            state="normal",
        )

    def _go_next(self) -> None:
        page = self.pages[self.current]
        if not page.validate():
            return
        if self.current == len(self.pages) - 1:
            self.destroy()
            return
        self.current += 1
        self._show_page(self.current)

    def _go_back(self) -> None:
        if self.current > 0:
            self.current -= 1
            self._show_page(self.current)

    def set_next_enabled(self, enabled: bool) -> None:
        self.next_btn.config(state="normal" if enabled else "disabled")

    def get_selected_experts(self) -> list[dict]:
        """Return list of expert definition dicts for enabled experts."""
        selected = []
        for defn in EXPERT_DEFINITIONS:
            s = defn["specialty"]
            if s in self.expert_vars and self.expert_vars[s]["enabled"].get():
                chosen_model = self.expert_vars[s]["model"].get()
                selected.append({**defn, "model": chosen_model})
        return selected


# ── Page base ─────────────────────────────────────────────────────────────────

class BasePage(tk.Frame):
    def __init__(self, parent: tk.Frame, wizard: SetupWizard) -> None:
        super().__init__(parent, bg=BG)
        self.wizard = wizard

    def on_show(self) -> None:
        pass

    def validate(self) -> bool:
        return True

    def _title(self, text: str) -> None:
        tk.Label(self, text=text, bg=BG, fg=ACCENT,
                 font=("Helvetica", 20, "bold")).pack(pady=(32, 4))

    def _subtitle(self, text: str) -> None:
        tk.Label(self, text=text, bg=BG, fg=FG,
                 font=("Helvetica", 12), wraplength=560).pack(pady=(0, 20))


# ── Page 1: Welcome + Ollama check ───────────────────────────────────────────

class WelcomePage(BasePage):
    def on_show(self) -> None:
        for w in self.winfo_children():
            w.destroy()

        self._title("Welcome to MoE Network")
        self._subtitle(
            "A decentralized AI network that runs entirely on your machine.\n"
            "This wizard will get you set up in about 5 minutes."
        )

        # Ollama check card
        card = tk.Frame(self, bg=CARD_BG, padx=24, pady=20)
        card.pack(fill="x", padx=40)

        tk.Label(card, text="Checking for Ollama...", bg=CARD_BG, fg=FG,
                 font=("Helvetica", 13, "bold")).pack(anchor="w")

        self.status_label = tk.Label(card, text="", bg=CARD_BG, fg=FG,
                                     font=("Helvetica", 11))
        self.status_label.pack(anchor="w", pady=(6, 0))

        self.link_btn = tk.Button(
            card, text="Download Ollama  →",
            command=lambda: webbrowser.open("https://ollama.com/download"),
            bg=ACCENT, fg=BG, relief="flat", font=("Helvetica", 11, "bold"),
            padx=12, pady=5, cursor="hand2",
        )

        self.retry_btn = tk.Button(
            card, text="Check Again",
            command=self.on_show,
            bg=BTN_BG, fg=FG, relief="flat", font=("Helvetica", 11),
            padx=12, pady=5, cursor="hand2",
        )

        threading.Thread(target=self._check_ollama, daemon=True).start()

    def _check_ollama(self) -> None:
        found = shutil.which("ollama") is not None
        self.wizard.ollama_ok = found
        self.after(0, self._update_status, found)

    def _update_status(self, found: bool) -> None:
        if found:
            self.status_label.config(
                text="✓  Ollama is installed. You're ready to go.",
                fg=SUCCESS,
            )
            self.link_btn.pack_forget()
            self.retry_btn.pack_forget()
            self.wizard.set_next_enabled(True)
        else:
            self.status_label.config(
                text="✗  Ollama not found. Install it first (free, no account needed).",
                fg=ERROR,
            )
            self.link_btn.pack(anchor="w", pady=(10, 4))
            self.retry_btn.pack(anchor="w")
            self.wizard.set_next_enabled(False)

    def validate(self) -> bool:
        if not self.wizard.ollama_ok:
            self.wizard.set_next_enabled(False)
            return False
        return True


# ── Page 2: Expert & model selection ─────────────────────────────────────────

class ExpertPage(BasePage):
    def on_show(self) -> None:
        for w in self.winfo_children():
            w.destroy()

        self._title("Choose Your Experts")
        self._subtitle("Select which AI specialists to run and pick a model for each.")

        scroll_frame = _ScrollFrame(self)
        scroll_frame.pack(fill="both", expand=True, padx=40, pady=(0, 8))
        inner = scroll_frame.inner

        for defn in EXPERT_DEFINITIONS:
            s = defn["specialty"]
            if s not in self.wizard.expert_vars:
                self.wizard.expert_vars[s] = {
                    "enabled": tk.BooleanVar(value=True),
                    "model":   tk.StringVar(value=defn["models"][0]),
                }
            self._expert_card(inner, defn)

        # Synthesis model
        synth_row = tk.Frame(inner, bg=BG)
        synth_row.pack(fill="x", pady=(8, 0))
        tk.Label(synth_row, text="Synthesis model:", bg=BG, fg=FG,
                 font=("Helvetica", 11)).pack(side="left")
        ttk.Combobox(
            synth_row, textvariable=self.wizard.synthesis_var,
            values=SYNTHESIS_MODEL_OPTIONS, state="readonly", width=24,
        ).pack(side="left", padx=8)

    def _expert_card(self, parent: tk.Frame, defn: dict) -> None:
        s     = defn["specialty"]
        evars = self.wizard.expert_vars[s]

        card = tk.Frame(parent, bg=CARD_BG, padx=16, pady=10)
        card.pack(fill="x", pady=4)

        top = tk.Frame(card, bg=CARD_BG)
        top.pack(fill="x")

        tk.Checkbutton(
            top, text=defn["label"], variable=evars["enabled"],
            bg=CARD_BG, fg=FG, selectcolor=BG, activebackground=CARD_BG,
            font=("Helvetica", 12, "bold"), cursor="hand2",
        ).pack(side="left")

        ttk.Combobox(
            top, textvariable=evars["model"],
            values=defn["models"], state="readonly", width=26,
        ).pack(side="right")

        tk.Label(card, text=defn["description"], bg=CARD_BG,
                 fg="#a6adc8", font=("Helvetica", 10)).pack(anchor="w")

    def validate(self) -> bool:
        selected = self.wizard.get_selected_experts()
        if not selected:
            tk.messagebox.showwarning("No experts selected",
                                      "Please enable at least one expert.")
            return False
        return True


# ── Page 3: Model download ────────────────────────────────────────────────────

class DownloadPage(BasePage):
    def __init__(self, parent: tk.Frame, wizard: SetupWizard) -> None:
        super().__init__(parent, wizard)
        self._started = False

    def on_show(self) -> None:
        if self._started:
            return
        self._started = True

        for w in self.winfo_children():
            w.destroy()

        self._title("Downloading Models")
        self._subtitle("This may take a few minutes depending on your internet speed.\n"
                       "Already-downloaded models are instant.")

        self.log = tk.Text(
            self, bg=CARD_BG, fg=FG, font=("Courier", 10),
            relief="flat", state="disabled", wrap="word",
        )
        self.log.pack(fill="both", expand=True, padx=40, pady=(0, 8))

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=40, pady=(0, 8))
        self.progress.start(12)

        self.wizard.set_next_enabled(False)
        threading.Thread(target=self._download_all, daemon=True).start()

    def _log(self, text: str, color: str = FG) -> None:
        def _do():
            self.log.config(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.config(state="disabled")
        self.after(0, _do)

    def _download_all(self) -> None:
        experts   = self.wizard.get_selected_experts()
        synthesis = self.wizard.synthesis_var.get()

        # Collect all unique models needed
        models_needed: list[tuple[str, str]] = []
        seen: set[str] = set()
        for e in experts:
            m = e["model"]
            if m not in seen:
                models_needed.append((e["label"], m))
                seen.add(m)
        if synthesis not in seen:
            models_needed.append(("Synthesis", synthesis))

        all_ok = True
        for label, model in models_needed:
            self._log(f"\n[{label}] Pulling {model} ...")
            ok = self._pull_model(model)
            if ok:
                self._log(f"  ✓ {model} ready", SUCCESS)
            else:
                self._log(f"  ✗ Failed to pull {model}", ERROR)
                all_ok = False

        if all_ok:
            self._log("\nAll models ready. Writing config...", SUCCESS)
            self._write_configs(experts, synthesis)
            self._log("Done!", SUCCESS)
        else:
            self._log("\nSome models failed. Check your internet and try again.", ERROR)

        self.after(0, self._finish, all_ok)

    def _pull_model(self, model: str) -> bool:
        try:
            import ollama as _ollama
            for progress in _ollama.pull(model, stream=True):
                status = progress.get("status", "")
                if status:
                    self._log(f"  {status}")
            return True
        except Exception as e:
            self._log(f"  Error: {e}", ERROR)
            return False

    def _write_configs(self, experts: list[dict], synthesis_model: str) -> None:
        ensure_dirs()
        # Remove old configs
        for f in EXPERTS_DIR.glob("*.yaml"):
            f.unlink()

        for i, e in enumerate(experts):
            cfg = {
                "specialty":        e["specialty"],
                "model":            e["model"],
                "http_port":        e["http_port"],
                "dht_port":         e["dht_port"],
                "is_bootstrap":     e.get("is_bootstrap", False),
                "description":      e["domain_desc"],
                "synthesis_model":  synthesis_model,
                "system_prompt":    e["system_prompt"],
            }
            out = EXPERTS_DIR / f"{e['specialty']}.yaml"
            with open(out, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        FIRST_RUN_SENTINEL.touch()
        self._log(f"  Config written to {APP_DIR}", FG)

    def _finish(self, success: bool) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate", value=100 if success else 0)
        self.wizard.set_next_enabled(success)


# ── Page 4: Done ──────────────────────────────────────────────────────────────

class DonePage(BasePage):
    def on_show(self) -> None:
        for w in self.winfo_children():
            w.destroy()

        self._title("All Set!")
        self._subtitle(
            "Your MoE network is configured and ready.\n"
            "Click Finish to launch -- the icon will appear in your system tray."
        )

        tk.Label(self, text="🎉", bg=BG, font=("Helvetica", 60)).pack(pady=20)

        info = (
            f"Experts configured: {len(list(EXPERTS_DIR.glob('*.yaml')))}\n"
            f"Config location: {APP_DIR}\n\n"
            "Right-click the tray icon to open a chat, manage the network,\n"
            "or enable the remote relay feature."
        )
        tk.Label(self, text=info, bg=BG, fg="#a6adc8",
                 font=("Helvetica", 11), justify="center").pack()


# ── Scrollable frame helper ───────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, bg=BG)
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = tk.Frame(canvas, bg=BG)

        self.inner.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))


# ── Entry point ───────────────────────────────────────────────────────────────

def run_if_needed() -> bool:
    """
    Run the wizard if this is the first time the app has been opened.
    Returns True if the wizard ran (caller should start tray after).
    Returns False if setup is already done (caller can start tray directly).
    """
    if FIRST_RUN_SENTINEL.exists() and any(EXPERTS_DIR.glob("*.yaml")):
        return False

    app = SetupWizard()
    app.mainloop()
    return True


if __name__ == "__main__":
    run_if_needed()
