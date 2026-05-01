"""
relay/server.py -- The Relay Server
=====================================
A lightweight message broker that sits between your phone/remote client
and your local MoE network. It never processes AI queries itself -- it
just holds messages in a queue until the local app picks them up.

Architecture (mirrors how Claude Dispatch works):

  Phone/remote              Relay Server               Local App (tray.py)
      |                          |                            |
      |  POST /query {text}      |                            |
      |------------------------->|                            |
      |  {id: "abc"}             |  GET /poll  (long-poll)   |
      |<-------------------------|<---------------------------|
      |                          |  200 {id, text}            |
      |                          |--------------------------->|
      |                          |  [local MoE processes it]  |
      |                          |  POST /result/abc {answer} |
      |                          |<---------------------------|
      |  GET /result/abc         |                            |
      |------------------------->|                            |
      |  {answer: "..."}         |                            |
      |<-------------------------|                            |

The local app never opens any inbound ports. It only makes outbound
requests to this server. All AI work happens on the local machine.

Deployment (pick any):
  - Railway:    railway up
  - Render:     connect repo, set start command to: uvicorn server:app
  - fly.io:     fly launch
  - VPS:        python -m uvicorn server:app --host 0.0.0.0 --port 8000
  - Local+ngrok: ngrok http 8000  (for testing)

Environment variables:
  API_KEY   -- shared secret (set this + relay_config.yaml to the same value)
  PORT      -- port to listen on (default 8000)

Usage:
    pip install -r requirements.txt
    API_KEY=my-secret uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────

API_KEY          = os.environ.get("API_KEY", "change-me-to-a-strong-secret")
RESULT_TTL_MINS  = 10   # how long to keep results before expiring them
MAX_QUEUE_SIZE   = 100  # refuse new queries if queue is this deep

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="MoE Network Relay",
    description="Message relay for the Decentralized MoE Network",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory state ───────────────────────────────────────────────────────────
# For production with multiple relay server instances, replace these with
# Redis (use aioredis). For a single instance they work fine.

query_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

# Results dict: query_id -> {answer, experts, created_at, expires_at}
results: dict[str, dict] = {}

# ── Auth ──────────────────────────────────────────────────────────────────────

def verify_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    """All endpoints require the shared API key in the X-API-Key header."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Models ────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    text: str
    session_id: Optional[str] = None   # optional tag for multi-user setups

class ResultPost(BaseModel):
    answer: str
    experts: list[str] = []            # which specialists contributed


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    """Public health check -- no auth needed."""
    return {
        "status":      "ok",
        "queue_depth": query_queue.qsize(),
        "results_cached": len(results),
    }


@app.post("/query", dependencies=[Depends(verify_key)])
async def receive_query(query: QueryRequest) -> dict:
    """
    Remote client posts a query here.
    The query is queued and the client gets back an ID to poll for the result.
    """
    if query_queue.full():
        raise HTTPException(status_code=503, detail="Queue full -- try again shortly")

    qid = str(uuid.uuid4())
    await query_queue.put({
        "id":         qid,
        "text":       query.text,
        "session_id": query.session_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"id": qid, "status": "queued"}


@app.get("/poll", dependencies=[Depends(verify_key)])
async def poll(timeout: int = 30) -> Response:
    """
    Local app calls this in a loop to receive incoming queries.

    This is a LONG-POLL endpoint: the server holds the connection open
    for up to `timeout` seconds waiting for a query. If one arrives,
    it responds immediately. If none arrives, it returns 204 (No Content)
    and the local app re-polls right away.

    This means near-zero latency when a query arrives, and no wasted
    requests when the queue is empty.
    """
    try:
        item = await asyncio.wait_for(query_queue.get(), timeout=float(timeout))
        return item  # FastAPI auto-serializes dicts to JSON
    except asyncio.TimeoutError:
        # Nothing in the queue -- tell the client to re-poll
        return Response(status_code=204)


@app.post("/result/{query_id}", dependencies=[Depends(verify_key)])
async def post_result(query_id: str, result: ResultPost) -> dict:
    """
    Local app posts the answer here after processing a query locally.
    Results are stored for RESULT_TTL_MINS minutes then discarded.
    """
    results[query_id] = {
        "answer":     result.answer,
        "experts":    result.experts,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=RESULT_TTL_MINS),
    }
    # Clean up old results opportunistically
    _purge_expired_results()
    return {"status": "ok"}


@app.get("/result/{query_id}", dependencies=[Depends(verify_key)])
async def get_result(query_id: str) -> Response:
    """
    Remote client polls here waiting for the local app to finish.
    Returns 204 if not ready yet, 200 with the answer when done.
    """
    if query_id not in results:
        return Response(status_code=204)  # not ready yet

    result = results[query_id]
    if datetime.now(timezone.utc) > result["expires_at"]:
        del results[query_id]
        raise HTTPException(status_code=404, detail="Result expired")

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _purge_expired_results() -> None:
    now = datetime.now(timezone.utc)
    expired = [k for k, v in results.items() if now > v["expires_at"]]
    for k in expired:
        del results[k]


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
