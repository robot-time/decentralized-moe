"""
tray.py -- System Tray App for the Decentralized MoE Network
=============================================================
Sits in your system tray (bottom-right on Windows, menu bar on macOS,
taskbar on Linux). Click to open a chat terminal or manage the network.

Features:
  - Start / stop the expert node network from the tray
  - Open a chat terminal connected to any live node
  - Automatic update checks (every 6 hours + on startup)
  - Launch at login toggle
  - Live node status in the menu
  - Relay polling: receive queries from your phone / remote machines
    (configure relay_config.yaml -- your machine does all the AI work)
  - Keep-awake toggle: prevents the system from sleeping while the
    relay is connected, so the outbound polling connection stays alive
    (screen can still turn off; only system sleep is blocked)

Usage:
    python tray.py          # start the tray app (nodes start automatically)
    python tray.py --no-autostart   # start tray but don't start nodes yet

The tray icon shows:
  Green  = network running
  Grey   = network stopped
  Red dot in corner = update available
  Blue ring = relay connected and polling
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import aiohttp
import pystray
import yaml
from PIL import Image, ImageDraw

from keepawake import KeepAwake
from updater import Updater, get_launch_at_login, set_launch_at_login

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent

# ── Icon drawing ──────────────────────────────────────────────────────────────


def make_icon(
    node_colors: list[tuple] = None,
    update_available: bool = False,
    running: bool = True,
) -> Image.Image:
    """
    Draw the tray icon: a central hub connected to colored expert nodes.
    Each colored dot represents one expert in the network.
    A small red badge appears if an update is available.
    """
    SIZE = 64
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)

    cx, cy = SIZE // 2, SIZE // 2

    # Background circle
    bg_color = (30, 41, 59, 220) if running else (71, 85, 105, 180)
    d.ellipse([2, 2, SIZE - 2, SIZE - 2], fill=bg_color)

    # Default node colors (math, english, code, science)
    if node_colors is None:
        node_colors = [
            (59, 130, 246),   # blue   - math
            (34, 197, 94),    # green  - english
            (249, 115, 22),   # orange - code
            (168, 85, 247),   # purple - science
        ]

    # Positions: top, right, bottom, left (diamond pattern)
    radius = 18
    positions = [
        (cx,          cy - radius),  # top
        (cx + radius, cy),           # right
        (cx,          cy + radius),  # bottom
        (cx - radius, cy),           # left
    ]

    # Draw connection lines from center to each node
    line_color = (255, 255, 255, 80 if running else 40)
    for pos in positions[:len(node_colors)]:
        d.line([cx, cy, pos[0], pos[1]], fill=line_color, width=2)

    # Draw expert nodes
    dot_r = 7
    for pos, color in zip(positions, node_colors):
        x, y = pos
        alpha = 255 if running else 120
        d.ellipse(
            [x - dot_r, y - dot_r, x + dot_r, y + dot_r],
            fill=(*color, alpha),
        )

    # Central hub dot
    hub_color = (255, 255, 255, 230 if running else 100)
    d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=hub_color)

    # Red badge if an update is available
    if update_available:
        d.ellipse([SIZE - 16, 2, SIZE - 2, 16], fill=(239, 68, 68, 255))
        d.ellipse([SIZE - 14, 4, SIZE - 4, 14], fill=(254, 202, 202, 255))

    return img


# ── Node manager ──────────────────────────────────────────────────────────────


class NodeManager:
    """
    Manages the lifecycle of expert node subprocesses.
    Reads experts/*.yaml to know what to start -- adding a YAML file
    and restarting the network is all you need to add a new expert.
    """

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []
        self.yaml_paths: list[Path] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self.processes) and any(
                p.poll() is None for p in self.processes
            )

    def alive_count(self) -> tuple[int, int]:
        """Returns (alive, total)."""
        with self._lock:
            total = len(self.processes)
            alive = sum(1 for p in self.processes if p.poll() is None)
        return alive, total

    def status_text(self) -> str:
        if not self.processes:
            return "Stopped"
        alive, total = self.alive_count()
        if alive == 0:
            return "Stopped"
        if alive == total:
            return f"Running ({alive} nodes)"
        return f"Partial ({alive}/{total} nodes up)"

    def start(self) -> None:
        with self._lock:
            if self.processes:
                return  # already running

            # Find all expert YAMLs; sort alphabetically so math.yaml (bootstrap) goes first
            yaml_paths = sorted(BASE_DIR.glob("experts/*.yaml")) + \
                         sorted(BASE_DIR.glob("experts/*.yml"))
            self.yaml_paths = yaml_paths

            for i, yaml_path in enumerate(yaml_paths):
                proc = subprocess.Popen(
                    [sys.executable, str(BASE_DIR / "node.py"), str(yaml_path)],
                    cwd=BASE_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.processes.append(proc)
                # Give the first node (bootstrap) time to open its DHT port
                if i == 0:
                    time.sleep(2)

    def stop(self) -> None:
        with self._lock:
            for proc in self.processes:
                proc.terminate()
            for proc in self.processes:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            self.processes.clear()

    def restart(self) -> None:
        self.stop()
        time.sleep(1)
        self.start()


# ── Terminal launcher (cross-platform) ────────────────────────────────────────


def open_ask_terminal(node_url: Optional[str] = None) -> None:
    """
    Open a terminal window running ask.py connected to the network.
    Works on Windows, macOS, and Linux.
    """
    ask_script = str(BASE_DIR / "ask.py")
    cmd_args = [sys.executable, ask_script]
    if node_url:
        cmd_args.append(node_url)

    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/k"] + cmd_args, creationflags=subprocess.CREATE_NEW_CONSOLE)

    elif sys.platform == "darwin":
        # Write a temp shell script and open it in Terminal.app
        tmp = BASE_DIR / "_open_terminal.sh"
        tmp.write_text(f"#!/bin/bash\n{' '.join(cmd_args)}\n")
        tmp.chmod(0o755)
        subprocess.Popen(["open", "-a", "Terminal", str(tmp)])

    else:
        # Try common Linux terminal emulators in order of preference
        for term, flag in [
            ("gnome-terminal", "--"),
            ("konsole", "-e"),
            ("xfce4-terminal", "-e"),
            ("xterm", "-e"),
        ]:
            if shutil.which(term):
                subprocess.Popen([term, flag] + cmd_args)
                return
        # Fallback: just run in background (no visible terminal)
        subprocess.Popen(cmd_args)


# ── Tray App ──────────────────────────────────────────────────────────────────


def _load_relay_config() -> dict:
    """Load relay_config.yaml, returning safe defaults if missing."""
    path = BASE_DIR / "relay_config.yaml"
    defaults = {
        "enabled": False,
        "relay_url": "",
        "api_key": "",
        "local_node_url": "http://localhost:8001",
        "keep_awake": True,
        "poll_timeout": 30,
    }
    if not path.exists():
        return defaults
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
        return {**defaults, **cfg}
    except Exception:
        return defaults


class TrayApp:
    """
    The system tray application. Manages the icon, menu, background
    update checker, keep-awake, and relay polling loop.

    Relay polling (mirrors Claude Dispatch architecture):
      - This app makes outbound HTTPS requests to the relay server
      - No inbound ports are opened on your machine
      - When a query arrives, it's forwarded to the local MoE node
      - The answer travels back through the same relay path
      - All AI processing happens locally; only text messages cross the relay
    """

    def __init__(self, autostart_nodes: bool = True) -> None:
        self.nodes = NodeManager()
        self.updater = Updater()
        self.keep_awake = KeepAwake()
        self.update_available: Optional[str] = None
        self._icon: Optional[pystray.Icon] = None
        self.autostart_nodes = autostart_nodes

        # Relay state
        self._relay_cfg = _load_relay_config()
        self._relay_connected = False   # True when actively polling
        self._relay_thread: Optional[threading.Thread] = None

    # ── Menu construction ─────────────────────────────────────────────────

    def _make_menu(self) -> pystray.Menu:
        """Build the right-click menu. Called each time the menu opens."""
        update_label = (
            f"Update to v{self.update_available} — Click to install"
            if self.update_available
            else f"Up to date (v{self.updater.current_version})"
        )

        relay_status = "Connected" if self._relay_connected else "Off"

        return pystray.Menu(
            # Status (non-clickable)
            pystray.MenuItem(
                f"Network: {self.nodes.status_text()}",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                f"Relay: {relay_status}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,

            # Chat
            pystray.MenuItem("Open Chat Terminal", self._on_open_terminal),

            pystray.Menu.SEPARATOR,

            # Network control
            pystray.MenuItem(
                "Start Network",
                self._on_start,
                enabled=not self.nodes.running,
            ),
            pystray.MenuItem(
                "Stop Network",
                self._on_stop,
                enabled=self.nodes.running,
            ),
            pystray.MenuItem("Restart Network", self._on_restart),

            pystray.Menu.SEPARATOR,

            # Settings
            pystray.MenuItem("Open Settings", self._on_open_settings),

            pystray.Menu.SEPARATOR,

            # Updates
            pystray.MenuItem(update_label, self._on_update_click),
            pystray.MenuItem("Check for Updates Now", self._on_check_updates),

            pystray.Menu.SEPARATOR,

            pystray.MenuItem("Quit", self._on_quit),
        )

    # ── Menu actions ──────────────────────────────────────────────────────

    def _on_open_terminal(self, icon=None, item=None) -> None:
        open_ask_terminal()

    def _on_start(self, icon=None, item=None) -> None:
        threading.Thread(target=self._start_and_refresh, daemon=True).start()

    def _start_and_refresh(self) -> None:
        self.nodes.start()
        time.sleep(3)
        self._refresh_icon()

    def _on_stop(self, icon=None, item=None) -> None:
        self.nodes.stop()
        self._refresh_icon()

    def _on_restart(self, icon=None, item=None) -> None:
        threading.Thread(target=self._restart_and_refresh, daemon=True).start()

    def _restart_and_refresh(self) -> None:
        self.nodes.restart()
        time.sleep(3)
        self._refresh_icon()

    def _on_check_updates(self, icon=None, item=None) -> None:
        threading.Thread(target=self._do_update_check, daemon=True).start()

    def _on_update_click(self, icon=None, item=None) -> None:
        if self.update_available:
            threading.Thread(target=self._do_apply_update, daemon=True).start()

    def _on_open_settings(self, icon=None, item=None) -> None:
        """Open the settings window in a separate process to avoid event-loop conflicts."""
        if getattr(sys, "frozen", False):
            # Frozen app: re-launch the same executable with --settings flag
            subprocess.Popen([sys.executable, "--settings"])
        else:
            # Source mode: run settings_ui.py directly
            subprocess.Popen([sys.executable, str(BASE_DIR / "settings_ui.py")])

    def _on_quit(self, icon=None, item=None) -> None:
        self._stop_relay()
        self.keep_awake.stop()
        self.nodes.stop()
        if self._icon:
            self._icon.stop()

    # ── Update logic ──────────────────────────────────────────────────────

    def _do_update_check(self) -> None:
        """Check GitHub for a newer version. Called in a background thread."""
        latest_version, download_url = self.updater.check()
        if latest_version and self.updater.is_newer(latest_version):
            self.update_available = latest_version
            self._refresh_icon(notify=f"Update v{latest_version} available -- click tray icon to install")
        else:
            self.update_available = None
            self._refresh_icon()

    def _do_apply_update(self) -> None:
        """Download and apply the update, then restart."""
        if not self.update_available:
            return
        _, download_url = self.updater.check()
        if not download_url:
            return

        self.nodes.stop()
        success = self.updater.apply(download_url)
        if success:
            # Re-exec tray.py with the new code
            os.execv(sys.executable, [sys.executable, str(BASE_DIR / "tray.py")])
        else:
            self.nodes.start()

    # ── Icon refresh ──────────────────────────────────────────────────────

    def _refresh_icon(self, notify: Optional[str] = None) -> None:
        """Update the tray icon image and menu to reflect current state."""
        if not self._icon:
            return
        self._icon.icon = make_icon(
            update_available=bool(self.update_available),
            running=self.nodes.running,
        )
        self._icon.menu = self._make_menu()
        if notify:
            try:
                self._icon.notify(notify, "MoE Network")
            except Exception:
                pass  # notifications not supported on all platforms

    # ── Relay polling ─────────────────────────────────────────────────────

    def _start_relay(self) -> None:
        """Start the background relay polling thread."""
        if self._relay_connected:
            return
        # Reload config from disk so settings changes take effect
        self._relay_cfg = _load_relay_config()
        cfg = self._relay_cfg
        if not cfg.get("relay_url") or not cfg.get("api_key"):
            self._refresh_icon(notify="Configure relay_config.yaml first")
            return

        self._relay_connected = True

        # Optionally keep the machine awake while relay is active
        if cfg.get("keep_awake", True):
            self.keep_awake.start()

        self._relay_thread = threading.Thread(
            target=self._relay_thread_main,
            daemon=True,
            name="relay-poll",
        )
        self._relay_thread.start()
        self._refresh_icon(notify="Relay connected -- remote queries enabled")

    def _stop_relay(self) -> None:
        self._relay_connected = False
        if self._relay_cfg.get("keep_awake", True):
            self.keep_awake.stop()
        self._refresh_icon()

    def _relay_thread_main(self) -> None:
        """Run the async relay loop in its own event loop (background thread)."""
        asyncio.run(self._relay_poll_loop())

    async def _relay_poll_loop(self) -> None:
        """
        Core relay loop. Mirrors how Claude Dispatch works:

          1. Make an outbound GET /poll to the relay server
          2. The server holds the connection open (long-poll, up to poll_timeout seconds)
          3. When a query arrives, the server responds with it immediately
          4. We send the query to our local MoE node for processing
          5. Post the result back to the relay so the remote client can fetch it
          6. Immediately start the next poll (step 1)

        No inbound ports are opened. All AI work happens locally.
        """
        cfg        = self._relay_cfg
        relay_url  = cfg["relay_url"].rstrip("/")
        api_key    = cfg["api_key"]
        node_url   = cfg.get("local_node_url", "http://localhost:8001")
        timeout    = int(cfg.get("poll_timeout", 30))
        headers    = {"X-API-Key": api_key}

        log.info(f"[relay] Polling {relay_url}/poll")

        async with aiohttp.ClientSession(headers=headers) as session:
            while self._relay_connected:
                try:
                    # Long-poll: server holds this open until a query arrives
                    async with session.get(
                        f"{relay_url}/poll",
                        timeout=aiohttp.ClientTimeout(total=timeout + 5),
                    ) as resp:

                        if resp.status == 204:
                            # No query arrived in the timeout window -- re-poll immediately
                            continue

                        if resp.status != 200:
                            log.warning(f"[relay] Unexpected poll status {resp.status}")
                            await asyncio.sleep(5)
                            continue

                        query_data = await resp.json()
                        qid  = query_data.get("id")
                        text = query_data.get("text", "")
                        log.info(f"[relay] Received query {qid[:8]}: {text[:60]}")

                        # Process the query locally through the MoE network
                        result = await self._process_via_local_node(session, node_url, text)

                        # Post result back to relay so remote client can fetch it
                        await session.post(
                            f"{relay_url}/result/{qid}",
                            json=result,
                        )
                        log.info(f"[relay] Posted result for {qid[:8]}")

                except asyncio.TimeoutError:
                    # Connection timed out -- re-poll
                    continue
                except aiohttp.ClientError as e:
                    log.warning(f"[relay] Connection error: {e}. Retrying in 10s...")
                    await asyncio.sleep(10)
                except Exception as e:
                    log.error(f"[relay] Unexpected error: {e}")
                    await asyncio.sleep(5)

        log.info("[relay] Polling stopped")

    async def _process_via_local_node(
        self,
        session: aiohttp.ClientSession,
        node_url: str,
        query: str,
    ) -> dict:
        """
        Forward the query to the local MoE node's /ask endpoint.
        The node orchestrates the full MoE pipeline and returns the answer.
        All processing is local -- nothing goes to external servers.
        """
        try:
            async with session.post(
                f"{node_url}/ask",
                json={"query": query},
                timeout=aiohttp.ClientTimeout(total=180),
                headers={},  # don't send relay API key to local node
            ) as resp:
                data = await resp.json()
                return {
                    "answer":  data.get("answer", "(no answer)"),
                    "experts": data.get("peers_queried", []),
                }
        except Exception as e:
            log.error(f"[relay] Local node error: {e}")
            return {"answer": f"Error reaching local MoE node: {e}", "experts": []}

    # ── Background threads ────────────────────────────────────────────────

    def _update_check_loop(self) -> None:
        """Check for updates on startup and then every 6 hours."""
        time.sleep(10)  # wait for everything to settle first
        while True:
            self._do_update_check()
            time.sleep(6 * 60 * 60)  # 6 hours

    def _status_refresh_loop(self) -> None:
        """Refresh the icon/menu every 30 seconds to show live node status."""
        while True:
            time.sleep(30)
            self._refresh_icon()

    # ── Run ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start nodes (if configured), then launch the tray icon."""
        if self.autostart_nodes:
            threading.Thread(target=self._start_and_refresh, daemon=True).start()

        # Auto-start relay if enabled in config
        if self._relay_cfg.get("enabled"):
            threading.Thread(target=self._start_relay, daemon=True).start()

        # Start background threads
        threading.Thread(target=self._update_check_loop,  daemon=True).start()
        threading.Thread(target=self._status_refresh_loop, daemon=True).start()

        # Create and run the tray icon
        icon_image = make_icon(running=self.autostart_nodes)
        self._icon = pystray.Icon(
            name="moe-network",
            icon=icon_image,
            title="MoE Network",
            menu=self._make_menu(),
        )
        self._icon.run()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    autostart = "--no-autostart" not in sys.argv
    TrayApp(autostart_nodes=autostart).run()
