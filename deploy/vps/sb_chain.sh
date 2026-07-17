#!/bin/bash
# Sports-bot (SB): babysit the Phase-1 index lanes, then launch Phase-2 once.
#
# Watches the three parallel index-harvest lanes (a: nfl,nhl,nba,mlb;
# b: tennis; c: soccer). If a lane process dies before its sports are all
# ":done" in its state file (server flake -> GAVE UP), restarts it — max 20
# restarts total, so a hard API outage can't spin forever. When ALL sports are
# done, launches the Phase-2 per-event odds harvester (sb_owls_history_odds.py)
# exactly once and exits.
#
# Run detached:  nohup bash sb_chain.sh >> sb_chain.log 2>&1 &
cd /home/ubuntu/sports-odds || exit 1
RESTARTS=0
log() { echo "$(date -u +%FT%TZ) $*"; }

lane_done() { # $1=state file, $2=csv sports -> 0 iff every sport marked done
  local s
  for s in $(echo "$2" | tr , " "); do
    grep -q "\"$s:done\": true" "$1" 2>/dev/null || return 1
  done
  return 0
}

while true; do
  if ! pgrep -f "python3 sb_owls_[h]istory_index" >/dev/null; then
    all=1
    for spec in "sb_state_a.json nfl,nhl,nba,mlb" \
                "sb_state_b.json tennis" \
                "sb_state_c.json soccer"; do
      set -- $spec
      if ! lane_done "$1" "$2"; then
        all=0
        if [ "$RESTARTS" -lt 20 ]; then
          RESTARTS=$((RESTARTS + 1))
          log "restarting lane $1 ($2) restart#$RESTARTS"
          SB_SPORTS="$2" SB_STATE="/home/ubuntu/sports-odds/$1" \
            nohup python3 sb_owls_history_index.py \
            >> "sb_history_index_${1:9:1}.log" 2>&1 &
        else
          log "restart cap (20) reached — giving up; investigate manually"
          exit 1
        fi
      fi
    done
    if [ "$all" -eq 1 ]; then
      nohup python3 sb_owls_history_odds.py >> sb_history_odds.log 2>&1 &
      log "all index lanes done — phase2 odds harvester launched"
      exit 0
    fi
  fi
  sleep 300
done
