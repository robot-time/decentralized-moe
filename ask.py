"""
ask.py -- Minimal CLI for the Decentralized MoE Network
========================================================
Connects to ANY node's /ask endpoint and runs the full MoE pipeline.
There is no coordinator -- the node you connect to orchestrates everything.

Usage:
    python ask.py                          # talks to localhost:8001 (math node)
    python ask.py http://localhost:8002    # talks to the english node
    python ask.py http://localhost:8003    # talks to the code node
    python ask.py http://192.168.1.5:8004  # talks to a node on another machine

The node you connect to:
  - Discovers all peers via DHT
  - Fans out your query to all experts concurrently
  - Synthesizes their responses
  - Returns the final answer

If that node goes down, just point ask.py at a different one.
"""

import sys
import requests


def main() -> None:
    # Default to the math node (bootstrap), but any node works
    base_url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8001"
    ask_url  = f"{base_url}/ask"

    # Check the node is alive before starting the session
    try:
        health = requests.get(f"{base_url}/health", timeout=5).json()
        node_label = f"{health.get('specialty', '?')} ({health.get('model', '?')})"
    except Exception as e:
        print(f"Could not reach node at {base_url}: {e}")
        print("Make sure nodes are running:  ./start.sh")
        sys.exit(1)

    print()
    print("=" * 64)
    print("  Decentralized MoE Network")
    print(f"  Entry point: {node_label} @ {base_url}")
    print("  (Any node orchestrates -- no central coordinator)")
    print("=" * 64)
    print("Type your question and press Enter. Ctrl+C to quit.\n")

    while True:
        try:
            query = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not query:
            continue

        print("\nRouting query through the network...")

        try:
            resp = requests.post(ask_url, json={"query": query}, timeout=180)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"Error: {e}\n")
            continue

        # Show which experts were consulted
        queried   = data.get("peers_queried", [])
        orchestrator = data.get("orchestrated_by", "?")
        print(f"[Orchestrated by: {orchestrator} | Experts consulted: {', '.join(queried)}]")

        # Show individual expert responses (truncated)
        expert_resps = data.get("expert_responses", [])
        if expert_resps:
            print("\n-- Expert responses --")
            for er in sorted(expert_resps, key=lambda x: x.get("confidence", 0), reverse=True):
                preview = er["response"]
                if len(preview) > 250:
                    preview = preview[:250] + "..."
                print(f"\n[{er['specialty'].upper()}] confidence={er.get('confidence','?')}/10")
                print(preview)

        # Final synthesized answer
        print("\n" + "=" * 64)
        print("FINAL ANSWER")
        print("=" * 64)
        print(data.get("answer", "(no answer returned)"))
        print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
