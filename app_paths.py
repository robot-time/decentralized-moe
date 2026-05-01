"""
app_paths.py -- Centralized path management (no admin permissions needed)
=========================================================================
All user-writable files go under the user's home directory:
  Windows : %APPDATA%\\MoE-Network\\
  macOS   : ~/Library/Application Support/MoE-Network/
  Linux   : ~/.config/moe-network/

Bundled read-only defaults (expert YAML templates, icons) come from
BUNDLE_DIR, which is the PyInstaller _MEIPASS directory when frozen,
or the project directory when running from source.

Import this module anywhere you need a file path -- never hardcode paths.
"""

import sys
from pathlib import Path


# ── Detect frozen (PyInstaller) vs source ────────────────────────────────────

def _bundle_dir() -> Path:
    """
    When PyInstaller bundles the app it extracts files to a temp folder
    pointed to by sys._MEIPASS. When running from source, use the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)          # type: ignore[attr-defined]
    return Path(__file__).parent


def _app_data_dir() -> Path:
    """
    User-writable config/data directory.
    Created automatically; requires no admin rights on any platform.
    """
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"

    d = base / "MoE-Network"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Exported paths ────────────────────────────────────────────────────────────

BUNDLE_DIR  = _bundle_dir()          # read-only bundled assets
APP_DIR     = _app_data_dir()        # user-writable config + state

EXPERTS_DIR = APP_DIR / "experts"    # user's expert YAML configs
LOG_DIR     = APP_DIR / "logs"
RELAY_CFG   = APP_DIR / "relay_config.yaml"
VERSION_FILE = APP_DIR / "version.txt"
FIRST_RUN_SENTINEL = APP_DIR / ".setup_done"   # exists = wizard has run

# Default expert templates bundled with the app
BUNDLED_EXPERTS_DIR = BUNDLE_DIR / "experts"


def ensure_dirs() -> None:
    """Create all required directories. Call once on startup."""
    for d in [EXPERTS_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def copy_default_experts() -> None:
    """
    Copy bundled expert YAML templates to the user's experts folder
    if they don't already have any configured.
    """
    if not BUNDLED_EXPERTS_DIR.exists():
        return
    if any(EXPERTS_DIR.glob("*.yaml")):
        return  # user already has configs

    import shutil
    for src in BUNDLED_EXPERTS_DIR.glob("*.yaml"):
        dst = EXPERTS_DIR / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
