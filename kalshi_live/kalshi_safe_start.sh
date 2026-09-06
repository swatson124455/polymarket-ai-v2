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

# 1b) A3b RAMP-FLOOR SESSION marker: an acked config change (or first-ever start)
#     runs its first session at the ramp floor — now CODE-ENFORCED (quoter reads the
#     marker; accumulating quotes held at D3_RUNGS[0]). A clean start with no drift
#     removes it. Crash-restarts mid-first-session keep the floor (marker survives).
if [ "$ACK" = "--ack-config-change" ]; then
  touch "$LIVE/RAMP_FLOOR_SESSION"
  echo "ramp-floor session ARMED (config change acked) -> $LIVE/RAMP_FLOOR_SESSION"
else
  rm -f "$LIVE/RAMP_FLOOR_SESSION"
fi

# 1c) fresh watch window + loss baseline for the continuous fill-watch (A4ii): the
#     files persist across watcher crash-restarts; each sanctioned start re-bases them.
date -u +%Y-%m-%dT%H:%M > "$LIVE/fillwatch.since"
rm -f "$LIVE/fillwatch.baseline"

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

# 4) the CONTINUOUS fill-watch (A4ii) is a systemd companion unit — enable + start it,
#    then VERIFY it is actually active (fail-closed if not). systemd self-heals a
#    later watcher death (Restart=always), closing the arm-time-only residual.
WSVC=polymarket-maker-kalshi-fillwatch
systemctl enable --now "$WSVC"
sleep 5
if [ "$(systemctl is-active "$WSVC")" != "active" ]; then
  echo ">>> FILL-WATCH UNIT NOT ACTIVE — fail-closed: stopping $SVC."
  journalctl -u "$WSVC" -n 20 --no-pager || true
  systemctl stop "$SVC"
  echo ">>> service stopped; fix the watcher, then rerun safe_start."
  exit 1
fi
echo "continuous fill-watch ACTIVE ($WSVC; since $(cat "$LIVE/fillwatch.since"); auto-STOP on realized_est <= -\$5)"
echo "=== SAFE START done; service active, watch running ==="
