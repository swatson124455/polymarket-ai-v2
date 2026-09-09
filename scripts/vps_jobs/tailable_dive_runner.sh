#!/usr/bin/env bash
# TAILABLE DIVE RUNNER - drains the candidate pipeline's queue SERIALLY
# (docs/MB_TAILABLE_PLAN.md P4). Cron (polymarket), after the 11:40Z chain:
#   0 13 * * * bash /opt/pa2-shared/mb_readout/scripts/vps_jobs/tailable_dive_runner.sh \
#       >> /opt/pa2-shared/mb_copyable_data/deep_dive/tailable_dive_runner.log 2>&1
#
# Queue  : mb_copyable_data/tailable/dive_queue.txt (mb_candidate_pipeline.py
#          appends; this script removes what it dived)
# Output : mb_copyable_data/deep_dive_pipeline/<addr>.json - the tailable
#          list + the grader's registration scan this dir; an ADMIT lands on
#          the list as VERIFIED automatically. ROSTER ADD = OPERATOR RULING.
#
# THE TWO LANDMINES (both hit for real, 2026-09-07/08):
#  1. SELF-MATCHING pgrep: a wait loop whose own cmdline contains the pattern
#     waits on itself forever (15h38m lost). Exclude by PID ($$, $PPID), never
#     by pattern alone.
#  2. CREDENTIAL IN THE PROCESS LIST: read DATABASE_URL INSIDE the script.
set -uo pipefail
cd /opt/polymarket-ai-v2
PY=/opt/polymarket-ai-v2/venv/bin/python
BASE=/opt/pa2-shared/mb_copyable_data
CACHE=$BASE/copyable_cache
OUT=$BASE/deep_dive_pipeline
QUEUE=$BASE/tailable/dive_queue.txt
MAXN=${TAILABLE_DIVE_MAXN:-3}      # dives per run (serial; ~10-40 min each)
TS() { date -u +%FT%TZ; }

[ -s "$QUEUE" ] || { echo "[$(TS)] queue empty - nothing to dive"; exit 0; }
mkdir -p "$OUT" || { echo "[$(TS)] FATAL: cannot create $OUT" >&2; exit 3; }
touch "$OUT/.wtest" && rm -f "$OUT/.wtest" || { echo "[$(TS)] FATAL: $OUT not writable" >&2; exit 3; }

# take the first MAXN valid addresses
BATCH=$(mktemp /tmp/tailable_dive_batch.XXXXXX)
grep -E '^0x[0-9a-fA-F]{40}$' "$QUEUE" | head -n "$MAXN" | tr 'A-F' 'a-f' > "$BATCH"
N=$(wc -l < "$BATCH")
[ "$N" -gt 0 ] || { echo "[$(TS)] queue holds no valid addresses"; rm -f "$BATCH"; exit 0; }
echo "[$(TS)] tailable dive runner: $N of $(grep -c . "$QUEUE") queued -> $OUT"

# wait for any OTHER dive (PID-excluded), bounded so cron never stacks
waited=0
while :; do
  others=$(pgrep -f "chain_deep_dive[.]py" | grep -vx "$$" | grep -vx "$PPID" || true)
  [ -z "$others" ] && break
  [ "$waited" -ge 7200 ] && { echo "[$(TS)] another dive still running after 2h (pids: $others) - giving up this run, queue untouched"; rm -f "$BATCH"; exit 0; }
  echo "[$(TS)] another dive is running (pids: $others) - waiting 600s"
  sleep 600; waited=$((waited + 600))
done

# secret read here, never on a command line
DBURL=$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2-)
[ -n "$DBURL" ] || { echo "[$(TS)] FATAL: no DATABASE_URL" >&2; rm -f "$BATCH"; exit 4; }
# the dive runs from the READOUT CLONE (master, refreshed 12:30Z) - the
# deploy path is merge-to-master; /opt/mirror3 is the watcher's pinned
# checkout and is not touched (ruling 2026-09-09; dive files md5-identical
# on both at the switch)
D=/opt/pa2-shared/mb_readout
export DATABASE_URL="$DBURL" PYTHONPATH="$D" PYTHONDONTWRITEBYTECODE=1

START=$(date +%s)
"$PY" "$D/scripts/chain_deep_dive.py" \
  --extra-traders "$BATCH" --cache "$CACHE" \
  --gamma-cache "$CACHE/gamma_resolutions.json" \
  --rpc-url https://polygon.gateway.tenderly.co --rps 8 --max-receipts 30000 \
  --fill-cache-dir "$CACHE/chain_fills" \
  --out-dir "$OUT" --out "$OUT/_summary_$(date -u +%Y%m%d).json"
RC=$?
echo "[$(TS)] dive rc=$RC | dossiers in dir: $(ls -1 "$OUT"/0x*.json 2>/dev/null | wc -l)"

# remove ONLY addresses with a FRESH dossier (mtime >= this run's start):
# a crash leaves them queued, and a RE-DIVE whose dive died must not be
# 'completed' by the older dossier (re-review 2026-09-09 A2/B6). Batch
# addresses left without a fresh dossier are ROTATED to the back so a
# persistently failing wallet never wedges the head of the queue (C5).
# Log lines go to stderr - the loop's stdout IS the new queue file (A1).
TMPQ=$(mktemp /tmp/tailable_dive_queue.XXXXXX)
ROTATE=$(mktemp /tmp/tailable_dive_rotate.XXXXXX)
while read -r a; do
  a=$(echo "$a" | tr 'A-F' 'a-f')
  if grep -qx "$a" "$BATCH"; then
    if [ -f "$OUT/$a.json" ] && [ "$(stat -c %Y "$OUT/$a.json")" -ge "$START" ]; then
      echo "[$(TS)] dived $a -> $(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('verdict'))" "$OUT/$a.json" 2>/dev/null)" >&2
    else
      echo "[$(TS)] NOT dived $a (no fresh dossier; dive rc=$RC) - moved to the back of the queue" >&2
      echo "$a" >> "$ROTATE"
    fi
  else
    echo "$a"
  fi
done < "$QUEUE" > "$TMPQ"
cat "$ROTATE" >> "$TMPQ"
mv "$TMPQ" "$QUEUE"
rm -f "$BATCH" "$ROTATE"
echo "[$(TS)] COMPLETE - queue left: $(grep -c . "$QUEUE") (verdicts are PROPOSALS ONLY; roster add = operator ruling)"
