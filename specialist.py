"""
specialist.py -- Self-routing specialist server
=================================================
Runs an HTTP /query endpoint backed by a local Ollama model.  The model
itself decides whether the query falls in its domain (matches the
"Specialists are now self-aware" direction in the architecture doc).

Usage:
    python specialist.py stem        # runs the STEM specialist
    python specialist.py hass        # runs the HASS specialist

  POST /query  {"query": "..."}  ->  {"specialty": ..., "response": ...}
  GET  /health                   ->  {"status": "ok", ...}

Replies are either the model's answer or the literal string
"DOMAIN_MISMATCH"; the aggregator filters mismatches out.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import ollama
from aiohttp import web

from config import load, load_expert

DOMAIN_MISMATCH = "DOMAIN_MISMATCH"


class Specialist:
    def __init__(self, expert: dict[str, Any], http_port: int) -> None:
        self.specialty:     str = expert["specialty"]
        self.label:         str = expert.get("label", expert["specialty"].upper())
        self.model:         str = expert["model"]
        self.system_prompt: str = expert["system_prompt"]
        self.domain:        str = expert.get("domain", "")
        self.http_port:     int = http_port

    # ── HTTP handlers ─────────────────────────────────────────────────────

    async def handle_query(self, request: web.Request) -> web.Response:
        body  = await request.json()
        query = body.get("query", "").strip()
        if not query:
            return web.json_response(
                {"error": "missing 'query'"}, status=400
            )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: ollama.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user",   "content": query},
                    ],
                    options={"temperature": 0},
                ),
            )
            text = result["message"]["content"].strip()
        except Exception as exc:
            return web.json_response(
                {"specialty": self.specialty,
                 "response":  f"ERROR: {exc}"},
                status=500,
            )

        return web.json_response({
            "specialty": self.specialty,
            "label":     self.label,
            "model":     self.model,
            "response":  text,
        })

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status":    "ok",
            "specialty": self.specialty,
            "label":     self.label,
            "model":     self.model,
            "domain":    self.domain.strip(),
        })

    # ── Server lifecycle ──────────────────────────────────────────────────

    async def serve_forever(self) -> None:
        app = web.Application()
        app.router.add_post("/query",  self.handle_query)
        app.router.add_get ("/health", self.handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.http_port)
        await site.start()

        print(f"[{self.specialty}] serving on http://0.0.0.0:{self.http_port} "
              f"(model={self.model})")
        await asyncio.Event().wait()  # run until cancelled


def run_specialist(specialty: str) -> None:
    cfg    = load()
    expert = load_expert(specialty)
    if expert is None:
        raise SystemExit(f"No expert config found for '{specialty}'")
    asyncio.run(Specialist(expert, cfg.get("http_port", 8001)).serve_forever())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python specialist.py <stem|hass|...>")
        sys.exit(1)
    run_specialist(sys.argv[1])
