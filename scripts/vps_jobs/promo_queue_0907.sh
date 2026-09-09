#!/usr/bin/env bash
# 2026-09-09 (re-review alt#6): the dive runs from the READOUT CLONE (master),
# never the watcher's frozen /opt/mirror3 checkout - one rule set per dossier dir.
# Promotion-screen dive queue (2026-09-07, operator "2 do it / 4 proceed
# as needed"): chain deep-dives for the corrected-board QUALIFIES wallets
# that PASSED the 1-month data-api eligibility read (16:36:38Z). Follows
# the scout_queue3 pattern: waits for any running dive, serial, verdicts
# are PROPOSALS ONLY — roster adds remain operator-gated.
set -uo pipefail
cd /opt/polymarket-ai-v2
PY=/opt/polymarket-ai-v2/venv/bin/python
CACHE=/opt/pa2-shared/mb_copyable_data/copyable_cache
OUT=/opt/pa2-shared/mb_copyable_data/deep_dive_promo0907
ROSTER=/tmp/promo_roster_0907b.txt
DBURL=$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2-)
echo "[$(date -u +%FT%TZ)] promo queue launch: $(wc -l < $ROSTER) candidates"
while pgrep -f "chain_deep_div[e].py" >/dev/null 2>&1; do sleep 600; done
touch "$OUT/.wtest" && rm -f "$OUT/.wtest" || { echo "FATAL: $OUT not writable" >&2; exit 3; }
PYTHONPATH=/opt/pa2-shared/mb_readout DATABASE_URL="$DBURL" "$PY" /opt/pa2-shared/mb_readout/scripts/chain_deep_dive.py \
  --extra-traders "$ROSTER" --cache "$CACHE" --gamma-cache "$CACHE/gamma_resolutions.json" \
  --rpc-url https://polygon.gateway.tenderly.co --rps 8 --max-receipts 30000 \
  --fill-cache-dir "$CACHE/chain_fills" \
  --out-dir "$OUT" --out "$OUT/_summary_promo_b.json"
RC=$?
N=$(ls -1 "$OUT"/0x*.json 2>/dev/null | wc -l)
echo "[$(date -u +%FT%TZ)] promo queue rc=$RC total JSONs in dir: $N"
echo "[$(date -u +%FT%TZ)] PROMO QUEUE COMPLETE (verdicts are PROPOSALS ONLY)"
