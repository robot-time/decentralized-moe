#!/usr/bin/env bash
# setup.sh -- One-time setup for the Decentralized MoE Network
# =============================================================
# Run this once before your first ./start.sh
# It reads every YAML in experts/ and pulls exactly the models your
# network needs. Add a new expert YAML -> re-run setup -> model is pulled.
#
# Usage:
#   ./setup.sh

set -e

echo ""
echo "=================================================="
echo "  Decentralized MoE Network -- Setup"
echo "=================================================="
echo ""

# ── 1. Check prerequisites ─────────────────────────────────────────────────

echo "Checking prerequisites..."

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.10+ first."
    exit 1
fi
echo "  python3: OK ($(python3 --version))"

if ! command -v ollama &>/dev/null; then
    echo ""
    echo "ERROR: ollama not found."
    echo "Install it from https://ollama.com then re-run this script."
    exit 1
fi
echo "  ollama:  OK ($(ollama --version 2>/dev/null || echo 'installed'))"

# Check Ollama is actually running
if ! ollama list &>/dev/null; then
    echo ""
    echo "Ollama is installed but not running. Starting it..."
    ollama serve &>/dev/null &
    sleep 3
    echo "  ollama serve: started in background"
fi

echo ""

# ── 2. Install Python dependencies ────────────────────────────────────────

echo "Installing Python dependencies..."
pip install -r requirements.txt --quiet
echo "  All packages installed."
echo ""

# ── 3. Discover which models the network needs ────────────────────────────
# Read every experts/*.yaml and extract the "model:" field.
# This means you never have to manually track which models to pull --
# just edit your expert YAMLs and re-run setup.

echo "Reading expert configs to discover required models..."
echo ""

MODELS_NEEDED=$(python3 - <<'PYEOF'
import os, yaml, sys

experts_dir = "experts"
models = []
for fname in sorted(os.listdir(experts_dir)):
    if not fname.endswith((".yaml", ".yml")):
        continue
    path = os.path.join(experts_dir, fname)
    with open(path) as f:
        cfg = yaml.safe_load(f)
    specialty = cfg.get("specialty", fname)
    model     = cfg.get("model", "")
    synth     = cfg.get("synthesis_model", "")
    if model:
        models.append(f"{specialty}:{model}")
    if synth and synth not in [m.split(":",1)[1] for m in models]:
        models.append(f"synthesis:{synth}")

for m in models:
    print(m)
PYEOF
)

if [ -z "$MODELS_NEEDED" ]; then
    echo "No expert configs found in experts/. Add YAML files and re-run."
    exit 1
fi

# Also grab the synthesis model from coordinator default
SYNTHESIS_MODEL=$(python3 - <<'PYEOF'
import re
with open("node.py") as f:
    content = f.read()
match = re.search(r'^SYNTHESIS_MODEL\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
if match:
    print(match.group(1))
PYEOF
)

echo "Models required by your network:"
echo ""

declare -A SEEN_MODELS

while IFS= read -r line; do
    SPECIALTY="${line%%:*}"
    MODEL="${line#*:}"
    echo "  [$SPECIALTY] $MODEL"
    SEEN_MODELS["$MODEL"]=1
done <<< "$MODELS_NEEDED"

# Add synthesis model if not already in the list
if [ -n "$SYNTHESIS_MODEL" ] && [ -z "${SEEN_MODELS[$SYNTHESIS_MODEL]}" ]; then
    echo "  [synthesis] $SYNTHESIS_MODEL"
    SEEN_MODELS["$SYNTHESIS_MODEL"]=1
fi

echo ""

# ── 4. Pull each model ─────────────────────────────────────────────────────

echo "Pulling models (already-pulled models are instant)..."
echo ""

FAILED=()
for MODEL in "${!SEEN_MODELS[@]}"; do
    echo "  Pulling $MODEL ..."
    if ollama pull "$MODEL"; then
        echo "  $MODEL: OK"
    else
        echo "  $MODEL: FAILED (will try to continue)"
        FAILED+=("$MODEL")
    fi
    echo ""
done

# ── 5. Summary ─────────────────────────────────────────────────────────────

echo "=================================================="
if [ ${#FAILED[@]} -eq 0 ]; then
    echo "  Setup complete! All models are ready."
    echo ""
    echo "  Option A -- Tray app (recommended):"
    echo "    python tray.py"
    echo "    (sits in your system tray, starts nodes automatically,"
    echo "     checks for updates every 6 hours)"
    echo ""
    echo "  Option B -- Terminal mode:"
    echo "    ./start.sh        <- launch all nodes"
    echo "    python ask.py     <- talk to the network"
else
    echo "  Setup finished with warnings."
    echo "  Failed to pull: ${FAILED[*]}"
    echo "  Check model names in your experts/ YAML files."
    echo "  Then re-run: ./setup.sh"
fi
echo "=================================================="
echo ""
