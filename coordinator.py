"""
coordinator.py -- Query Coordinator for the Decentralized MoE Network
======================================================================
The coordinator is the user-facing interface to the whole network. It:

  1. Discovers all live expert nodes via the shared Kademlia DHT
  2. Routes each user query to the right experts (MoE gate)
  3. Queries all selected experts concurrently (MoA -- Mixture of Agents)
  4. Synthesizes their responses into one final answer using a local Ollama model

MoE (Mixture of Experts):
  Each specialist node has a domain description. The coordinator scores
  how well the query matches each node's description and selects the top-k.
  In production you would use embedding similarity; here we use keyword overlap
  which is easy to understand and surprisingly effective.

MoA (Mixture of Agents):
  Instead of picking ONE expert, we ask SEVERAL concurrently and combine their
  answers. This is the "Mixture of Agents" pattern. The synthesis model reads
  all expert responses (ordered by confidence) and produces a single answer
  that draws on each expert's strengths.

Usage:
    python coordinator.py
    (Make sure at least one node is running first)
"""

import asyncio
import json
import logging
import sys
from typing import Optional

import aiohttp
import ollama
from kademlia.network import Server

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Config constants (easy to change) ────────────────────────────────────────

DHT_BOOTSTRAP_HOST = "127.0.0.1"
DHT_BOOTSTRAP_PORT = 8468       # Must match math node's dht_port

COORDINATOR_DHT_PORT = 8475     # Our own DHT port (distinct from all nodes)

SYNTHESIS_MODEL = "llama3:8b"   # Model used to blend expert answers
                                # Change to any model you have pulled

QUERY_TIMEOUT_SECONDS = 60      # Max wait for a single expert response

TOP_K_EXPERTS = 4               # How many experts to consult per query
                                # Set to None to always query all of them

# ── Coordinator ───────────────────────────────────────────────────────────────


class Coordinator:
    """
    Discovers, routes, queries, and synthesizes across the expert node network.
    """

    def __init__(self) -> None:
        self.dht: Server = Server()
        self.nodes: dict[str, dict] = {}  # specialty -> node metadata dict

    # ── DHT: discovery ────────────────────────────────────────────────────────

    async def _connect_dht(self) -> None:
        """Join the DHT network as an observer (no HTTP server needed)."""
        await self.dht.listen(COORDINATOR_DHT_PORT)
        await self.dht.bootstrap([(DHT_BOOTSTRAP_HOST, DHT_BOOTSTRAP_PORT)])
        log.info(f"Connected to DHT network via {DHT_BOOTSTRAP_HOST}:{DHT_BOOTSTRAP_PORT}")

    async def _discover_nodes(self) -> None:
        """
        Read the shared node_list from the DHT, then fetch each node's
        full metadata. We re-run this before every query so that nodes
        joining or leaving the network are picked up automatically.
        """
        raw = await self.dht.get("node_list")
        if not raw:
            log.warning("DHT node_list is empty -- are any nodes running?")
            self.nodes = {}
            return

        specialties: list[str] = json.loads(raw)
        nodes: dict[str, dict] = {}

        for specialty in specialties:
            raw_info = await self.dht.get(f"node_{specialty}")
            if raw_info:
                nodes[specialty] = json.loads(raw_info)
            else:
                log.warning(f"Could not fetch metadata for node '{specialty}'")

        self.nodes = nodes
        log.info(f"Discovered {len(self.nodes)} node(s): {list(self.nodes)}")

    # ── MoE: routing gate ─────────────────────────────────────────────────────

    def _select_experts(self, query: str, top_k: Optional[int] = TOP_K_EXPERTS) -> list[dict]:
        """
        MoE Gating: score each expert by how well its domain description
        overlaps with the user's query, then pick the top-k.

        This is a simple keyword-frequency gate. For production, replace
        with cosine similarity of embeddings for much better routing.

        If top_k is None, all experts are selected (full MoA).
        """
        if not self.nodes:
            return []

        query_words = set(query.lower().split())

        scored: list[tuple[int, dict]] = []
        for specialty, info in self.nodes.items():
            description_words = set(info.get("description", specialty).lower().split())
            # Score = number of query words that appear in this expert's domain description
            overlap = len(query_words & description_words)
            scored.append((overlap, info))

        # Sort highest overlap first
        scored.sort(key=lambda x: x[0], reverse=True)

        if top_k is None:
            return [info for _, info in scored]

        # Always include at least 1 expert; fall back to all if all scores are 0
        selected = [info for _, info in scored[:top_k]]
        return selected if selected else list(self.nodes.values())

    # ── MoA: concurrent querying ──────────────────────────────────────────────

    async def _query_one_expert(
        self,
        session: aiohttp.ClientSession,
        node: dict,
        query: str,
    ) -> Optional[dict]:
        """
        Send a query to a single expert node and return its response dict.
        Returns None if the node is unreachable or times out -- the
        coordinator gracefully skips failed nodes.
        """
        url = f"http://{node['host']}:{node['http_port']}/query"
        timeout = aiohttp.ClientTimeout(total=QUERY_TIMEOUT_SECONDS)
        try:
            async with session.post(url, json={"query": query}, timeout=timeout) as resp:
                return await resp.json()
        except Exception as e:
            log.warning(f"  [{node['specialty']}] No response: {e}")
            return None

    async def _query_all_experts(self, query: str, nodes: list[dict]) -> list[dict]:
        """
        MoA: fire all expert queries at the same time with asyncio.gather.

        Total wall-clock time = max(individual response times), not the sum.
        Failed / timed-out nodes are filtered out so synthesis still works.
        """
        async with aiohttp.ClientSession() as session:
            tasks = [self._query_one_expert(session, node, query) for node in nodes]
            results = await asyncio.gather(*tasks)

        return [r for r in results if r is not None]

    # ── MoA: synthesis ────────────────────────────────────────────────────────

    def _build_synthesis_prompt(self, query: str, expert_responses: list[dict]) -> str:
        """
        Build the prompt that asks SYNTHESIS_MODEL to blend expert answers.

        Experts are ordered by confidence (highest first) so the synthesizer
        naturally gives more weight to domain-relevant answers.
        """
        sorted_responses = sorted(
            expert_responses,
            key=lambda x: x.get("confidence", 0),
            reverse=True,
        )

        lines = [
            "USER QUERY:",
            query,
            "",
            "RESPONSES FROM SPECIALIST AI MODELS:",
            "(ordered by domain confidence, highest first)",
            "",
        ]

        for resp in sorted_responses:
            conf = resp.get("confidence", "?")
            lines.append(f"--- {resp['specialty'].upper()} EXPERT  [confidence {conf}/10] ---")
            lines.append(resp["response"])
            lines.append("")

        lines += [
            "YOUR TASK:",
            "Synthesize the above specialist responses into ONE clear, comprehensive answer.",
            "Prioritize information from higher-confidence experts for domain-specific facts.",
            "Do not mention the individual experts or their confidence scores in your answer.",
            "Write directly to the user.",
        ]

        return "\n".join(lines)

    async def _synthesize(self, query: str, expert_responses: list[dict]) -> str:
        """
        Use a local Ollama model to blend all expert responses into one answer.
        This is the MoA aggregation step.
        """
        synthesis_prompt = self._build_synthesis_prompt(query, expert_responses)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: ollama.chat(
                model=SYNTHESIS_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a synthesis agent in a Mixture of Agents system. "
                            "You receive responses from multiple specialist AI models "
                            "and combine them into the single best possible answer. "
                            "Be clear, accurate, and comprehensive."
                        ),
                    },
                    {"role": "user", "content": synthesis_prompt},
                ],
            ),
        )
        return result["message"]["content"]

    # ── Interactive loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main event loop: discover nodes, ask user for queries, synthesize."""
        await self._connect_dht()
        await self._discover_nodes()

        if not self.nodes:
            print("\nNo expert nodes found in the DHT.")
            print("Start at least one node with:  python node.py experts/math.yaml")
            return

        print()
        print("=" * 64)
        print("  Decentralized MoE Network -- Coordinator")
        print(f"  {len(self.nodes)} expert node(s) online: {', '.join(self.nodes)}")
        print(f"  Synthesis model: {SYNTHESIS_MODEL}")
        print("=" * 64)
        print("Ask anything. The network will route your query to the right")
        print("experts, collect their answers, and synthesize a final response.")
        print("Press Ctrl+C to quit.\n")

        while True:
            # Read query (handle EOF gracefully)
            try:
                query = input("You: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nBye!")
                break

            if not query:
                continue

            # Re-discover nodes on every query (handles nodes joining/leaving)
            await self._discover_nodes()
            if not self.nodes:
                print("No nodes available right now.\n")
                continue

            # ── MoE: select experts ────────────────────────────────────────
            experts = self._select_experts(query, top_k=TOP_K_EXPERTS)
            expert_names = [n["specialty"] for n in experts]
            print(f"\n[MoE] Routing to: {', '.join(expert_names)}")

            # ── MoA: query concurrently ────────────────────────────────────
            print("[MoA] Querying experts in parallel...")
            expert_responses = await self._query_all_experts(query, experts)

            if not expert_responses:
                print("All experts failed to respond. Check that nodes are running.\n")
                continue

            # Show each expert's raw answer (truncated for readability)
            print("\n" + "-" * 64)
            print("EXPERT RESPONSES")
            print("-" * 64)
            for resp in sorted(expert_responses, key=lambda x: x.get("confidence", 0), reverse=True):
                conf = resp.get("confidence", "?")
                preview = resp["response"]
                if len(preview) > 300:
                    preview = preview[:300] + "..."
                print(f"\n[{resp['specialty'].upper()}] confidence={conf}/10")
                print(preview)

            # ── MoA synthesis ──────────────────────────────────────────────
            print("\n" + "-" * 64)
            print(f"SYNTHESIZING with {SYNTHESIS_MODEL}...")
            print("-" * 64)
            final_answer = await self._synthesize(query, expert_responses)

            print("\nFINAL ANSWER")
            print("=" * 64)
            print(final_answer)
            print("=" * 64 + "\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(Coordinator().run())
