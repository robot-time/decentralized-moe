"""
config.py -- User configuration for the MoE Network app
========================================================
Single source of truth for: which role this device plays (stem / hass /
user-only), where the bootstrap node lives, and what specialist YAMLs are
available.  Uses platform-native data directories so it survives reinstalls.

  Windows : %APPDATA%\\MoE-Network\\config.yaml
  macOS   : ~/Library/Application Support/MoE-Network/config.yaml
  Linux   : ~/.config/moe-network/config.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

# ── Locations ────────────────────────────────────────────────────────────────

def _app_dir() -> Path:
    """Platform-native user-writable directory."""
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    d = base / "MoE-Network"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bundle_dir() -> Path:
    """Where bundled assets (default expert YAMLs) live."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent


APP_DIR     = _app_dir()
EXPERTS_DIR = APP_DIR / "experts"
LOG_DIR     = APP_DIR / "logs"
CONFIG_FILE = APP_DIR / "config.yaml"
BUNDLE_DIR  = _bundle_dir()

DEFAULTS: dict[str, Any] = {
    # role: "stem" | "hass" | "user"
    # "user" means we don't host a specialist; we just chat with the network.
    "role": "user",

    # Where to find at least one other node to bootstrap into the network.
    # host:port of any running specialist (its DHT port).
    # An empty string means "no remote bootstrap, LAN-only".
    "bootstrap": "",

    # Local DHT listen port for this node
    "dht_port": 8468,

    # Local HTTP port the specialist serves /query on (only used if role != user)
    "http_port": 8001,

    # Synthesis model used when multiple specialists return non-mismatch
    "aggregator_model": "llama3.1:8b",

    # Has the first-run setup wizard been completed?
    "wizard_done": False,
}


# ── I/O ──────────────────────────────────────────────────────────────────────

def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    EXPERTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_FILE.exists():
        save(DEFAULTS)
        return dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            data = yaml.safe_load(f) or {}
        return {**DEFAULTS, **data}
    except Exception:
        return dict(DEFAULTS)


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def copy_default_experts() -> None:
    """Copy bundled experts/*.yaml to the user's experts dir on first launch."""
    ensure_dirs()
    src_dir = BUNDLE_DIR / "experts"
    if not src_dir.exists():
        return
    for src in src_dir.glob("*.yaml"):
        dst = EXPERTS_DIR / src.name
        if not dst.exists():
            try:
                dst.write_text(src.read_text())
            except Exception:
                pass


def load_expert(specialty: str) -> dict[str, Any] | None:
    """Load the YAML for a specialty (stem / hass / ...). None if missing."""
    path = EXPERTS_DIR / f"{specialty}.yaml"
    if not path.exists():
        path = BUNDLE_DIR / "experts" / f"{specialty}.yaml"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None
