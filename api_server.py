"""
api_server.py -- FastAPI bridge between the React UI and the Python node network
=================================================================================
Serves the built React frontend as static files and exposes the API endpoints
the UI calls. Manages starting/stopping the expert node subprocesses.

Run directly:
    python api_server.py

Or let the Go shell start it automatically.
"""

import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import AsyncGenerator

import aiohttp
import ollama as ollama_client
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app_paths import FIRST_RUN_SENTINEL, copy_default_experts, ensure_dirs
from setup_wizard import run_if_needed

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent
UI_INDEX  = BASE_DIR / "ui" / "index.html"
API_PORT  = 8080
NODE_URL  = "http://localhost:8001"  # math node is the default orchestrator

# ── Node process manager ──────────────────────────────────────────────────────

class NodeManager:
    def __init__(self):
        self._procs: list[subprocess.Popen] = []

    @property
    def running(self) -> bool:
        return bool(self._procs) and any(p.poll() is None for p in self._procs)

    def start(self):
        if self.running:
            return
        yaml_paths = sorted(BASE_DIR.glob("experts/*.yaml")) + \
                     sorted(BASE_DIR.glob("experts/*.yml"))
        for i, path in enumerate(yaml_paths):
            proc = subprocess.Popen(
                [sys.executable, str(BASE_DIR / "node.py"), str(path)],
                cwd=BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(proc)
            if i == 0:
                time.sleep(2)
        log.info(f"Started {len(self._procs)} expert node(s)")

    def stop(self):
        for p in self._procs:
            p.terminate()
        for p in self._procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self._procs.clear()

    def status(self) -> dict:
        if not self._procs:
            return {"running": False, "nodes": []}
        alive = [p for p in self._procs if p.poll() is None]
        return {"running": bool(alive), "nodes": len(alive)}


nodes = NodeManager()

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="MoE Network API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    model: str | None = None


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    return {**nodes.status(), "api_port": API_PORT}


@app.post("/api/nodes/start")
async def start_nodes():
    nodes.start()
    return nodes.status()


@app.post("/api/nodes/stop")
async def stop_nodes():
    nodes.stop()
    return nodes.status()


@app.get("/api/models")
async def list_models():
    try:
        result = ollama_client.list()
        models = [
            {"name": m.model, "size": m.size}
            for m in result.models
        ]
        return {"models": models}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    Stream the MoE network response as newline-delimited JSON events.
    The React UI reads these via fetch + ReadableStream.
    """
    if not nodes.running:
        raise HTTPException(status_code=503, detail="Nodes are not running. Start the network first.")

    async def event_stream() -> AsyncGenerator[str, None]:
        # Wait up to 10 s for the node to be ready
        for _ in range(10):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{NODE_URL}/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                        if r.status == 200:
                            break
            except Exception:
                pass
            await asyncio.sleep(1)
            yield json.dumps({"type": "status", "text": "Waiting for nodes..."}) + "\n"
        else:
            yield json.dumps({"type": "error", "text": "Nodes did not respond in time."}) + "\n"
            return

        yield json.dumps({"type": "status", "text": "Routing query through experts..."}) + "\n"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{NODE_URL}/ask",
                    json={"query": req.message},
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    data = await resp.json()

            # Emit expert responses
            for er in sorted(data.get("expert_responses", []),
                             key=lambda x: x.get("confidence", 0), reverse=True):
                yield json.dumps({
                    "type": "expert",
                    "specialty": er["specialty"],
                    "confidence": er.get("confidence", 5),
                    "response": er["response"],
                }) + "\n"

            # Emit final answer
            yield json.dumps({
                "type": "answer",
                "text": data.get("answer", "(no answer returned)"),
                "orchestrated_by": data.get("orchestrated_by", "?"),
                "peers_queried": data.get("peers_queried", []),
            }) + "\n"

        except Exception as e:
            yield json.dumps({"type": "error", "text": str(e)}) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


# ── Serve the single-file UI ──────────────────────────────────────────────────

@app.get("/")
async def serve_ui():
    return FileResponse(str(UI_INDEX))


# ── Startup / shutdown ────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    ensure_dirs()
    copy_default_experts()
    # Start nodes automatically on launch
    asyncio.get_event_loop().run_in_executor(None, nodes.start)


@app.on_event("shutdown")
async def on_shutdown():
    nodes.stop()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Show wizard on first run (blocks until wizard closes)
    run_if_needed()

    uvicorn.run(
        "api_server:app",
        host="127.0.0.1",
        port=API_PORT,
        log_level="warning",
    )
