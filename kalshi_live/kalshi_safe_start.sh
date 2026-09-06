#!/usr/bin/env bash
# KALSHI SAFE START — the ONLY sanctioned way to start the maker.
# Runs preflight (hard gate); starts ONLY on PASS; snapshots the config baseline
# for the config-change guard; then arms the first-hours fill-watch.
#
# Usage (on box):  sudo bash kalshi_safe_start.sh [--ack-config-change]
# Refuses to start on any preflight FAIL. Never bypasses the STOP sentinel.
set -euo pipefail
LIVE=/opt/pa2-maker-kalshi-live
SVC=polymarket-maker-kalshi-ws
PY=$LIVE/venv/bin/python
ACK="${1:-}"

echo "=== SAFE START $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1) preflight gate (env loaded so the quoter module reads real knobs)
set -a; source "$LIVE/live.env"; set +a
cd "$LIVE"
if ! "$PY" "$LIVE/kalshi_preflight.py" $ACK; then
  echo ">>> PREFLIGHT FAILED — NOT starting. Resolve the FAIL line(s) above."
  exit 1
fi

# 2) start
systemctl enable --now "$SVC"
sleep 6
if [ "$(systemctl is-active "$SVC")" != "active" ]; then
  echo ">>> service did not come active — check journalctl -u $SVC"
  exit 1
fi

# 3) snapshot the config baseline (config-change guard reads this next time)
cp "$LIVE/live.env" "$LIVE/live.env.last_started"
echo "config baseline snapshotted -> live.env.last_started"

# 4) arm the first-hours fill-watch in the background (read-only)
SINCE=$(date -u +%Y-%m-%dT%H:%M)
nohup "$PY" "$LIVE/kalshi_fill_watch.py" --since "$SINCE" --hours 2 --interval 60 \
  >> "$LIVE/fill_watch.log" 2>&1 &
echo "fill-watch armed (2h, 60s) -> $LIVE/fill_watch.log  (since $SINCE)"
echo "=== SAFE START done; service active, watch running ==="
