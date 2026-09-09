#!/usr/bin/env bash
# 2026-09-09 (re-review alt#6): the dive runs from the READOUT CLONE (master),
# never the watcher's frozen /opt/mirror3 checkout - one rule set per dossier dir.
# Promotion dive queue — the 5 remaining lower-bound-proven wallets
# (2026-09-08 relaunch after the 09-07 queue wedged for 15.6h).
#
# TWO LANDMINES THIS SCRIPT EXISTS TO AVOID (both hit for real):
#  1. SELF-MATCHING pgrep. The 09-07 launcher wrapped the dive in
#     `while pgrep -f "chain_deep_div[e].py"; do sleep 600; done`. Because the
#     wrapper's OWN cmdline contained the literal `chain_deep_dive.py` (the
#     command it was about to run), the bracket trick did not save it: pgrep -f
#     matched the wrapper itself and it waited on itself forever — 0% CPU,
#     0-byte log, 15.6h lost. Any wait-for-peer loop MUST exclude self by PID,
#     never by cmdline pattern alone. This script uses $$ exclusion.
#  2. CREDENTIAL IN THE PROCESS LIST. Launching via
#     `bash -c "... DATABASE_URL=$(...) python ..."` expands the secret into a
#     world-readable cmdline (visible to any `ps`). Read it INSIDE the script,
#     as the scout queues already do, so it stays in the environment only.
#
# Verdicts are PROPOSALS ONLY — a roster add is always an operator decision.
set -uo pipefail
cd /opt/polymarket-ai-v2
PY=/opt/polymarket-ai-v2/venv/bin/python
CACHE=/opt/pa2-shared/mb_copyable_data/copyable_cache
OUT=/opt/pa2-shared/mb_copyable_data/deep_dive_promo0907
ROSTER=/tmp/promo_roster_0907c.txt
LOG_TS() { date -u +%FT%TZ; }

[ -s "$ROSTER" ] || { echo "FATAL: empty/missing roster $ROSTER" >&2; exit 2; }
echo "[$(LOG_TS)] promo queue 0908 launch: $(wc -l < "$ROSTER") candidates"

# wait for any OTHER dive, excluding this script and its children by PID
while :; do
  others=$(pgrep -f "chain_deep_dive[.]py" | grep -vx "$$" | grep -vx "$PPID" || true)
  [ -z "$others" ] && break
  echo "[$(LOG_TS)] another dive is running (pids: $others) — waiting 600s"
  sleep 600
done

touch "$OUT/.wtest" && rm -f "$OUT/.wtest" || { echo "FATAL: $OUT not writable" >&2; exit 3; }

# secret read here, never on a command line
DBURL=$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2-)
[ -n "$DBURL" ] || { echo "FATAL: no DATABASE_URL" >&2; exit 4; }
export DATABASE_URL="$DBURL" PYTHONPATH=/opt/pa2-shared/mb_readout

"$PY" /opt/pa2-shared/mb_readout/scripts/chain_deep_dive.py \
  --extra-traders "$ROSTER" --cache "$CACHE" \
  --gamma-cache "$CACHE/gamma_resolutions.json" \
  --rpc-url https://polygon.gateway.tenderly.co --rps 8 --max-receipts 30000 \
  --fill-cache-dir "$CACHE/chain_fills" \
  --out-dir "$OUT" --out "$OUT/_summary_promo_c.json"
RC=$?
echo "[$(LOG_TS)] promo queue 0908 rc=$RC | dossiers in dir: $(ls -1 "$OUT"/0x*.json 2>/dev/null | wc -l)"
echo "[$(LOG_TS)] COMPLETE (verdicts are PROPOSALS ONLY)"
