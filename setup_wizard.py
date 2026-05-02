"""
setup_wizard.py -- First-Run Setup Wizard
==========================================
Runs once on first launch. Modern black and white UI.
After finishing, writes a sentinel file so it never opens again.
The single-instance lock in main.py prevents it stacking up.

Steps:
  1. Welcome + Ollama check
  2. Pick experts and models
  3. Download models (live log)
  4. Done -- tray app launches automatically
"""

import shutil
import sys
import threading
import webbrowser
from tkinter import font as tkfont
from tkinter import messagebox, ttk
import tkinter as tk

import yaml

from app_paths import APP_DIR, EXPERTS_DIR, FIRST_RUN_SENTINEL, ensure_dirs

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#0d0d0d"   # near-black background
SURFACE   = "#1a1a1a"   # card / panel
BORDER    = "#2e2e2e"   # subtle border
FG        = "#ffffff"   # primary text
FG_DIM    = "#888888"   # secondary text
BTN_FG    = "#000000"   # text on white button
BTN_BG    = "#ffffff"   # primary button
BTN_SEC   = "#1a1a1a"   # secondary button
GOOD      = "#22c55e"   # success green
BAD       = "#ef4444"   # error red

FONT_H1   = ("Helvetica", 22, "bold")
FONT_H2   = ("Helvetica", 14, "bold")
FONT_BODY = ("Helvetica", 12)
FONT_SMALL= ("Helvetica", 10)
FONT_MONO = ("Courier", 10)

# ── Expert catalogue ──────────────────────────────────────────────────────────

EXPERTS = [
    {
        "specialty": "math",
        "label":     "Math",
        "desc":      "Algebra, calculus, statistics, proofs",
        "http_port": 8001,
        "dht_port":  8468,
        "bootstrap": True,
        "models":    ["qwen2.5-math:7b", "mathstral:7b", "deepseek-r1:7b", "llama3:8b"],
        "domain":    "mathematics algebra calculus equations geometry statistics probability proofs",
        "prompt":    "You are a mathematics expert. Show step-by-step working. Be rigorous.",
    },
    {
        "specialty": "english",
        "label":     "English / Writing",
        "desc":      "Grammar, writing, literature, editing",
        "http_port": 8002,
        "dht_port":  8469,
        "bootstrap": False,
        "models":    ["mistral:7b", "llama3:8b", "gemma2:9b", "phi3:medium"],
        "domain":    "english writing grammar language literature essays poetry prose vocabulary",
        "prompt":    "You are an English language expert. Write with clarity and elegance.",
    },
    {
        "specialty": "code",
        "label":     "Coding",
        "desc":      "Programming, debugging, algorithms",
        "http_port": 8003,
        "dht_port":  8471,
        "bootstrap": False,
        "models":    ["qwen2.5-coder:7b", "deepseek-coder:6.7b", "codellama:7b"],
        "domain":    "coding programming software python javascript debugging algorithms",
        "prompt":    "You are a software engineering expert. Always include working code examples.",
    },
    {
        "specialty": "science",
        "label":     "Science",
        "desc":      "Physics, chemistry, biology",
        "http_port": 8004,
        "dht_port":  8472,
        "bootstrap": False,
        "models":    ["llama3:8b", "gemma2:9b", "mistral:7b"],
        "domain":    "science physics chemistry biology research experiments",
        "prompt":    "You are a natural sciences expert. Ground answers in empirical evidence.",
    },
]

SYNTH_MODELS = ["llama3:8b", "mistral:7b", "gemma2:9b"]


# ── Reusable widgets ──────────────────────────────────────────────────────────

def _label(parent, text, font=FONT_BODY, color=FG, **kw):
    # Use setdefault so callers can override bg without colliding with the
    # default. macOS Aqua tk raises TypeError on duplicate kwargs; Windows
    # silently last-wins, which is why this bug only shows on Mac.
    kw.setdefault("bg", parent["bg"])
    return tk.Label(parent, text=text, font=font, fg=color, **kw)

def _divider(parent):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=32, pady=12)

def _btn(parent, text, command, primary=True, **kw):
    bg = BTN_BG if primary else BTN_SEC
    fg = BTN_FG if primary else FG
    b = tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
        relief="flat", font=("Helvetica", 11, "bold" if primary else "normal"),
        padx=20, pady=8, cursor="hand2", **kw
    )
    # Hover effect
    b.bind("<Enter>", lambda e: b.config(bg="#e5e5e5" if primary else "#2a2a2a"))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b


# ── Wizard window ─────────────────────────────────────────────────────────────

class SetupWizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MoE Network Setup")
        self.geometry("620x540")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Shared state
        self.ollama_ok   = False
        self.expert_vars = {}   # specialty -> {enabled: BooleanVar, model: StringVar}
        self.synth_var   = tk.StringVar(value=SYNTH_MODELS[0])
        self.setup_done  = False

        # Build ttk style
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE,
                    foreground=FG, bordercolor=BORDER, arrowcolor=FG)
        s.configure("TCheckbutton", background=BG, foreground=FG)
        s.configure("Horizontal.TProgressbar", troughcolor=SURFACE,
                    background=FG, bordercolor=BORDER)

        # Nav bar
        self._nav = tk.Frame(self, bg=SURFACE, height=60)
        self._nav.pack(side="bottom", fill="x")
        self._nav.pack_propagate(False)

        self._back_btn = _btn(self._nav, "← Back", self._go_back, primary=False)
        self._back_btn.pack(side="left", padx=20, pady=12)

        self._next_btn = _btn(self._nav, "Continue →", self._go_next, primary=True)
        self._next_btn.pack(side="right", padx=20, pady=12)

        # Page container
        self._container = tk.Frame(self, bg=BG)
        self._container.pack(fill="both", expand=True)

        self._pages = [
            WelcomePage(self._container, self),
            ExpertsPage(self._container, self),
            DownloadPage(self._container, self),
            DonePage(self._container, self),
        ]
        for p in self._pages:
            p.place(relwidth=1, relheight=1)

        self._step = 0
        self._show(0)

    def _show(self, i):
        self._step = i
        self._pages[i].lift()
        self._pages[i].on_show()
        self._back_btn.config(state="normal" if i > 0 else "disabled")
        last = (i == len(self._pages) - 1)
        self._next_btn.config(text="Finish" if last else "Continue →")

    def _go_next(self):
        if not self._pages[self._step].validate():
            return
        if self._step == len(self._pages) - 1:
            self._finish()
        else:
            self._show(self._step + 1)

    def _go_back(self):
        if self._step > 0:
            self._show(self._step - 1)

    def set_next(self, enabled: bool, label: str = None):
        self._next_btn.config(state="normal" if enabled else "disabled")
        if label:
            self._next_btn.config(text=label)

    def get_experts(self):
        out = []
        for e in EXPERTS:
            s = e["specialty"]
            if self.expert_vars.get(s, {}).get("enabled", tk.BooleanVar()).get():
                out.append({**e, "model": self.expert_vars[s]["model"].get()})
        return out

    def _finish(self):
        self.setup_done = True
        FIRST_RUN_SENTINEL.touch()
        self.destroy()

    def _on_close(self):
        # Write sentinel even if user closes early so wizard doesn't loop
        FIRST_RUN_SENTINEL.touch()
        self.destroy()


# ── Base page ─────────────────────────────────────────────────────────────────

class Page(tk.Frame):
    def __init__(self, parent, wizard: SetupWizard):
        super().__init__(parent, bg=BG)
        self.wz = wizard

    def on_show(self): pass
    def validate(self) -> bool: return True

    def _header(self, title, subtitle=None):
        tk.Frame(self, bg=BG, height=28).pack()
        _label(self, title, FONT_H1).pack(anchor="w", padx=32)
        if subtitle:
            _label(self, subtitle, FONT_BODY, FG_DIM, wraplength=540, justify="left").pack(
                anchor="w", padx=32, pady=(4, 0))


# ── Page 1: Welcome ───────────────────────────────────────────────────────────

class WelcomePage(Page):
    def on_show(self):
        for w in self.winfo_children(): w.destroy()

        self._header(
            "Set up MoE Network",
            "A local AI network that runs entirely on your machine.\nLet's get you going in about 5 minutes."
        )
        _divider(self)

        # Ollama status card
        card = tk.Frame(self, bg=SURFACE, padx=24, pady=20)
        card.pack(fill="x", padx=32)

        top = tk.Frame(card, bg=SURFACE)
        top.pack(fill="x")
        _label(top, "Ollama", FONT_H2, bg=SURFACE).pack(side="left")

        self._status = _label(top, "Checking...", FONT_BODY, FG_DIM)
        self._status.pack(side="right")
        self._status.master.configure(bg=SURFACE)

        _label(card, "Ollama runs local AI models. Free, no account needed.",
               FONT_SMALL, FG_DIM).pack(anchor="w", pady=(6, 0))

        self._download_btn = _btn(card, "Download Ollama  ↗",
                                  lambda: webbrowser.open("https://ollama.com/download"),
                                  primary=False)
        self._retry_btn = _btn(card, "Check again",
                               self.on_show, primary=False)

        self.wz.set_next(False)
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        found = shutil.which("ollama") is not None
        self.after(0, self._update, found)

    def _update(self, found):
        self.wz.ollama_ok = found
        if found:
            self._status.config(text="Installed ✓", fg=GOOD)
            self._download_btn.pack_forget()
            self._retry_btn.pack_forget()
            self.wz.set_next(True)
        else:
            self._status.config(text="Not found", fg=BAD)
            self._download_btn.pack(anchor="w", pady=(12, 4))
            self._retry_btn.pack(anchor="w")
            self.wz.set_next(False)

    def validate(self):
        return self.wz.ollama_ok


# ── Page 2: Expert selection ──────────────────────────────────────────────────

class ExpertsPage(Page):
    def on_show(self):
        for w in self.winfo_children(): w.destroy()

        self._header(
            "Choose your experts",
            "Each expert runs a different AI model with its own specialty."
        )
        _divider(self)

        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=32)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        for e in EXPERTS:
            s = e["specialty"]
            if s not in self.wz.expert_vars:
                self.wz.expert_vars[s] = {
                    "enabled": tk.BooleanVar(value=True),
                    "model":   tk.StringVar(value=e["models"][0]),
                }
            self._card(inner, e)

        # Synthesis model row
        row = tk.Frame(inner, bg=BG)
        row.pack(fill="x", pady=(12, 4))
        _label(row, "Synthesis model", FONT_SMALL, FG_DIM).pack(side="left")
        ttk.Combobox(row, textvariable=self.wz.synth_var,
                     values=SYNTH_MODELS, state="readonly", width=22,
                     ).pack(side="right")

    def _card(self, parent, e):
        s = e["specialty"]
        v = self.wz.expert_vars[s]

        card = tk.Frame(parent, bg=SURFACE, padx=16, pady=12)
        card.pack(fill="x", pady=4)

        row = tk.Frame(card, bg=SURFACE)
        row.pack(fill="x")

        tk.Checkbutton(
            row, text=e["label"], variable=v["enabled"],
            bg=SURFACE, fg=FG, selectcolor="#333333",
            activebackground=SURFACE, activeforeground=FG,
            font=FONT_H2, cursor="hand2",
        ).pack(side="left")

        ttk.Combobox(row, textvariable=v["model"],
                     values=e["models"], state="readonly", width=24,
                     ).pack(side="right")

        _label(card, e["desc"], FONT_SMALL, FG_DIM).pack(anchor="w", pady=(2, 0))

    def validate(self):
        if not self.wz.get_experts():
            messagebox.showwarning("No experts selected",
                                   "Enable at least one expert to continue.")
            return False
        return True


# ── Page 3: Download ──────────────────────────────────────────────────────────

class DownloadPage(Page):
    def __init__(self, parent, wizard):
        super().__init__(parent, wizard)
        self._started = False

    def on_show(self):
        if self._started:
            return
        self._started = True
        for w in self.winfo_children(): w.destroy()

        self._header(
            "Downloading models",
            "Already-downloaded models are instant. New ones may take a few minutes."
        )
        _divider(self)

        self._log = tk.Text(self, bg=SURFACE, fg=FG, font=FONT_MONO,
                            relief="flat", state="disabled", wrap="word",
                            padx=12, pady=8)
        self._log.pack(fill="both", expand=True, padx=32, pady=(0, 8))

        self._bar = ttk.Progressbar(self, mode="indeterminate",
                                    style="Horizontal.TProgressbar")
        self._bar.pack(fill="x", padx=32, pady=(0, 4))
        self._bar.start(12)

        self.wz.set_next(False)
        threading.Thread(target=self._run, daemon=True).start()

    def _log_line(self, text, color=FG):
        def _do():
            self._log.config(state="normal")
            self._log.insert("end", text + "\n")
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _run(self):
        experts  = self.wz.get_experts()
        synth    = self.wz.synth_var.get()

        seen     = set()
        to_pull  = []
        for e in experts:
            if e["model"] not in seen:
                to_pull.append((e["label"], e["model"]))
                seen.add(e["model"])
        if synth not in seen:
            to_pull.append(("Synthesis", synth))

        all_ok = True
        for label, model in to_pull:
            self._log_line(f"\n[{label}]  pulling {model} ...")
            ok = self._pull(model)
            self._log_line(f"  {'✓ done' if ok else '✗ failed'}", GOOD if ok else BAD)
            if not ok:
                all_ok = False

        if all_ok:
            self._log_line("\nWriting config...", FG_DIM)
            self._write(experts, synth)
            self._log_line("All done.", GOOD)
        else:
            self._log_line("\nSome models failed. Check your connection.", BAD)

        self.after(0, self._done, all_ok)

    def _pull(self, model) -> bool:
        try:
            import ollama as _ol
            for p in _ol.pull(model, stream=True):
                s = p.get("status", "")
                if s:
                    self._log_line(f"  {s}")
            return True
        except Exception as ex:
            self._log_line(f"  Error: {ex}", BAD)
            return False

    def _write(self, experts, synth):
        ensure_dirs()
        for f in EXPERTS_DIR.glob("*.yaml"):
            f.unlink()
        for e in experts:
            cfg = {
                "specialty":       e["specialty"],
                "model":           e["model"],
                "http_port":       e["http_port"],
                "dht_port":        e["dht_port"],
                "is_bootstrap":    e["bootstrap"],
                "description":     e["domain"],
                "synthesis_model": synth,
                "system_prompt":   e["prompt"],
            }
            with open(EXPERTS_DIR / f"{e['specialty']}.yaml", "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

    def _done(self, ok):
        self._bar.stop()
        self._bar.config(mode="determinate", value=100 if ok else 0)
        self.wz.set_next(ok, "Continue →")


# ── Page 4: Done ─────────────────────────────────────────────────────────────

class DonePage(Page):
    def on_show(self):
        for w in self.winfo_children(): w.destroy()

        self._header("You're all set")
        _divider(self)

        tk.Frame(self, bg=BG, height=12).pack()

        _label(self, "The app is starting in your system tray.", FONT_H2).pack()
        tk.Frame(self, bg=BG, height=8).pack()

        if sys.platform == "win32":
            tip = "Look for the icon in the bottom-right corner of your screen.\nClick the  ∧  arrow if you don't see it right away."
        elif sys.platform == "darwin":
            tip = "Look for the icon in the menu bar at the top-right of your screen."
        else:
            tip = "Look for the icon in your system tray."

        _label(self, tip, FONT_BODY, FG_DIM, wraplength=480, justify="center").pack(pady=8)

        tk.Frame(self, bg=BG, height=24).pack()

        _label(self,
               "Right-click the tray icon to open a chat,\n"
               "manage experts, or enable the remote relay.",
               FONT_BODY, FG_DIM, justify="center").pack()


# ── Public entry point ────────────────────────────────────────────────────────

def run_if_needed() -> bool:
    """
    Show the wizard if this is the first run.
    Returns True if wizard ran, False if already set up.
    """
    if FIRST_RUN_SENTINEL.exists():
        return False

    wz = SetupWizard()

    # Force window to the foreground (important on Windows where new windows
    # can appear behind the taskbar or existing windows).
    wz.lift()
    wz.focus_force()
    if sys.platform == "win32":
        wz.attributes("-topmost", True)
        wz.after(500, lambda: wz.attributes("-topmost", False))

    wz.mainloop()
    return True


if __name__ == "__main__":
    run_if_needed()
