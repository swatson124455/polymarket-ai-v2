#!/usr/bin/env bash
# esports_silo — throwaway probe of the pinnodds (Pinnacle wrapper) API.
# Goal: discover the real response schema + confirm esports coverage BEFORE we
# rewire the collector. Read-only; hits a few endpoints and dumps raw JSON heads.
#
# USAGE (on the box — it has network; the silo does not):
#   PINNODDS_KEY=your_key bash esports_silo/scripts/probe_pinnodds.sh
#
# The key is only sent to pinnodds and printed nowhere. Rotate it after — it has
# been shared in chat.
set -u
KEY="${PINNODDS_KEY:-${1:-}}"
[ -n "$KEY" ] || { echo "usage: PINNODDS_KEY=xxxx bash esports_silo/scripts/probe_pinnodds.sh"; exit 2; }

BASE="https://pinnodds.com/kit/v1"
AUTH=(-H "x-portal-apikey: $KEY" -H "accept: application/json")
PP='import sys,json
raw=sys.stdin.read()
try:
    d=json.loads(raw)
except Exception:
    print("(non-JSON, first 600 chars):"); print(raw[:600]); sys.exit()
print(json.dumps(d, indent=1)[:2200])'

hit() {  # $1 = label   $2 = path(+query)
  printf '\n\033[1;36m===== %s =====\033[0m\n' "$1"
  local code
  code=$(curl -s -o /tmp/_pinn.$$ -w '%{http_code}' "${AUTH[@]}" "$BASE/$2")
  echo "HTTP $code   $BASE/$2"
  [ "$code" = "200" ] && python3 -c "$PP" < /tmp/_pinn.$$ || head -c 600 /tmp/_pinn.$$
  echo
}

# 1) what sports exist + their ids (find the esports id)
hit "sports list" "sports"

# 2) esports guesses — Pinnacle's canonical esports sport_id is 12; try a few shapes
hit "markets sport_id=12 prematch" "markets?sport_id=12&event_type=prematch"
hit "markets sport_id=12 live"     "markets?sport_id=12&event_type=live"
hit "leagues sport_id=12"          "leagues?sport_id=12"

rm -f /tmp/_pinn.$$ 2>/dev/null
echo "---"
echo "Paste this whole output back. I only need the SHAPE (fields + esports league/team names)"
echo "to wire the collector; the actual odds values don't matter here."
