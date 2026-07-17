#!/bin/bash
# Nightly backup of the Maker recorder arms' DATA files (never code/venvs).
# Read-only on the arm dirs; writes ONLY /opt/pa2-maker-backups (also
# kernel-enforced by the unit's ProtectSystem=strict + ReadWritePaths).
# Keeps last 7 dailies; hard disk guard at 1500MB.
set -u
DEST=/opt/pa2-maker-backups
DAY=$(date -u +%Y%m%d)
OUT="$DEST/maker-data-$DAY.tar.gz"
cd / || exit 1
FILES=$(ls opt/pa2-maker-sim/state.json opt/pa2-maker-sim/samples-* \
           opt/pa2-maker-sim-v2/state.json opt/pa2-maker-sim-v2/samples-* \
           opt/pa2-maker-sim-v3/state.json opt/pa2-maker-sim-v3/universe.json opt/pa2-maker-sim-v3/samples-* \
           opt/pa2-maker-sim-v4/state.json opt/pa2-maker-sim-v4/universe.json opt/pa2-maker-sim-v4/samples-* \
           opt/pa2-maker-census/census-* 2>/dev/null)
[ -z "$FILES" ] && { echo "backup FAIL: no data files found"; exit 1; }
tar czf "$OUT.tmp" $FILES || { rm -f "$OUT.tmp"; echo "backup FAIL: tar error"; exit 1; }
mv "$OUT.tmp" "$OUT"
ls -1t "$DEST"/maker-data-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
TOT=$(du -sm "$DEST" | cut -f1)
if [ "$TOT" -gt 1500 ]; then
    ls -1t "$DEST"/maker-data-*.tar.gz | tail -n +4 | xargs -r rm -f
    echo "backup WARN: disk guard trimmed to 3 newest (was ${TOT}MB)"
fi
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1)), kept $(ls "$DEST" | grep -c tar.gz), ${TOT}MB total"
