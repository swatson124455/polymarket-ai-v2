#!/usr/bin/env bash
# Scout sweep #2 (2026-08-25, operator rec 6): first sweep on the FIXED
# selection band (10<=trades/6h<250, mkts>=5, notional>=$25k) - 82
# human-scale candidates from the 07-30 capture. Serial dives ~days;
# verdicts are PROPOSALS ONLY. Waits for any running dive first.
set -uo pipefail
cd /opt/polymarket-ai-v2
PY=/opt/polymarket-ai-v2/venv/bin/python
CACHE=/opt/pa2-shared/mb_copyable_data/copyable_cache
OUT=/opt/pa2-shared/mb_copyable_data/deep_dive_scout2
ROSTER=/tmp/scout_dive_roster2.txt
DBURL=$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2-)
echo "[$(date -u +%FT%TZ)] sweep2 launch: $(wc -l < $ROSTER) candidates"
while pgrep -f "chain_deep_div[e].py" >/dev/null 2>&1; do sleep 600; done
touch "$OUT/.wtest" && rm -f "$OUT/.wtest" || { echo "FATAL: $OUT not writable" >&2; exit 3; }
PYTHONPATH=/opt/mirror3 DATABASE_URL="$DBURL" "$PY" /opt/mirror3/scripts/chain_deep_dive.py \
  --extra-traders "$ROSTER" --cache "$CACHE" --gamma-cache "$CACHE/gamma_resolutions.json" \
  --rpc-url https://polygon.gateway.tenderly.co --rps 8 --max-receipts 30000 \
  --fill-cache-dir "$CACHE/chain_fills" \
  --out-dir "$OUT" --out "$OUT/_summary_sweep2.json" > /tmp/deep_dive_sweep2.log 2>&1
RC=$?
N=$(ls -1 "$OUT"/0x*.json 2>/dev/null | wc -l)
echo "[$(date -u +%FT%TZ)] sweep2 rc=$RC produced $N/$(wc -l < $ROSTER)"
[ "$N" -eq 0 ] && { echo "FATAL: ZERO JSONs" >&2; exit 4; }
echo "[$(date -u +%FT%TZ)] SWEEP2 COMPLETE (verdicts are PROPOSALS ONLY)"
