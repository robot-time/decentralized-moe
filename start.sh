#!/usr/bin/env bash
# start.sh -- Launch the Decentralized MoE Network
# =================================================
# First time? Run ./setup.sh first to pull models and install dependencies.
#
# After that, just run ./start.sh
# Then in another terminal: python ask.py
#
# Any node can orchestrate -- no coordinator, no single point of failure.
# If a node dies, point ask.py at a different port and keep going.

set -e

# ── Quick preflight check ──────────────────────────────────────────────────
if ! python3 -c "import kademlia, aiohttp, ollama, yaml" 2>/dev/null; then
    echo "Dependencies missing. Run ./setup.sh first."
    exit 1
fi

if ! ollama list &>/dev/null; then
    echo "Ollama is not running. Starting it..."
    ollama serve &>/dev/null &
    sleep 3
fi

echo ""
echo "=================================================="
echo "  Decentralized MoE Network"
echo "  Every node orchestrates -- no coordinator"
echo "=================================================="
echo ""

# ── Start nodes (math first -- it's the DHT bootstrap) ────────────────────
PIDS=()
cleanup() {
    echo ""
    echo "Shutting down..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    echo "Done."
}
trap cleanup EXIT INT TERM

echo "Starting expert nodes..."
echo ""

# Find and start every expert YAML in experts/
# Order matters: math.yaml must start first (is_bootstrap: true)
# We sort so math.yaml comes first alphabetically; adjust if needed.
FIRST=true
for yaml_file in $(ls experts/*.yaml experts/*.yml 2>/dev/null | sort); do
    SPECIALTY=$(python3 -c "import yaml; d=yaml.safe_load(open('$yaml_file')); print(d.get('specialty','?'))")
    HTTP_PORT=$(python3 -c "import yaml; d=yaml.safe_load(open('$yaml_file')); print(d.get('http_port','?'))")
    MODEL=$(python3 -c "import yaml; d=yaml.safe_load(open('$yaml_file')); print(d.get('model','?'))")

    python3 node.py "$yaml_file" &
    PIDS+=($!)

    echo "  [$SPECIALTY] model=$MODEL  http=:$HTTP_PORT  PID=${PIDS[-1]}"

    # Give the first node (bootstrap) 2 seconds to open its DHT port
    if [ "$FIRST" = true ]; then
        sleep 2
        FIRST=false
    fi
done

echo ""
echo "Waiting for DHT registration..."
sleep 3

echo ""
echo "=================================================="
echo "  Network is up! Talk to any node:"
echo ""

for yaml_file in $(ls experts/*.yaml experts/*.yml 2>/dev/null | sort); do
    SPECIALTY=$(python3 -c "import yaml; d=yaml.safe_load(open('$yaml_file')); print(d.get('specialty','?'))")
    HTTP_PORT=$(python3 -c "import yaml; d=yaml.safe_load(open('$yaml_file')); print(d.get('http_port','?'))")
    echo "    python ask.py http://localhost:$HTTP_PORT    <- $SPECIALTY node"
done

echo ""
echo "  Ctrl+C to shut everything down."
echo "=================================================="
echo ""

wait
