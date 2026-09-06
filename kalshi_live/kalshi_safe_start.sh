#!/usr/bin/env bash
# KALSHI SAFE START — the ONLY sanctioned way to start the maker.
# Runs preflight (hard gate); starts ONLY on PASS; snapshots the config baseline
# for the config-change guard; then arms the first-hours fill-watch and VERIFIES
# the watcher is actually alive (blind-review hole A1) — if it is not, the service
# is stopped again (fail-closed: "active AND watched" is the contract).
#
# Usage (on box):  sudo bash kalshi_safe_start.sh [--ack-config-change]
# Refuses to start on any preflight FAIL. Never bypasses the STOP sentinel.
# Config drift since the last clean start (or a first start) FAILs preflight
# unless --ack-config-change is passed (hole A3a fix); the ack is materialized
# as $LIVE/CONFIG_CHANGE_ACK so the systemd ExecStartPre gate (hole A2 fix)
# honors it too, and is consumed only after the baseline snapshot (hole N1 fix:
# an ungated start can never launder drift into the baseline).
set -euo pipefail
LIVE=/opt/pa2-maker-kalshi-live
SVC=polymarket-maker-kalshi-ws
PY=$LIVE/venv/bin/python
ACK="${1:-}"

echo "=== SAFE START $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 0) materialize the ack for the ExecStartPre gate (removed after snapshot below)
if [ "$ACK" = "--ack-config-change" ]; then
  touch "$LIVE/CONFIG_CHANGE_ACK"
fi

# 1) preflight gate (env loaded so the quoter module reads real knobs)
set -a; source "$LIVE/live.env"; set +a
cd "$LIVE"
if ! "$PY" "$LIVE/kalshi_preflight.py" $ACK; then
  rm -f "$LIVE/CONFIG_CHANGE_ACK"   # never leave a stale ack behind a failed gate
  echo ">>> PREFLIGHT FAILED — NOT starting. Resolve the FAIL line(s) above."
  exit 1
fi

# 2) start (the unit's ExecStartPre re-runs preflight --pre-start as a belt)
systemctl enable --now "$SVC"
sleep 6
if [ "$(systemctl is-active "$SVC")" != "active" ]; then
  rm -f "$LIVE/CONFIG_CHANGE_ACK"   # never leave a stale ack behind a failed start
  echo ">>> service did not come active — check journalctl -u $SVC"
  exit 1
fi

# 3) snapshot the config baseline (config-change guard reads this next time).
#    Only reachable through the gate above, so a drifted config gets here only
#    when explicitly acked — the snapshot is always legitimate (N1).
cp "$LIVE/live.env" "$LIVE/live.env.last_started"
rm -f "$LIVE/CONFIG_CHANGE_ACK"
echo "config baseline snapshotted -> live.env.last_started"

# 4) arm the first-hours fill-watch in the background (read-only) and VERIFY it
SINCE=$(date -u +%Y-%m-%dT%H:%M)
nohup "$PY" "$LIVE/kalshi_fill_watch.py" --since "$SINCE" --hours 2 --interval 60 \
  >> "$LIVE/fill_watch.log" 2>&1 &
WPID=$!
sleep 5
if ! kill -0 "$WPID" 2>/dev/null; then
  echo ">>> FILL-WATCH DIED AT ARM (PID $WPID gone) — fail-closed: stopping $SVC."
  echo ">>> last fill_watch.log lines:"
  tail -n 20 "$LIVE/fill_watch.log" || true
  systemctl stop "$SVC"
  echo ">>> service stopped; fix the watcher, then rerun safe_start."
  exit 1
fi
echo "fill-watch armed and ALIVE (PID $WPID, 2h, 60s) -> $LIVE/fill_watch.log  (since $SINCE)"
echo "NOTE: aliveness is verified at arm time only; a later watcher death is not"
echo "      auto-detected (A4ii continuous-watch remains an open operator item)."
echo "=== SAFE START done; service active, watch running ==="
