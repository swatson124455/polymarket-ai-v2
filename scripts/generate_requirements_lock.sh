#!/usr/bin/env bash
# Generate pinned requirements.lock from VPS virtualenv.
# Run ON VPS: bash scripts/generate_requirements_lock.sh
# Then copy requirements.lock to local repo.
set -euo pipefail

VENV="/opt/pa2-shared/venv"
if [ ! -d "$VENV" ]; then
    echo "ERROR: VPS venv not found at $VENV"
    exit 1
fi

source "$VENV/bin/activate"
pip freeze > requirements.lock
echo "Generated requirements.lock with $(wc -l < requirements.lock) pinned packages"
echo "Copy to local repo: scp ubuntu@34.251.224.21:/opt/polymarket-ai-v2/requirements.lock ."
