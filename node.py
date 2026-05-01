"""
node.py -- Expert Node for the Decentralized MoE Network
=========================================================
Every node is fully self-sufficient. There is no central coordinator.

Each node exposes TWO endpoints:

  POST /query  -- "peer call": just answer in your specialty, no fan-out.
                  Used by other nodes when they orchestrate a full ask.

  POST /ask    -- "user call": orchestrate the full MoE pipeline yourself.
                  Discover all peers via DHT, fan out /query to everyone
                  (including yourself), collect answers, synthesize with a
                  local Ollama model, return the final answer.
                  Any node can serve this -- no single point of failure.

The split between /query and /ask is what prevents routing loops:
  /ask  fans out to peers using /query only, never /ask.
  /query never fans out at all -- it just answers and returns.

Usage:
    python node.py experts/math.yaml      # also the DHT bootstrap
    python node.py experts/english.yaml
    python node.py experts/code.yaml
    python node.py experts/science.yaml

Then talk to any node:
    python ask.py                         # connects to localhost:8001 by default
    python ask.py http://localhost:8003   # connect to the code node instead
"""

import asyncio
import json
import logging
import re
import sys
from typing import Any, Optional

import aiohttp
import ollama
import yaml
from aiohttp import web
from kademlia.network import Server

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

BOOTSTRAP_HOST = "127.0.0.1"
BOOTSTRAP_PORT = 8468          # math node's DHT port -- the network entry point

QUERY_TIMEOUT  = 60            # seconds to wait for a peer's /query response
SYNTHESIS_MODEL = "llama3.2:latest"  # model used to blend all expert answers
                               # any node can use a different one via its yaml

# ── Expert Node ───────────────────────────────────────────────────────────────


class ExpertNode:
    """
    A fully autonomous expert node. Can answer queries in its specialty
    AND orchestrate the full MoE/MoA pipeline when a user talks to it.
    """

    def __init__(self, config_path: str) -> None:
        with open(config_path) as f:
            cfg: dict[str, Any] = yaml.safe_load(f)

        self.specialty:    str  = cfg["specialty"]
        self.model:        str  = cfg["model"]
        self.http_port:    int  = cfg["http_port"]
        self.dht_port:     int  = cfg["dht_port"]
        self.is_bootstrap: bool = cfg.get("is_bootstrap", False)
        self.description:  str  = cfg.get("description", self.specialty)
        self.system_prompt: str = cfg["system_prompt"]
        # Allow per-node override of synthesis model; fall back to global default
        self.synthesis_model: str = cfg.get("synthesis_model", SYNTHESIS_MODEL)

        self.dht: Server = Server()

    # ─────────────────────────────────────────────────────────────────────────
    # DHT: join and register
    # ─────────────────────────────────────────────────────────────────────────

    async def _join_dht(self) -> None:
        """
        Open our DHT port and join the Kademlia network.

        The math node is the bootstrap -- it creates the network.
        Every other node connects to the bootstrap to join.
        After joining, we store our metadata under node_{specialty} and
        append ourselves to the shared node_list.
        """
        await self.dht.listen(self.dht_port)

        if not self.is_bootstrap:
            await self.dht.bootstrap([(BOOTSTRAP_HOST, BOOTSTRAP_PORT)])
            log.info(f"[{self.specialty}] Joined DHT via {BOOTSTRAP_HOST}:{BOOTSTRAP_PORT}")
        else:
            log.info(f"[{self.specialty}] Started as DHT bootstrap on :{self.dht_port}")

        # Write our own metadata into the DHT
        await self.dht.set(
            f"node_{self.specialty}",
            json.dumps({
                "specialty":   self.specialty,
                "model":       self.model,
                "host":        "127.0.0.1",
                "http_port":   self.http_port,
                "description": self.description,
            }),
        )

        # Safe read-modify-write on the shared node list
        raw = await self.dht.get("node_list")
        node_list: list[str] = json.loads(raw) if raw else []
        if self.specialty not in node_list:
            node_list.append(self.specialty)
        await self.dht.set("node_list", json.dumps(node_list))

        log.info(f"[{self.specialty}] Registered. Network: {node_list}")

    async def _discover_peers(self) -> list[dict]:
        """
        Read the DHT to find all live peer nodes (including ourselves).
        Called fresh on every /ask so newly joined nodes are included.
        """
        raw = await self.dht.get("node_list")
        if not raw:
            return []

        peers: list[dict] = []
        for specialty in json.loads(raw):
            raw_info = await self.dht.get(f"node_{specialty}")
            if raw_info:
                peers.append(json.loads(raw_info))

        return peers

    # ─────────────────────────────────────────────────────────────────────────
    # MoE: select which peers to query
    # ─────────────────────────────────────────────────────────────────────────

    def _select_peers(self, query: str, peers: list[dict], top_k: Optional[int] = None) -> list[dict]:
        """
        MoE Gating: score each peer by keyword overlap between the query
        and that peer's domain description, then return the top-k.

        top_k=None means query everyone (full MoA).

        In production you would replace keyword overlap with cosine
        similarity of embeddings for much better routing accuracy.
        """
        if top_k is None:
            return peers

        query_words = set(query.lower().split())
        scored = [
            (len(query_words & set(p.get("description", p["specialty"]).lower().split())), p)
            for p in peers
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [p for _, p in scored[:top_k]]
        return selected if selected else peers  # never return empty

    # ─────────────────────────────────────────────────────────────────────────
    # MoA: fan out /query to all selected peers concurrently
    # ─────────────────────────────────────────────────────────────────────────

    async def _call_peer_query(
        self,
        session: aiohttp.ClientSession,
        peer: dict,
        query: str,
    ) -> Optional[dict]:
        """
        Call POST /query on a single peer node.
        Returns None silently if the peer is unreachable or times out --
        the network degrades gracefully when nodes go down.
        """
        url = f"http://{peer['host']}:{peer['http_port']}/query"
        timeout = aiohttp.ClientTimeout(total=QUERY_TIMEOUT)
        try:
            async with session.post(url, json={"query": query}, timeout=timeout) as resp:
                return await resp.json()
        except Exception as exc:
            log.warning(f"[{self.specialty}] Peer {peer['specialty']} unreachable: {exc}")
            return None

    async def _fan_out(self, query: str, peers: list[dict]) -> list[dict]:
        """
        MoA: fire /query at all selected peers simultaneously.
        Total latency = slowest peer, not the sum of all peers.
        """
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                *[self._call_peer_query(session, p, query) for p in peers]
            )
        return [r for r in results if r is not None]

    # ─────────────────────────────────────────────────────────────────────────
    # MoA: synthesize all expert responses into one answer
    # ─────────────────────────────────────────────────────────────────────────

    def _build_synthesis_prompt(self, query: str, responses: list[dict]) -> str:
        """
        Build the prompt for the synthesis model.
        Responses are ordered by confidence (highest first) so the model
        naturally leans on the most domain-relevant experts.
        """
        ordered = sorted(responses, key=lambda r: r.get("confidence", 0), reverse=True)

        lines = [
            "USER QUERY:",
            query,
            "",
            "RESPONSES FROM SPECIALIST AI MODELS (highest confidence first):",
            "",
        ]
        for r in ordered:
            conf = r.get("confidence", "?")
            lines.append(f"--- {r['specialty'].upper()} EXPERT  [confidence {conf}/10] ---")
            lines.append(r["response"])
            lines.append("")

        lines += [
            "YOUR TASK:",
            "Synthesize the specialist responses above into ONE clear, accurate,",
            "comprehensive answer. Prioritize higher-confidence experts for",
            "domain-specific facts. Write directly to the user; do not mention",
            "the individual experts or their confidence scores.",
        ]
        return "\n".join(lines)

    async def _synthesize(self, query: str, responses: list[dict]) -> str:
        """Run the synthesis model to blend all expert answers (MoA aggregation)."""
        prompt = self._build_synthesis_prompt(query, responses)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: ollama.chat(
                model=self.synthesis_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a synthesis agent in a Mixture of Agents system. "
                            "You receive answers from multiple specialist AI models and "
                            "combine them into the single best possible response. "
                            "Be clear, accurate, and comprehensive."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            ),
        )
        return result["message"]["content"]

    # ─────────────────────────────────────────────────────────────────────────
    # HTTP endpoints
    # ─────────────────────────────────────────────────────────────────────────

    async def handle_query(self, request: web.Request) -> web.Response:
        """
        POST /query  { "query": "..." }

        PEER CALL -- answer only in this node's specialty.
        Never fans out to other nodes (that would create routing loops).

        The model self-reports a CONFIDENCE score (0-10) which the
        orchestrating node uses to weight responses during synthesis.
        """
        body  = await request.json()
        query: str = body.get("query", "")

        log.info(f"[{self.specialty}] /query: {query[:70]}...")

        # Append a confidence self-rating instruction to the user prompt
        augmented = (
            f"{query}\n\n"
            "---\n"
            "After your answer, on a NEW LINE write exactly:\n"
            "CONFIDENCE: <0-10>\n"
            "(10 = squarely in my domain; 0 = completely outside it)"
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user",   "content": augmented},
                ],
            ),
        )

        raw: str = result["message"]["content"]
        confidence = self._parse_confidence(raw)
        clean = re.sub(r"\n*CONFIDENCE:\s*\d+\s*$", "", raw, flags=re.IGNORECASE).strip()

        return web.json_response({
            "specialty":  self.specialty,
            "model":      self.model,
            "response":   clean,
            "confidence": confidence,
        })

    async def handle_ask(self, request: web.Request) -> web.Response:
        """
        POST /ask  { "query": "..." }

        USER CALL -- full MoE + MoA pipeline, orchestrated by THIS node.
        Any node in the network can serve this endpoint. If you connect
        to the math node, it orchestrates. If you connect to the code
        node, that one orchestrates. No central coordinator needed.

        Pipeline:
          1. Discover all peers via DHT
          2. MoE: select the best peers for this query
          3. MoA: fan out /query to all selected peers concurrently
          4. Synthesize all responses into one answer
          5. Return final answer + raw expert responses for transparency
        """
        body  = await request.json()
        query: str = body.get("query", "")

        log.info(f"[{self.specialty}] /ask (orchestrating): {query[:70]}...")

        # Step 1: discover peers (fresh every call -- handles churn)
        peers = await self._discover_peers()
        if not peers:
            return web.json_response(
                {"error": "No peers found in DHT. Is the network up?"},
                status=503,
            )

        # Step 2: MoE gate -- select peers (None = all, or pass top_k=N)
        selected = self._select_peers(query, peers, top_k=None)
        log.info(f"[{self.specialty}] Routing to: {[p['specialty'] for p in selected]}")

        # Step 3: MoA fan-out -- query all selected peers in parallel
        expert_responses = await self._fan_out(query, selected)
        if not expert_responses:
            return web.json_response(
                {"error": "All peers failed to respond."},
                status=503,
            )

        # Step 4: synthesize
        final_answer = await self._synthesize(query, expert_responses)

        # Step 5: return answer + raw responses so callers can inspect
        return web.json_response({
            "answer":           final_answer,
            "orchestrated_by":  self.specialty,
            "peers_queried":    [r["specialty"] for r in expert_responses],
            "expert_responses": expert_responses,
        })

    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health -- liveness probe."""
        return web.json_response({
            "status":    "ok",
            "specialty": self.specialty,
            "model":     self.model,
            "http_port": self.http_port,
        })

    def _parse_confidence(self, text: str) -> int:
        """Parse CONFIDENCE: N from model output. Defaults to 5 if missing."""
        m = re.search(r"CONFIDENCE:\s*(\d+)", text, re.IGNORECASE)
        if m:
            try:
                return max(0, min(10, int(m.group(1))))
            except ValueError:
                pass
        return 5

    # ─────────────────────────────────────────────────────────────────────────
    # Startup
    # ─────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Join the DHT, start the HTTP server, run forever."""
        await self._join_dht()

        app = web.Application()
        app.router.add_post("/query",  self.handle_query)   # peer call
        app.router.add_post("/ask",    self.handle_ask)     # user call (orchestrates)
        app.router.add_get ("/health", self.handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.http_port)
        await site.start()

        log.info(
            f"[{self.specialty}] Ready!  "
            f"model={self.model}  "
            f"http=0.0.0.0:{self.http_port}  "
            f"dht=:{self.dht_port}"
        )
        await asyncio.sleep(float("inf"))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python node.py <config.yaml>")
        print("Example: python node.py experts/math.yaml")
        sys.exit(1)

    asyncio.run(ExpertNode(sys.argv[1]).start())
