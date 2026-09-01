#!/bin/bash
# Nightly backup of the Maker recorder arms' DATA files (never code/venvs).
# Read-only on the arm dirs; writes ONLY /opt/pa2-maker-backups (also
# kernel-enforced by the unit's ProtectSystem=strict + ReadWritePaths).
# Keeps last 7 dailies; hard disk guard at 1500MB.
# Review fixes 2026-07-17: v5 dir added; tar rc=1 ("file changed as we read
# it" — expected against live writers) no longer discards the night; per-dir
# file counts printed so a silently-empty glob is visible in the log.
set -u
DEST=/opt/pa2-maker-backups
DAY=$(date -u +%Y%m%d)
OUT="$DEST/maker-data-$DAY.tar.gz"
cd / || exit 1
FILES=$(ls opt/pa2-maker-live/state.json opt/pa2-maker-live/universe.json opt/pa2-maker-live/ledgers/* \
           opt/pa2-maker-sim/state.json opt/pa2-maker-sim/samples-* \
           opt/pa2-maker-sim-v2/state.json opt/pa2-maker-sim-v2/samples-* \
           opt/pa2-maker-sim-v3/state.json opt/pa2-maker-sim-v3/universe.json opt/pa2-maker-sim-v3/samples-* \
           opt/pa2-maker-sim-v4/state.json opt/pa2-maker-sim-v4/universe.json opt/pa2-maker-sim-v4/samples-* \
           opt/pa2-maker-sim-v5/state.json opt/pa2-maker-sim-v5/universe.json opt/pa2-maker-sim-v5/samples-* \
           opt/pa2-maker-sim-v6/state.json opt/pa2-maker-sim-v6/universe.json opt/pa2-maker-sim-v6/samples-* \
           opt/pa2-maker-kalshi/state.json opt/pa2-maker-kalshi/samples-* opt/pa2-maker-kalshi/census-* \
           opt/pa2-maker-sensor/state.json opt/pa2-maker-feeds/wb_forecasts.jsonl opt/pa2-maker-feeds/informed_flow.jsonl \
           opt/pa2-maker-census/census-* 2>/dev/null)
[ -z "$FILES" ] && { echo "backup FAIL: no data files found"; exit 1; }
COUNTS=$(echo "$FILES" | awk -F/ '{print $2}' | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}')
tar czf "$OUT.tmp" --warning=no-file-changed --ignore-failed-read $FILES
RC=$?
if [ "$RC" -gt 1 ]; then
    rm -f "$OUT.tmp"; echo "backup FAIL: tar rc=$RC"; exit 1
fi
mv "$OUT.tmp" "$OUT"
ls -1t "$DEST"/maker-data-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
TOT=$(du -sm "$DEST" | cut -f1)
if [ "$TOT" -gt 1500 ]; then
    ls -1t "$DEST"/maker-data-*.tar.gz | tail -n +4 | xargs -r rm -f
    echo "backup WARN: disk guard trimmed to 3 newest (was ${TOT}MB)"
fi
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1)), files per dir: $COUNTS, kept $(ls "$DEST" | grep -c tar.gz), ${TOT}MB total"
