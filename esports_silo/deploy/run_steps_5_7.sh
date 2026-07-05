#!/usr/bin/env bash
# esports_silo — GO_LIVE_CHECKLIST steps 5–7, as one paste-free command.
#
# Step 5: write /opt/esports_silo/.env (silo DATABASE_URL + the 3 API keys).
# Step 6: validate the keys (VALID / INVALID / UNREACHABLE / MISSING).
# Step 7: dry-run both collectors (NO writes) and print the per-(game,book)
#         coverage table — this is where we finally see whether the plan
#         returns singbet/sbobet for esports.
#
# Nothing trades. SILO_ENTRY_HALT stays true. Run steps 0–4 FIRST (the silo DB
# must already exist and have cleared the gate).
#
# USAGE (on the box, from the cloned repo root ~/esports_silo_src):
#   bash esports_silo/deploy/run_steps_5_7.sh
#
# KEYS: each of ODDSPAPI_API_KEY / PANDASCORE_API_KEY / RIOT_API_KEY is taken
# from the environment if you set it, else auto-sourced from the box's existing
# shared env files. To force a specific OddsPapi key for this run, prefix the
# command:  ODDSPAPI_API_KEY=papi-xxxx bash esports_silo/deploy/run_steps_5_7.sh
# Key VALUES are never printed — only VALID/INVALID and the last 4 characters.
set -uo pipefail

DBNAME="esports_silo"; DBROLE="esports_silo"
ENV_DIR="/opt/esports_silo"; ENV_FILE="$ENV_DIR/.env"
ENV_CANDIDATES="/opt/pa2-shared/.env /opt/pa2-esports-shared/.env $HOME/.env /opt/polymarket-ai-v2-esports/.env"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mSTOP: %s\033[0m\n' "$*" >&2; exit 2; }
mask(){ local v="$1"; [ -n "$v" ] && printf '…%s (len %s)' "${v: -4}" "${#v}" || printf '(empty)'; }

[ -f esports_silo/db/schema.sql ] || die "run me from the cloned repo root (~/esports_silo_src)."

# ── Pick a Python that has asyncpg + aiohttp (the collectors need both) ─────
PY=""
for cand in "${SILO_PYTHON:-}" /opt/pa2-esports-shared/venv/bin/python \
            /opt/pa2-shared/venv/bin/python /opt/polymarket-ai-v2-esports/venv/bin/python \
            python3 python; do
  [ -n "$cand" ] || continue
  command -v "$cand" >/dev/null 2>&1 || continue
  "$cand" -c 'import asyncpg' >/dev/null 2>&1 && { PY="$cand"; break; }
done
if [ -z "$PY" ]; then
  for cand in /opt/*/venv/bin/python /opt/*/.venv/bin/python "$HOME"/*/bin/python; do
    [ -x "$cand" ] || continue
    "$cand" -c 'import asyncpg' >/dev/null 2>&1 && { PY="$cand"; break; }
  done
fi
[ -n "$PY" ] || die "no python with asyncpg found — set SILO_PYTHON=/path/to/botvenv/python."
echo "using python: $PY"
# aiohttp/structlog are needed by the collectors (not by validate_keys, which is stdlib).
HTTP_OK=1
"$PY" -c 'import aiohttp, structlog' >/dev/null 2>&1 || HTTP_OK=0
[ "$HTTP_OK" = 1 ] || echo "WARN: $PY lacks aiohttp/structlog — key validation still works, but the Step-7 dry-run may fail. Install into that venv if so."

# ── Resolve a KEY=VALUE from the box's shared env files (first hit wins) ────
resolve_from_env() {   # $1 = var name -> echoes value or empty
  local name="$1" f val
  for f in $ENV_CANDIDATES; do
    [ -f "$f" ] || continue
    val=$(grep -hE "^(export[[:space:]]+)?$name=" "$f" 2>/dev/null | head -1 \
          | sed -E "s/^(export[[:space:]]+)?$name=//")
    val=${val%\"}; val=${val#\"}; val=${val%\'}; val=${val#\'}
    [ -n "$val" ] && { printf '%s' "$val"; return; }
  done
}

# env override beats box-sourced value; box-sourced is the fallback
ODDSPAPI_API_KEY="${ODDSPAPI_API_KEY:-$(resolve_from_env ODDSPAPI_API_KEY)}"
PANDASCORE_API_KEY="${PANDASCORE_API_KEY:-$(resolve_from_env PANDASCORE_API_KEY)}"
RIOT_API_KEY="${RIOT_API_KEY:-$(resolve_from_env RIOT_API_KEY)}"

echo "keys resolved:"
echo "  ODDSPAPI_API_KEY   = $(mask "$ODDSPAPI_API_KEY")"
echo "  PANDASCORE_API_KEY = $(mask "$PANDASCORE_API_KEY")"
echo "  RIOT_API_KEY       = $(mask "$RIOT_API_KEY")"

# ── Step 5 — (re)set the silo role password and write the .env ──────────────
say "Step 5 — write $ENV_FILE"
PW=$(openssl rand -hex 24)
ROLE_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DBROLE';")
[ "$ROLE_EXISTS" = "1" ] || die "silo role '$DBROLE' missing — run steps 0–4 first."
sudo -u postgres psql -c "ALTER ROLE $DBROLE LOGIN PASSWORD '$PW';" >/dev/null || die "ALTER ROLE failed"
SILO_DB="postgresql://$DBROLE:$PW@localhost:5432/$DBNAME"

sudo mkdir -p "$ENV_DIR" || die "mkdir $ENV_DIR failed"
sudo chown "$(id -u):$(id -g)" "$ENV_DIR" 2>/dev/null || true
umask 177
cat > "$ENV_FILE" <<EOF
DATABASE_URL=$SILO_DB
ODDSPAPI_API_KEY=$ODDSPAPI_API_KEY
PANDASCORE_API_KEY=$PANDASCORE_API_KEY
RIOT_API_KEY=$RIOT_API_KEY
POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com
POLYMARKET_CLOB_API=https://clob.polymarket.com
SHARP_BOOKS=singbet,sbobet
ESPORTS_GAMES=cs2,lol,dota2,valorant
CALIBRATOR_PATH=esports_silo/artifacts/calibrator_sharp_consensus_v1.json
SILO_BANKROLL_USD=1000
SILO_ENTRY_HALT=true
EOF
chmod 600 "$ENV_FILE"
echo "wrote $ENV_FILE (chmod 600). SILO_ENTRY_HALT=true, SHARP_BOOKS=singbet,sbobet."

# ── Step 6 — validate the keys ─────────────────────────────────────────────
say "Step 6 — validate keys (want VALID; INVALID=rejected/expired, UNREACHABLE=network)"
ODDSPAPI_API_KEY="$ODDSPAPI_API_KEY" PANDASCORE_API_KEY="$PANDASCORE_API_KEY" \
  RIOT_API_KEY="$RIOT_API_KEY" "$PY" -m esports_silo.scripts.validate_keys || true

# ── Step 7 — dry-run the collectors (NO writes) ────────────────────────────
say "Step 7a — odds collector dry-run (per-(game,book) coverage; confirm singbet/sbobet)"
DATABASE_URL="$SILO_DB" ODDSPAPI_API_KEY="$ODDSPAPI_API_KEY" SHARP_BOOKS="singbet,sbobet" \
  "$PY" -m esports_silo.collectors.odds_collector --once --dry-run --poll-all || \
  echo "(odds dry-run returned non-zero — paste the output; often a coverage/quota signal, not a crash)"

say "Step 7b — polymarket collector dry-run (first raw payload logged)"
DATABASE_URL="$SILO_DB" \
  "$PY" -m esports_silo.collectors.polymarket_collector --once --dry-run || \
  echo "(polymarket dry-run returned non-zero — paste the output)"

echo
printf '\033[1;32mSteps 5–7 done. Check: (a) all keys VALID above, (b) singbet/sbobet show >0 coverage.\033[0m\n'
echo "If a book shows 0 coverage or a key is INVALID, paste the output — do not proceed to Step 8."
