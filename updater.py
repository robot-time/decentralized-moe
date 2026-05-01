"""
updater.py -- Automatic Update System for the Decentralized MoE Network
========================================================================
Checks a GitHub releases page for newer versions, downloads the release
zip, and applies it in-place -- preserving your experts/ folder so any
custom nodes you've added survive the update.

To host updates for your friends:
  1. Put this project in a GitHub repo
  2. Set GITHUB_REPO below to "yourusername/decentralized-moe"
  3. Tag releases as v1.0.0, v1.1.0, etc. and attach a zip of the project
  4. Everyone running tray.py will get notified and can update in one click

Also provides launch-at-login helpers used by tray.py.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import requests

# ── Config ────────────────────────────────────────────────────────────────────

# Change this to your GitHub repo after pushing the project there.
# Format: "username/repo-name"
GITHUB_REPO = "robot-time/decentralized-moe"

BASE_DIR    = Path(__file__).parent
VERSION_FILE = BASE_DIR / "version.txt"

# Files/folders to preserve during an update (user customizations)
PRESERVE = {"experts/"}


# ── Version helpers ───────────────────────────────────────────────────────────


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse "1.2.3" into (1, 2, 3) for comparison."""
    v = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


# ── Updater ───────────────────────────────────────────────────────────────────


class Updater:
    """
    Checks GitHub Releases for newer versions and applies updates.

    How it works:
      1. check() hits the GitHub API to get the latest release tag + zip URL
      2. is_newer() compares the remote tag to version.txt
      3. apply() downloads the zip, extracts it to a temp folder, then
         copies new files over -- skipping anything in PRESERVE so your
         custom expert YAMLs are never touched
    """

    def __init__(self) -> None:
        self.current_version: str = self._read_version()

    def _read_version(self) -> str:
        try:
            return VERSION_FILE.read_text().strip()
        except FileNotFoundError:
            return "0.0.0"

    def check(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Query the GitHub releases API.
        Returns (latest_version_string, download_url) or (None, None) on failure.
        """
        if GITHUB_REPO == "your-username/decentralized-moe":
            # Repo not configured yet -- skip silently
            return None, None

        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            resp = requests.get(url, timeout=10, headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            data = resp.json()

            tag          = data.get("tag_name", "")
            latest       = tag.lstrip("v").strip()

            # Find the zip asset in the release
            download_url = None
            for asset in data.get("assets", []):
                if asset.get("name", "").endswith(".zip"):
                    download_url = asset["browser_download_url"]
                    break

            # Fall back to the auto-generated source zip
            if not download_url:
                download_url = data.get("zipball_url")

            return latest, download_url

        except Exception as exc:
            print(f"[updater] Update check failed: {exc}")
            return None, None

    def is_newer(self, remote_version: str) -> bool:
        """True if remote_version is newer than the locally installed version."""
        return _parse_version(remote_version) > _parse_version(self.current_version)

    def apply(self, download_url: str) -> bool:
        """
        Download the release zip and apply it.

        Strategy:
          - Download to a temp file
          - Extract to a temp directory
          - Find the project root inside the zip (handles GitHub's extra nesting)
          - Copy all files EXCEPT those in PRESERVE to BASE_DIR
          - Update version.txt

        Returns True on success, False on failure.
        """
        print(f"[updater] Downloading update from {download_url}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)

                # ── Download ──────────────────────────────────────────────
                zip_path = tmp_path / "update.zip"
                with requests.get(download_url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(zip_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)

                # ── Extract ───────────────────────────────────────────────
                extract_dir = tmp_path / "extracted"
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_dir)

                # GitHub zips have a top-level folder like "username-repo-abc123/"
                # Find the actual project root (where node.py lives)
                project_root = extract_dir
                for candidate in extract_dir.iterdir():
                    if candidate.is_dir() and (candidate / "node.py").exists():
                        project_root = candidate
                        break

                if not (project_root / "node.py").exists():
                    print("[updater] Could not find project root in zip.")
                    return False

                # ── Copy new files, preserving user customizations ────────
                for item in project_root.rglob("*"):
                    if not item.is_file():
                        continue

                    rel = item.relative_to(project_root)
                    rel_str = str(rel)

                    # Skip anything inside a preserved folder
                    if any(rel_str.startswith(p) for p in PRESERVE):
                        print(f"[updater] Preserving {rel_str}")
                        continue

                    dest = BASE_DIR / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest)
                    print(f"[updater] Updated {rel_str}")

                print("[updater] Update applied successfully.")
                return True

        except Exception as exc:
            print(f"[updater] Update failed: {exc}")
            return False


# ── Launch at login ───────────────────────────────────────────────────────────

_TRAY_SCRIPT = str(BASE_DIR / "tray.py")
_APP_NAME    = "MoE Network"
_APP_ID      = "com.moe-network.tray"


def get_launch_at_login() -> bool:
    """Return True if launch-at-login is currently enabled."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ,
            )
            winreg.QueryValueEx(key, _APP_NAME)
            return True
        except (ImportError, OSError):
            return False

    elif sys.platform == "darwin":
        plist = Path.home() / f"Library/LaunchAgents/{_APP_ID}.plist"
        return plist.exists()

    else:
        desktop = Path.home() / f".config/autostart/{_APP_ID}.desktop"
        return desktop.exists()


def set_launch_at_login(enabled: bool) -> None:
    """Enable or disable launch at login, cross-platform."""
    cmd = f'"{sys.executable}" "{_TRAY_SCRIPT}"'

    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            if enabled:
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, _APP_NAME)
                except FileNotFoundError:
                    pass
        except ImportError:
            print("[updater] winreg not available -- launch-at-login not supported")

    elif sys.platform == "darwin":
        plist = Path.home() / f"Library/LaunchAgents/{_APP_ID}.plist"
        if enabled:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_APP_ID}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{_TRAY_SCRIPT}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
""")
            subprocess.run(["launchctl", "load", str(plist)], check=False)
        else:
            if plist.exists():
                subprocess.run(["launchctl", "unload", str(plist)], check=False)
                plist.unlink()

    else:  # Linux (XDG autostart)
        desktop = Path.home() / f".config/autostart/{_APP_ID}.desktop"
        if enabled:
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_text(f"""[Desktop Entry]
Type=Application
Name={_APP_NAME}
Exec={sys.executable} {_TRAY_SCRIPT}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
""")
        else:
            if desktop.exists():
                desktop.unlink()
