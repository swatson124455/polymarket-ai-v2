#!/usr/bin/env bash
# KALSHI RESTART BUNDLE — runs ONLY on an explicit operator "restart" naming.
# Usage (from the lane worktree, operator-authorized):
#   bash kalshi_live/restart_bundle.sh "D3_RAMP=1 D2_FEEDBACK=1"     # flags to arm
# What it does, in order (idempotent, everything backed up):
#   1. md5-verifies then deploys the dark-built files from git HEAD blobs (LF, git show):
#      maker_kalshi_quoter.py  kalshi_capital_rank.py  kalshi_credit_feedback.py
#   2. Rebuilds kalshi_credit_feedback.json on the VPS (the W7 clamp binds without it —
#      deliberate fail-closed, but a fresh feed is the intended state; review F1).
#   3. Appends the operator-named KALSHI_* flags to live.env (backup first).
#   4. Archives STOP (the operator naming IS the authorization), restarts the service,
#      and runs w1_restart_checkpoint.py --watch-min 70 (ledger timer is HOURLY at :16 —
#      the 08-04 watcher's 25-min patience was the miscalibration).
# NEVER run without the operator's restart naming. The script refuses without an ACK arg.
set -euo pipefail
FLAGS="${1:?usage: restart_bundle.sh \"D3_RAMP=1 ...\" OPERATOR-RESTART-ACK}"
ACK="${2:?second arg must be OPERATOR-RESTART-ACK — operator naming required}"
[ "$ACK" = "OPERATOR-RESTART-ACK" ] || { echo "refusing: no operator ack"; exit 2; }
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@18.201.216.0"
TAG="RESTART2-$(date -u +%Y%m%d_%H%M%S)"
FILES="maker_kalshi_quoter.py kalshi_capital_rank.py kalshi_credit_feedback.py"

for f in $FILES; do
  LOCAL_MD5=$(git show "HEAD:kalshi_live/$f" | md5sum | cut -d' ' -f1)
  git show "HEAD:kalshi_live/$f" | ssh -i "$KEY" "$VPS" \
    "sudo -n bash -c 'cp /opt/pa2-maker-kalshi-live/$f /opt/pa2-maker-kalshi-live/$f.bak-$TAG 2>/dev/null; cat > /opt/pa2-maker-kalshi-live/$f && md5sum /opt/pa2-maker-kalshi-live/$f'" \
    | grep -q "$LOCAL_MD5" || { echo "MD5 MISMATCH on $f — aborting"; exit 1; }
  echo "deployed $f ($LOCAL_MD5)"
done

ssh -i "$KEY" "$VPS" sudo -n bash -s <<EOS
set -e
cd /opt/pa2-maker-kalshi-live && set -a && . ./live.env && set +a
PYTHONPATH=/opt/pa2-maker-kalshi-live ./venv/bin/python kalshi_credit_feedback.py \
  --out /opt/pa2-maker-kalshi-live/kalshi_credit_feedback.json
cp live.env "live.env.bak-$TAG"
for kv in $FLAGS; do
  k="KALSHI_\${kv%%=*}"; v="\${kv#*=}"
  grep -q "^\$k=" live.env && sed -i "s/^\$k=.*/\$k=\$v/" live.env || echo "\$k=\$v" >> live.env
  echo "armed \$k=\$v"
done
TS=\$(date -u +%Y%m%d_%H%M%S)
[ -f STOP ] && mv STOP "STOP.cleared-\$TS"
# A7 (operator-ruled 2026-08-05): an operator-NAMED restart re-baselines the daily-loss
# governor at current equity (quoter consumes this marker once, first untorn cycle), so
# the governor's day and the fresh P2 window agree — no more manual halt-absorption.
touch day_baseline_reset && chown polymarket:polymarket day_baseline_reset || true
systemctl restart polymarket-maker-kalshi-ws.service
SINCE=\$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "restarted_at=\$SINCE"
nohup ./venv/bin/python -u w1_restart_checkpoint.py --since "\$SINCE" --watch-min 70 \
  > /tmp/w1_watch_\$TS.log 2>&1 &
echo "checkpoint watcher: /tmp/w1_watch_\$TS.log"
EOS
echo "BUNDLE COMPLETE — watch the checkpoint log above."
