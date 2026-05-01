"""
relay/remote.py -- Remote CLI Client
======================================
Lets you talk to your local MoE network from anywhere -- another machine,
a terminal on your phone (via SSH), or any Python environment.

Your query goes:  you -> relay server -> your home machine -> relay -> back to you
All AI processing happens on your home machine. The relay only passes messages.

Usage:
    python remote.py <relay_url> <api_key>
    python remote.py https://my-relay.example.com my-secret-key

Or set environment variables and run without args:
    export MOE_RELAY_URL=https://my-relay.example.com
    export MOE_API_KEY=my-secret-key
    python remote.py
"""

import os
import sys
import time

import requests

# ── Config ────────────────────────────────────────────────────────────────────

RELAY_URL = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MOE_RELAY_URL", "")
API_KEY   = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("MOE_API_KEY", "")

HEADERS   = {"X-API-Key": API_KEY}
POLL_INTERVAL = 2    # seconds between result polls
RESULT_TIMEOUT = 300 # max seconds to wait for a result


def check_relay() -> bool:
    """Verify the relay server is reachable before starting."""
    try:
        resp = requests.get(f"{RELAY_URL}/health", timeout=10)
        data = resp.json()
        print(f"  Relay status: {data.get('status', '?')}  "
              f"(queue depth: {data.get('queue_depth', '?')})")
        return True
    except Exception as e:
        print(f"  Could not reach relay at {RELAY_URL}: {e}")
        return False


def send_query(text: str) -> str | None:
    """Post a query to the relay and return the query ID."""
    try:
        resp = requests.post(
            f"{RELAY_URL}/query",
            json={"text": text},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as e:
        print(f"  Failed to send query: {e}")
        return None


def wait_for_result(query_id: str) -> dict | None:
    """
    Poll /result/{id} until the local app finishes processing.
    The local machine does the work; we just wait here.
    """
    deadline = time.time() + RESULT_TIMEOUT
    dots = 0

    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{RELAY_URL}/result/{query_id}",
                headers=HEADERS,
                timeout=10,
            )
            if resp.status_code == 200:
                print()  # end the "..." line
                return resp.json()
            elif resp.status_code == 204:
                # Not ready yet -- show progress dots
                print("." * (dots % 4 + 1) + "   ", end="\r", flush=True)
                dots += 1
                time.sleep(POLL_INTERVAL)
            else:
                print(f"\n  Unexpected status {resp.status_code}")
                return None
        except Exception as e:
            print(f"\n  Poll error: {e}")
            time.sleep(POLL_INTERVAL)

    print("\n  Timed out waiting for result.")
    return None


def main() -> None:
    if not RELAY_URL or not API_KEY:
        print("Usage:  python remote.py <relay_url> <api_key>")
        print("   or:  MOE_RELAY_URL=... MOE_API_KEY=... python remote.py")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  MoE Network -- Remote Client")
    print(f"  Relay: {RELAY_URL}")
    print()
    if not check_relay():
        print("  Is the relay server running?")
        sys.exit(1)
    print()
    print("  Your queries are processed on your home machine.")
    print("  Nothing is stored on the relay server.")
    print("  Ctrl+C to quit.")
    print("=" * 60)
    print()

    while True:
        try:
            query = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not query:
            continue

        # Submit to relay
        qid = send_query(query)
        if not qid:
            continue

        print(f"Sent (id={qid[:8]}...). Waiting for your machine to respond")

        # Wait for local machine to process and post the result
        result = wait_for_result(qid)
        if not result:
            continue

        experts = result.get("experts", [])
        if experts:
            print(f"[Experts: {', '.join(experts)}]")

        print()
        print(result.get("answer", "(no answer)"))
        print()


if __name__ == "__main__":
    main()
