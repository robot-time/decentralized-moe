"""
aggregator.py -- MoA combiner
==============================
Sends a query to every known specialist in parallel, filters out
DOMAIN_MISMATCH replies, and either returns the single matching
specialist's answer directly OR runs a synthesis pass over multiple
matching answers (Mixture of Agents).

Architecture (from the Notion design doc):
  - If only one specialist claims the query: return that answer
  - If multiple claim it: synthesise the best combined answer
  - Node dropouts are absorbed by parallelism — late/missing peers
    just produce fewer responses, not a failure

Used by app.py from a worker thread on each user message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp
import ollama

from network import Peer
from specialist import DOMAIN_MISMATCH


@dataclass
class SpecialistReply:
    specialty: str
    label:     str
    response:  str  # may be DOMAIN_MISMATCH or an error string


@dataclass
class Answer:
    text:     str            # final answer to display
    consulted: list[str]     # specialty labels that returned an answer
    skipped:   list[str]     # specialty labels that returned DOMAIN_MISMATCH
    synthesised: bool        # True if MoA combined multiple answers


# ── Fan-out ──────────────────────────────────────────────────────────────────

async def _ask_one(
    session: aiohttp.ClientSession, peer: Peer, query: str, timeout: int
) -> SpecialistReply | None:
    try:
        async with session.post(
            f"{peer.url.rstrip('/')}/query",
            json={"query": query},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            data = await resp.json()
            return SpecialistReply(
                specialty=data.get("specialty", peer.specialty),
                label=data.get("label", peer.label),
                response=data.get("response", ""),
            )
    except Exception as exc:
        # A peer that times out or 500s is treated as "didn't claim it"
        return SpecialistReply(
            specialty=peer.specialty,
            label=peer.label,
            response=f"ERROR: {exc}",
        )


async def fan_out(
    peers: list[Peer], query: str, timeout: int = 120
) -> list[SpecialistReply]:
    if not peers:
        return []
    async with aiohttp.ClientSession() as session:
        return [
            r for r in await asyncio.gather(
                *[_ask_one(session, p, query, timeout) for p in peers]
            ) if r is not None
        ]


# ── MoA synthesis ────────────────────────────────────────────────────────────

def _is_match(reply: SpecialistReply) -> bool:
    txt = reply.response.strip().upper().replace(" ", "_")
    return (
        txt
        and not txt.startswith("ERROR")
        and DOMAIN_MISMATCH not in txt
    )


def _synthesise(
    query: str, replies: list[SpecialistReply], synthesis_model: str
) -> str:
    """Blend multiple specialist responses into one answer via local model."""
    blocks = []
    for r in replies:
        blocks.append(f"--- {r.label} specialist ---\n{r.response}")
    prompt = (
        f"USER QUERY:\n{query}\n\n"
        f"RESPONSES FROM SPECIALIST AI MODELS:\n\n"
        + "\n\n".join(blocks)
        + "\n\nYOUR TASK:\n"
        "Synthesise the specialist responses above into one clear, "
        "accurate, comprehensive answer.  Write directly to the user; "
        "do not mention the individual specialists by name."
    )
    result = ollama.chat(
        model=synthesis_model,
        messages=[
            {"role": "system",
             "content": "You combine multiple specialist answers into the "
                        "single best response for the user."},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0.2},
    )
    return result["message"]["content"].strip()


# ── Public entry point ───────────────────────────────────────────────────────

def aggregate(
    query: str,
    replies: list[SpecialistReply],
    synthesis_model: str,
) -> Answer:
    matches  = [r for r in replies if _is_match(r)]
    mismatch = [r for r in replies if not _is_match(r)]
    skipped  = [r.label for r in mismatch]

    if not matches:
        return Answer(
            text=("No specialist in the network claimed this query. "
                  "Try rephrasing, or add a specialist that covers the topic."),
            consulted=[],
            skipped=skipped,
            synthesised=False,
        )

    if len(matches) == 1:
        m = matches[0]
        return Answer(
            text=m.response,
            consulted=[m.label],
            skipped=skipped,
            synthesised=False,
        )

    # MoA synthesis
    try:
        text = _synthesise(query, matches, synthesis_model)
        synthesised = True
    except Exception as exc:
        # Fall back to the first match if synthesis fails
        text = matches[0].response + f"\n\n(synthesis failed: {exc})"
        synthesised = False

    return Answer(
        text=text,
        consulted=[r.label for r in matches],
        skipped=skipped,
        synthesised=synthesised,
    )
