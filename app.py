"""
app.py -- MoE Network desktop app (pywebview + HTML frontend)
=============================================================
Serves a local aiohttp web app that mimics the Ollama desktop UI,
then opens it in a native pywebview window.  This gives us the
Ollama aesthetic in a single installable executable.

Endpoints:
  GET  /            -> static HTML UI
  GET  /api/health  -> {status, role, specialty, model, dht_port}
  GET  /api/config  -> current config dict
  POST /api/config  -> update config (writes config.yaml)
  GET  /api/peers   -> list of discovered peers
  POST /api/query   -> fan-out to specialists + synthesise
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import urllib.request
from pathlib import Path

from aiohttp import web

from aggregator import aggregate, fan_out
from config import BUNDLE_DIR, copy_default_experts, ensure_dirs, load, load_expert, save
from keepawake import KeepAwake
from network import Network, Peer, _local_ip
from specialist import Specialist
from updater import check_latest, is_newer, apply_update, _current_version, _asset_name

_UI_DIR = Path(__file__).parent / "ui"


@web.middleware
async def _cors_middleware(request: web.Request, handler) -> web.Response:
    """Allow pywebview-loaded HTML to call the API (any origin)."""
    if request.method == "OPTIONS":
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


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


class WebApp:
    def __init__(self) -> None:
        ensure_dirs()
        copy_default_experts()
        self.cfg = load()
        self.worker = AsyncWorker()
        self.network: Network | None = None
        self._ready = threading.Event()
        self._keepawake = KeepAwake()
        self._local_specialist_url: str | None = None
        self._update_info: dict = {"checked": False}
        self._auto_update_task = None

    # ── Handlers ────────────────────────────────────────────────────────

    async def handle_index(self, request: web.Request) -> web.Response:
        html_path = _UI_DIR / "index.html"
        if not html_path.exists():
            html_path = BUNDLE_DIR / "ui" / "index.html"
        try:
            text = html_path.read_text()
        except Exception:
            return web.Response(text="UI not found", status=500)
        return web.Response(text=text, content_type="text/html")

    async def handle_health(self, request: web.Request) -> web.Response:
        role = self.cfg.get("role", "user")
        expert = load_expert(role) if role != "user" else None
        return web.json_response({
            "status":    "ok",
            "role":      role,
            "specialty": expert.get("specialty") if expert else None,
            "label":     expert.get("label") if expert else None,
            "model":     expert.get("model") if expert else None,
            "dht_port":  self.cfg.get("dht_port", 8468),
            "version":   "1.6.1",
            "update_available": self._update_info.get("available", False),
        })

    async def handle_config_get(self, request: web.Request) -> web.Response:
        expert = load_expert(self.cfg.get("role", "user"))
        payload = dict(self.cfg)
        if expert:
            payload["expert_label"] = expert.get("label")
            payload["expert_model"] = expert.get("model")
        return web.json_response(payload)

    async def handle_config_post(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        allowed = {"role", "bootstrap", "dht_port", "http_port",
                   "aggregator_model", "wizard_done", "keep_awake",
                   "auto_update"}
        for key in allowed:
            if key in body:
                self.cfg[key] = body[key]
        save(self.cfg)
        return web.json_response({"ok": True})

    async def handle_peers(self, request: web.Request) -> web.Response:
        if self.network is None:
            return web.json_response([])
        peers = await self.network.discover()
        return web.json_response([
            {"specialty": p.specialty, "label": p.label, "url": p.url}
            for p in peers
        ])

    async def handle_query(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        query = body.get("query", "").strip()
        if not query:
            return web.json_response({"error": "empty query"}, status=400)
        if self.network is None:
            return web.json_response({"error": "network not ready"}, status=503)

        try:
            peers = await self.network.discover()
            # Always include the local specialist so queries work even
            # before the DHT peer list has propagated.
            if self._local_specialist_url and not any(
                p.url == self._local_specialist_url for p in peers
            ):
                expert = load_expert(self.cfg.get("role", "user"))
                peers.insert(0, Peer(
                    specialty=expert.get("specialty", self.cfg.get("role", "user")),
                    label=expert.get("label", self.cfg.get("role", "user").upper()),
                    url=self._local_specialist_url,
                    last_seen=time.time(),
                ))
            replies = await fan_out(peers, query)
            answer = aggregate(
                query, replies,
                synthesis_model=self.cfg.get("aggregator_model", "llama3.1:8b"),
            )
            # Build origin info so the UI can show local vs remote
            local_url = self._local_specialist_url
            seen_labels = set()
            origins = []
            for r in replies:
                if r.label not in answer.consulted or r.label in seen_labels:
                    continue
                seen_labels.add(r.label)
                is_local = bool(local_url and r.url == local_url)
                host = r.url.replace("http://", "").replace("https://", "").split(":")[0] if r.url else "unknown"
                origins.append({"label": r.label, "local": is_local, "host": host})
            return web.json_response({
                "text":        answer.text,
                "consulted":   answer.consulted,
                "skipped":     answer.skipped,
                "synthesised": answer.synthesised,
                "origins":     origins,
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def handle_update_check(self, request: web.Request) -> web.Response:
        """Return cached update info or trigger a fresh check."""
        try:
            latest = await check_latest()
            if "error" in latest:
                return web.json_response({"error": latest["error"]}, status=502)
            current = _current_version()
            available = is_newer(current, latest["version"])
            self._update_info = {
                "current": current,
                "latest": latest["version"],
                "available": available,
                "url": latest["url"],
                "asset": latest["assets"].get(_asset_name(), ""),
                "checked": True,
                "checked_at": time.time(),
            }
            return web.json_response(self._update_info)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

    async def handle_update_apply(self, request: web.Request) -> web.Response:
        """Download and apply the update. Returns success before exiting."""
        asset_url = self._update_info.get("asset", "")
        if not asset_url:
            return web.json_response({"error": "no asset url"}, status=400)
        ok = await apply_update(asset_url)
        if ok:
            # Schedule a clean exit after the response is sent
            asyncio.get_event_loop().call_later(1, self._shutdown)
            return web.json_response({"ok": True, "message": "Restarting…"})
        return web.json_response({"error": "update failed"}, status=500)

    def _shutdown(self) -> None:
        """Exit the process so the updater script can replace files."""
        import os
        os._exit(0)

    # ── Startup ─────────────────────────────────────────────────────────

    async def _run_server(self) -> None:
        """Start the aiohttp server and DHT network inside the worker loop."""
        app = web.Application(middlewares=[_cors_middleware])
        app.router.add_get("/",             self.handle_index)
        app.router.add_get("/api/health",  self.handle_health)
        app.router.add_get("/api/config",  self.handle_config_get)
        app.router.add_post("/api/config", self.handle_config_post)
        app.router.add_get("/api/peers",   self.handle_peers)
        app.router.add_post("/api/query",  self.handle_query)
        app.router.add_get("/api/update", self.handle_update_check)
        app.router.add_post("/api/update", self.handle_update_apply)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 8765)
        await site.start()
        print("[server] listening on http://127.0.0.1:8765")

        # Signal that the HTTP server is ready
        self._ready.set()

        # Now start DHT / specialist in the same loop
        await self._start_network()

        # Keep the coroutine alive so the loop doesn't drop it
        await asyncio.Event().wait()

    def _load_ui_html(self) -> str:
        """Read the UI HTML so we can inject it directly into pywebview."""
        html_path = _UI_DIR / "index.html"
        if not html_path.exists():
            html_path = BUNDLE_DIR / "ui" / "index.html"
        try:
            return html_path.read_text(encoding="utf-8")
        except Exception as exc:
            return f"<html><body><h1>UI Error</h1><p>{exc}</p></body></html>"

    async def _start_network(self) -> None:
        cfg = self.cfg
        role = cfg.get("role", "user")
        dht_port = int(cfg.get("dht_port", 8468))
        http_port = int(cfg.get("http_port", 8001))
        bootstrap = cfg.get("bootstrap", "") or ""

        my_specialty: str | None = None
        my_label:     str | None = None

        if role in ("stem", "hass"):
            expert = load_expert(role)
            if expert:
                spec = Specialist(expert, http_port)
                asyncio.create_task(spec.serve_forever())
                my_specialty = spec.specialty
                my_label = spec.label
                self._local_specialist_url = f"http://{_local_ip()}:{http_port}"

        self.network = Network(
            dht_port=dht_port,
            bootstrap=bootstrap,
            my_specialty=my_specialty,
            my_label=my_label,
            my_http_port=http_port,
        )
        await self.network.start()
        print(f"[network] DHT listening on port {dht_port}")

        # Background auto-update check
        if self.cfg.get("auto_update", True):
            asyncio.create_task(self._auto_update_loop())

    async def _auto_update_loop(self) -> None:
        """Check for updates once on startup after a short delay."""
        await asyncio.sleep(30)
        try:
            latest = await check_latest()
            if "error" not in latest:
                current = _current_version()
                available = is_newer(current, latest["version"])
                self._update_info = {
                    "current": current,
                    "latest": latest["version"],
                    "available": available,
                    "url": latest["url"],
                    "asset": latest["assets"].get(_asset_name(), ""),
                    "checked": True,
                    "checked_at": time.time(),
                }
                if available:
                    print(f"[update] v{latest['version']} available (running v{current})")
        except Exception as exc:
            print(f"[update] auto-check failed: {exc}")

    # ── Entry ───────────────────────────────────────────────────────────

    def run(self) -> None:
        if self.cfg.get("keep_awake", True):
            self._keepawake.start()
        self.worker.start()
        self.worker.submit(self._run_server())
        print("[main] waiting for server to be ready...")
        if not self._ready.wait(timeout=10):
            print("[main] ERROR: server failed to start within 10 seconds")
            sys.exit(1)
        # Give the event loop one more tick
        time.sleep(0.2)
        # Poll the health endpoint to confirm HTTP is truly serving
        if not self._poll_server():
            print("[main] ERROR: server not responding to HTTP requests")
            sys.exit(1)
        print("[main] server ready, opening window")
        try:
            self._open_window()
        finally:
            self._keepawake.stop()

    def _poll_server(self) -> bool:
        for _ in range(20):
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8765/api/health", timeout=0.5
                ) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                time.sleep(0.1)
        return False

    def _open_window(self) -> None:
        try:
            import webview
        except ImportError:
            print("pywebview not installed. Opening in system browser...")
            import webbrowser
            webbrowser.open("http://127.0.0.1:8765")
            while True:
                time.sleep(1)
            return

        html = self._load_ui_html()
        win = webview.create_window(
            "MoE Network",
            html=html,
            width=1024,
            height=768,
            min_size=(720, 520),
            text_select=True,
        )
        webview.start(debug=False)


def main() -> None:
    WebApp().run()


if __name__ == "__main__":
    main()
