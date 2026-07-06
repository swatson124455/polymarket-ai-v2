#!/usr/bin/env bash
# esports_silo — turn ON automatic odds pulling (pinnodds → odds_raw, every 15 min).
#
# What it does, in order:
#   1. writes /opt/esports_silo/.env (silo DATABASE_URL + PINNODDS_API_KEY + carries any
#      existing PANDASCORE/RIOT keys for the results collector);
#   2. runs ONE real collection pass with --poll-all (forces every current line in) and
#      counts the odds_raw rows written — proves the DB write path end-to-end;
#   3. only if rows landed, installs + starts the systemd timer (15-min cadence).
#
# Collect-only. Trades NOTHING — SILO_ENTRY_HALT stays true. Run steps 0-4 first (DB + gate).
#
# USAGE (on the box, from the cloned repo root ~/esports_silo_src):
#   PINNODDS_API_KEY=your_key bash esports_silo/deploy/enable_autopull.sh
set -uo pipefail

DBNAME="esports_silo"; DBROLE="esports_silo"
ENV_DIR="/opt/esports_silo"; ENV_FILE="$ENV_DIR/.env"
REPO="$(pwd)"
ENV_CANDIDATES="/opt/pa2-shared/.env /opt/pa2-esports-shared/.env $HOME/.env"

say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die(){ printf '\n\033[1;31mSTOP: %s\033[0m\n' "$*" >&2; exit 2; }

[ -f esports_silo/collectors/pinnodds_collector.py ] || die "run me from the cloned repo root (~/esports_silo_src)."
[ -n "${PINNODDS_API_KEY:-}" ] || die "PINNODDS_API_KEY not set — prefix it: PINNODDS_API_KEY=xxx bash $0"

# venv python with aiohttp+asyncpg
PY=""
for c in /opt/pa2-esports-shared/venv/bin/python /opt/pa2-shared/venv/bin/python \
         /opt/polymarket-ai-v2-esports/venv/bin/python; do
  [ -x "$c" ] && "$c" -c 'import aiohttp,asyncpg' >/dev/null 2>&1 && { PY="$c"; break; }
done
[ -n "$PY" ] || die "no venv python with aiohttp+asyncpg found."
echo "python: $PY"

# carry existing PandaScore/Riot keys (results collector) from the box's shared env
grab(){ local n="$1" f v; for f in $ENV_CANDIDATES; do [ -f "$f" ] || continue
  v=$(grep -hE "^(export[[:space:]]+)?$n=" "$f" 2>/dev/null | head -1 | sed -E "s/^(export[[:space:]]+)?$n=//")
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}; [ -n "$v" ] && { printf '%s' "$v"; return; }; done; }
PANDA="$(grab PANDASCORE_API_KEY)"; RIOT="$(grab RIOT_API_KEY)"

# ── 1) silo DATABASE_URL (reset role pw so it's known) + write .env ─────────
say "1. write $ENV_FILE"
[ "$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DBROLE'")" = "1" ] \
  || die "silo role missing — run steps 0-4 first."
PW=$(openssl rand -hex 24)
sudo -u postgres psql -c "ALTER ROLE $DBROLE LOGIN PASSWORD '$PW';" >/dev/null || die "ALTER ROLE failed"
SILO_DB="postgresql://$DBROLE:$PW@localhost:5432/$DBNAME"
sudo mkdir -p "$ENV_DIR"; sudo chown "$(id -u):$(id -g)" "$ENV_DIR" 2>/dev/null || true
umask 177
cat > "$ENV_FILE" <<EOF
DATABASE_URL=$SILO_DB
PINNODDS_API_KEY=$PINNODDS_API_KEY
PINNODDS_BASE=https://pinnodds.com/kit/v1
PANDASCORE_API_KEY=$PANDA
RIOT_API_KEY=$RIOT
POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com
POLYMARKET_CLOB_API=https://clob.polymarket.com
SHARP_BOOKS=pinnacle
ESPORTS_GAMES=cs2,lol,dota2,valorant
CALIBRATOR_PATH=esports_silo/artifacts/calibrator_sharp_consensus_v1.json
SILO_BANKROLL_USD=1000
SILO_ENTRY_HALT=true
EOF
chmod 600 "$ENV_FILE"
echo "wrote $ENV_FILE (pinnacle source=pinnodds, HALT=true; panda/riot carried: ${PANDA:+yes}${PANDA:-no}/${RIOT:+yes}${RIOT:-no})"

# ── 2) one REAL write pass (force all current lines) + verify ───────────────
say "2. one real odds write (--poll-all) then count odds_raw"
before=$(psql "$SILO_DB" -tAc "SELECT count(*) FROM odds_raw WHERE aggregator='pinnodds'")
DATABASE_URL="$SILO_DB" PINNODDS_API_KEY="$PINNODDS_API_KEY" \
  "$PY" -m esports_silo.collectors.pinnodds_collector --once --poll-all || die "collector pass failed"
after=$(psql "$SILO_DB" -tAc "SELECT count(*) FROM odds_raw WHERE aggregator='pinnodds'")
echo "odds_raw(pinnodds): $before -> $after"
[ "${after:-0}" -gt "${before:-0}" ] || die "no new odds_raw rows written — do NOT enable the timer; paste the output."
psql "$SILO_DB" -c "SELECT game, count(*) FROM matches WHERE source='pinnodds' GROUP BY game ORDER BY game;"

# ── 3) install + start the 15-min timer ────────────────────────────────────
say "3. install + start the systemd timer (collect-only; 15 min)"
sudo cp esports_silo/deploy/esports-silo-collect.service /etc/systemd/system/ || die "cp service failed"
sudo cp esports_silo/deploy/esports-silo-collect.timer   /etc/systemd/system/ || die "cp timer failed"
# make sure the unit's WorkingDirectory matches THIS clone
sudo sed -i "s|^WorkingDirectory=.*|WorkingDirectory=$REPO|" /etc/systemd/system/esports-silo-collect.service
sudo systemctl daemon-reload
sudo systemctl enable --now esports-silo-collect.timer || die "enable timer failed"
sudo systemctl start esports-silo-collect.service || true

echo
printf '\033[1;32mAUTO-PULL ON — pinnodds odds → odds_raw every 15 min. Trades nothing (HALT=true).\033[0m\n'
echo "check it:   systemctl list-timers | grep esports    ;    journalctl -u esports-silo-collect -n 30 --no-pager"
