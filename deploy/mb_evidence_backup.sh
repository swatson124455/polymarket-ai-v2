#!/usr/bin/env bash
# MB evidence backup (operator directive 2026-08-25 item 5): nightly bundle
# of the lane's decision-bearing artifacts. Two tiers:
#   tier1 (small, critical): locks, ledger, band/readout logs, bidsim sinks,
#          verdict dirs, fee/label caches metadata - full copies
#   tier2 (large): the two shadow sinks, gzipped
# Bundles land in /opt/pa2-backups/mb_evidence/ (same box - the OFF-BOX leg
# is the Windows-side daily pull; a cloud bucket needs operator credentials
# and is the flagged upgrade path). Retention 14 days.
set -uo pipefail
TS=$(date -u +%Y%m%d)
BASE=/opt/pa2-backups/mb_evidence
OUT=$BASE/mb_evidence_$TS.tar.gz
mkdir -p "$BASE"
D=/opt/pa2-shared
DD=$D/mb_copyable_data
tar -czf "$OUT.tmp" \
  --ignore-failed-read \
  "$DD/chain_audit.json" \
  "$DD/deep_dive/verdict_locks.json" \
  "$DD/deep_dive/band_lock.json" \
  "$DD/deep_dive/cohort5_qual_locks.json" \
  "$DD/deep_dive/label_fee_refresh.log" \
  "$DD/deep_dive/shadow_readout_log.txt" \
  $DD/deep_dive/0x*.json \
  $DD/deep_dive_rereview/0x*.json \
  $DD/deep_dive_scout/0x*.json \
  "$DD/copyable_cache/fee_map.json" \
  "$DD/copyable_cache/fee_rate_map.json" \
  "$D/mirror3_bidsim.jsonl" \
  $D/mirror3_bidsim.jsonl.pre-* \
  "$D/mirror3_shadow.jsonl" \
  "$D/mirror3_shadow_rtds.jsonl" \
  2>/dev/null
mv "$OUT.tmp" "$OUT"
SZ=$(du -h "$OUT" | cut -f1)
N=$(tar -tzf "$OUT" | wc -l)
[ "$N" -gt 5 ] || { echo "[$(date -u +%FT%TZ)] FATAL: bundle has only $N entries"; exit 4; }
# gamma_resolutions.json (82MB) weekly only (Sunday) - it is rebuilt daily by cron anyway
if [ "$(date -u +%u)" = "7" ]; then
  cp "$DD/copyable_cache/gamma_resolutions.json" "$BASE/gamma_resolutions_$TS.json" 2>/dev/null || true
fi
ls -1t "$BASE"/mb_evidence_*.tar.gz | tail -n +15 | xargs -r rm -f
ls -1t "$BASE"/gamma_resolutions_*.json 2>/dev/null | tail -n +5 | xargs -r rm -f
echo "[$(date -u +%FT%TZ)] mb_evidence backup OK: $OUT ($SZ, $N entries)"
