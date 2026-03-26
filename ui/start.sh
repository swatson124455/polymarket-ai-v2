#!/bin/bash
# Launch the Polymarket AI Dashboard
# Usage: bash ui/start.sh
#    or: cd ui && bash start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT"

# Install uvicorn if missing
python3 -c "import uvicorn" 2>/dev/null || pip3 install uvicorn

PORT="${DASHBOARD_PORT:-8050}"

echo ""
echo "  ================================================"
echo "  Polymarket AI Dashboard"
echo "  ================================================"
echo ""
echo "  Open in browser: http://localhost:$PORT"
echo "  Press Ctrl+C to stop"
echo ""

python3 -m uvicorn ui.app:app --host 0.0.0.0 --port "$PORT" --log-level info
