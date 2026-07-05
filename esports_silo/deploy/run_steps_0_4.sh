#!/usr/bin/env bash
# esports_silo — GO_LIVE_CHECKLIST steps 0–4, as one paste-free command.
#
# WHY: the raw checklist block is too large to paste into some SSH/PowerShell
# terminals. This runs the SAME steps (get code, make the silo DB, import
# history, drop the one bad row, run the data-quality gate) and pauses after
# the dry-run so you can eyeball the counts before writing. NO trading — the
# bot stays halted; nothing here touches keys, collectors, or execution.
#
# USAGE (on the box, from the repo root):
#   SOURCE_DATABASE_URL="postgresql://…old-polymarket-db" bash esports_silo/deploy/run_steps_0_4.sh
#
# It NEVER echoes the generated DB password to stdout. It DOES print the full
# DATABASE_URL once (Step 1) so you can paste it into the .env — that line
# contains the password, so treat it as a secret. Nothing is written to git.
#
# Re-runnable: if the silo role/db already exist it reuses them; the import and
# gate are safe to re-run. Exit code is the gate's verdict (0 = PASS).
set -uo pipefail

BRANCH="claude/blissful-davinci-twt397-n94qo6"
DBNAME="esports_silo"
DBROLE="esports_silo"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mSTOP: %s\033[0m\n' "$*" >&2; exit 2; }

[ -f esports_silo/db/schema.sql ] || die "run me from the repo root (esports_silo/db/schema.sql not found)."
: "${SOURCE_DATABASE_URL:?set SOURCE_DATABASE_URL to the OLD bot DB read URL, e.g. SOURCE_DATABASE_URL=... bash $0}"

# ── Step 0 — latest code ───────────────────────────────────────────────────
say "Step 0 — fetch + checkout $BRANCH"
git fetch origin "$BRANCH"           || die "git fetch failed"
git checkout "$BRANCH"               || die "git checkout failed"
git pull origin "$BRANCH"            || die "git pull failed"

# ── Step 1 — silo's own DB (idempotent) ────────────────────────────────────
say "Step 1 — silo role + database + schema"
ROLE_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DBROLE';")
if [ "$ROLE_EXISTS" = "1" ]; then
  echo "role '$DBROLE' already exists — reusing it (password unchanged)."
  [ -n "${SILO_DB:-}" ] || die "role exists but \$SILO_DB is not set in this shell — export the existing DATABASE_URL and re-run, or drop the role to recreate."
else
  PW=$(openssl rand -hex 24)
  sudo -u postgres psql -c "CREATE ROLE $DBROLE LOGIN PASSWORD '$PW' NOSUPERUSER NOCREATEDB NOCREATEROLE;" || die "CREATE ROLE failed"
  export SILO_DB="postgresql://$DBROLE:$PW@localhost:5432/$DBNAME"
fi

DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DBNAME';")
if [ "$DB_EXISTS" = "1" ]; then
  echo "database '$DBNAME' already exists — reusing it."
else
  sudo -u postgres createdb -O "$DBROLE" "$DBNAME" || die "createdb failed"
fi

echo
echo ">>> PASTE THIS INTO /opt/esports_silo/.env (Step 5) — it contains the password, keep it secret:"
echo "    DATABASE_URL=$SILO_DB"
echo

psql "$SILO_DB" -f esports_silo/db/schema.sql || die "schema load failed"

# ── Step 2 — import history (dry-run, then confirm, then real) ──────────────
say "Step 2 — import DRY-RUN (no writes) — want ~32k matches / ~1,777 aliases"
DATABASE_URL="$SILO_DB" SOURCE_DATABASE_URL="$SOURCE_DATABASE_URL" \
  python -m esports_silo.scripts.import_from_prior_bot --matches-from-db --aliases-from-db --dry-run \
  || die "dry-run import failed"

echo
read -r -p "Counts look right (~32k matches / ~1,777 aliases)? Type YES to write, anything else aborts: " ANS
[ "$ANS" = "YES" ] || die "aborted before the real import — nothing was written."

say "Step 2 — import FOR REAL"
DATABASE_URL="$SILO_DB" SOURCE_DATABASE_URL="$SOURCE_DATABASE_URL" \
  python -m esports_silo.scripts.import_from_prior_bot --matches-from-db --aliases-from-db \
  || die "real import failed"

# ── Step 3 — drop the one known bad row ────────────────────────────────────
say "Step 3 — delete the fake 0–0 row grid_1015039 (want DELETE 0 or DELETE 1)"
psql "$SILO_DB" -c "DELETE FROM matches WHERE match_id='grid_1015039';" || die "delete failed"

# ── Step 4 — THE GATE ──────────────────────────────────────────────────────
say "Step 4 — data-quality gate (the milestone)"
DATABASE_URL="$SILO_DB" python -m esports_silo.scripts.verify_data_quality
GATE=$?
echo
if [ "$GATE" -eq 0 ]; then
  printf '\033[1;32mGATE: PASS (exit 0) — data has left quarantine. Next: Step 5 (keys into the .env).\033[0m\n'
else
  printf '\033[1;33mGATE did NOT pass (exit %s) — copy the output above and send it to Claude; do not improvise.\033[0m\n' "$GATE"
fi
exit "$GATE"
