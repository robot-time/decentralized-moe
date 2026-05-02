"""
app.py -- MoE Network desktop app (pywebview + HTML frontend)
=============================================================
Serves a local aiohttp web app that mimics the Ollama desktop UI,
then opens it in a native pywebview window.  Thi s gives us the
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
from pathlib import Path

from aiohttp import web

from aggregator import aggregate, fan_out
from config import BUNDLE_DIR, copy_default_experts, ensure_dirs, load, load_expert, save
from network import Network
from specialist import Specialist

_UI_DIR = Path(__file__).parent / "ui"


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
            "version":   "1.3.0",
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
                   "aggregator_model", "wizard_done"}
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
            replies = await fan_out(peers, query)
            answer = aggregate(
                query, replies,
                synthesis_model=self.cfg.get("aggregator_model", "llama3.1:8b"),
            )
            return web.json_response({
                "text":        answer.text,
                "consulted":   answer.consulted,
                "skipped":     answer.skipped,
                "synthesised": answer.synthesised,
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

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

        self.network = Network(
            dht_port=dht_port,
            bootstrap=bootstrap,
            my_specialty=my_specialty,
            my_label=my_label,
            my_http_port=http_port,
        )
        await self.network.start()

    def _run_server(self) -> None:
        asyncio.set_event_loop(self.worker.loop)
        app = web.Application()
        app.router.add_get("/",             self.handle_index)
        app.router.add_get("/api/health",  self.handle_health)
        app.router.add_get("/api/config",  self.handle_config_get)
        app.router.add_post("/api/config", self.handle_config_post)
        app.router.add_get("/api/peers",   self.handle_peers)
        app.router.add_post("/api/query",  self.handle_query)

        runner = web.AppRunner(app)
        self.worker.loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 8765)
        self.worker.loop.run_until_complete(site.start())
        self.worker.loop.run_until_complete(self._start_network())
        self.worker.loop.run_forever()

    def run(self) -> None:
        self.worker.start()
        time.sleep(0.3)
        self._open_window()

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

        webview.create_window(
            "MoE Network",
            "http://127.0.0.1:8765",
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
