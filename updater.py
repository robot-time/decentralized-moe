"""
updater.py -- Check for and apply updates from GitHub Releases
==============================================================
Queries the GitHub Releases API, downloads the latest build for the
current platform, and swaps it in place of the running executable.

Auto-update flow:
  1. check_latest() queries the GitHub API
  2. is_newer() compares semver-ish tags
  3. apply_update() downloads the asset, writes a platform-specific
     updater script, launches it, and returns so the main app can exit
  4. The updater script waits for the main process to die, replaces
     the files, and relaunches the app
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

GITHUB_API = "https://api.github.com/repos/robot-time/decentralized-moe/releases/latest"
REPO_URL   = "https://github.com/robot-time/decentralized-moe/releases/latest"


def _current_version() -> str:
    """Read version from version.txt (bundled or dev)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    vf = base / "version.txt"
    if vf.exists():
        return vf.read_text().strip()
    return "0.0.0"


def _parse_version(v: str) -> tuple[int, ...]:
    """Semver-ish: '1.5.2' -> (1, 5, 2)."""
    parts = []
    for p in v.lstrip("v").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(current: str, latest: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _asset_name() -> str:
    if sys.platform == "win32":
        return "MoE-Network-Windows.zip"
    elif sys.platform == "darwin":
        return "MoE-Network-macOS.zip"
    return ""


def _app_path() -> Path:
    """Path to the running app bundle / exe."""
    exe = Path(sys.executable)
    if sys.platform == "darwin":
        # /path/MoE-Network.app/Contents/MacOS/MoE-Network
        parts = exe.parts
        try:
            idx = parts.index("Contents")
            return Path(*parts[:idx])
        except ValueError:
            pass
    return exe


async def check_latest() -> dict:
    """Query GitHub API for latest release info."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GITHUB_API,
                headers={"Accept": "application/vnd.github+json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return {"error": f"HTTP {resp.status}"}
                data = await resp.json()
                return {
                    "version": data.get("tag_name", "").lstrip("v"),
                    "url": data.get("html_url", REPO_URL),
                    "assets": {
                        a["name"]: a["browser_download_url"]
                        for a in data.get("assets", [])
                    },
                    "published": data.get("published_at", ""),
                }
    except Exception as exc:
        return {"error": str(exc)}


async def download_update(dest: Path, url: str) -> bool:
    """Download release asset to dest."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=None) as resp:
                if resp.status != 200:
                    return False
                with open(dest, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                return True
    except Exception:
        return False


def _write_updater_script(staged: Path, target: Path) -> Path:
    """Write a platform-specific script that replaces the app and relaunches."""
    if sys.platform == "win32":
        bat = staged.parent / "moe_updater.bat"
        bat.write_text(
            f"@echo off\n"
            f"timeout /t 2 /nobreak >nul\n"
            f"move /Y \"{staged}\" \"{target}\"\n"
            f"start \"\" \"{target}\"\n"
            f'del "%~f0"\n',
            encoding="utf-8",
        )
        return bat
    else:
        sh = staged.parent / "moe_updater.sh"
        sh.write_text(
            f"#!/bin/bash\n"
            f"sleep 2\n"
            f'rm -rf "{target}"\n'
            f'mv "{staged}" "{target}"\n'
            f'open "{target}"\n'
            f'rm "$0"\n',
            encoding="utf-8",
        )
        os.chmod(sh, 0o755)
        return sh


async def apply_update(asset_url: str) -> bool:
    """Download, stage, and spawn updater for the new release."""
    asset = _asset_name()
    if not asset:
        return False

    app_path = _app_path()
    temp_dir = Path(tempfile.gettempdir()) / "moe-update"
    temp_dir.mkdir(exist_ok=True)

    # macOS: download zip, extract, swap .app bundle
    if sys.platform == "darwin":
        zip_path = temp_dir / asset
        if not await download_update(zip_path, asset_url):
            return False
        extracted = temp_dir / "MoE-Network.app"
        if extracted.exists():
            shutil.rmtree(extracted)
        try:
            subprocess.run(
                ["unzip", "-q", str(zip_path), "-d", str(temp_dir)],
                check=True,
            )
        except Exception:
            return False
        staged = temp_dir / "MoE-Network.app"
        if not staged.exists():
            candidates = list(temp_dir.glob("*/MoE-Network.app"))
            if candidates:
                staged = candidates[0]
            else:
                return False
        updater = _write_updater_script(staged, app_path)
    else:
        # Windows: download exe directly
        exe_path = temp_dir / "MoE-Network.exe"
        if not await download_update(exe_path, asset_url):
            return False
        updater = _write_updater_script(exe_path, app_path)

    try:
        if sys.platform == "win32":
            subprocess.Popen([str(updater)], shell=True)
        else:
            subprocess.Popen([str(updater)])
        return True
    except Exception:
        return False
