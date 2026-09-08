#!/usr/bin/env bash
# Daily label supplement + fee map refresh (runs as polymarket, 11:40Z -
# before the 12:30Z readout). Keeps the readout fed: CLOB labels for shadow
# markets the shared backfill structurally cannot see (2026-07-22 finding),
# and the per-market taker-fee map (2026-07-30 operator-approved equation).
# Read-only vs trading state; writes only the two cache files (each script
# does its own backup + atomic replace + non-empty guards).
set -uo pipefail
D=/opt/pa2-shared/mb_readout
LOG=/opt/pa2-shared/mb_copyable_data/deep_dive/label_fee_refresh.log
DBURL=$(grep -m1 "^DATABASE_URL=" /opt/pa2-shared/.env | cut -d= -f2-)
[ -n "$DBURL" ] || { echo "[$(date -u +%FT%TZ)] FATAL: no DATABASE_URL" >> "$LOG"; exit 1; }
cd /opt/polymarket-ai-v2
{
  echo "===== $(date -u +%FT%TZ) label supplement ====="
  DATABASE_URL="$DBURL" PYTHONPATH=/opt/polymarket-ai-v2 \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/shadow_label_supplement.py" --write 2>&1 | tail -6
  echo "===== $(date -u +%FT%TZ) fee map ====="
  DATABASE_URL="$DBURL" PYTHONPATH=/opt/polymarket-ai-v2 \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/build_fee_map.py" --write 2>&1 | tail -4
} >> "$LOG"
{
  echo "===== $(date -u +%FT%TZ) cohort5 qualification ====="
  DATABASE_URL="$DBURL" PYTHONPATH="$D" \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/cohort5_qualification.py" 2>&1 | grep -vE "^[0-9]{4}-" | tail -30
} >> "$LOG"
{
  echo "===== $(date -u +%FT%TZ) band 0.65-0.85 forward test ====="
  DATABASE_URL="$DBURL" PYTHONPATH="$D" \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/band_tracker.py" 2>&1
} >> "$LOG"
{
  DATABASE_URL="$DBURL" PYTHONPATH="$D" \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_scoreboard.py" 2>&1
} >> "$LOG"
{
  echo "===== $(date -u +%FT%TZ) canon verification (blind, date-seeded) ====="
  PYTHONPATH=/opt/mirror3 \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/canon_verify.py" 2>&1
} >> "$LOG"
{
  echo "===== $(date -u +%FT%TZ) trader funnel (one-table review) ====="
  # operator sizer foursome (2026-08-30 "1 ok"): sourced, never hardcoded -
  # /opt/pa2-shared/mb_sizer.env is the ONE place to change sizing params
  [ -f /opt/pa2-shared/mb_sizer.env ] && . /opt/pa2-shared/mb_sizer.env
  DATABASE_URL="$DBURL" PYTHONPATH="$D" \
  MB_SIZER_BANKROLL="$MB_SIZER_BANKROLL" \
  MB_SIZER_KELLY_MULT="$MB_SIZER_KELLY_MULT" \
  MB_SIZER_CONCURRENCY="$MB_SIZER_CONCURRENCY" \
  MB_SIZER_MIN_VIABLE="$MB_SIZER_MIN_VIABLE" \
  MB_ALLOC_TIER_FRACS="${MB_ALLOC_TIER_FRACS:-}" \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/trader_funnel.py" 2>&1
} >> "$LOG"
{
  echo "===== $(date -u +%FT%TZ) hypothetical dollar ledger (paper) ====="
  [ -f /opt/pa2-shared/mb_sizer.env ] && . /opt/pa2-shared/mb_sizer.env
  DATABASE_URL="$DBURL" PYTHONPATH="$D"   MB_SIZER_BANKROLL="$MB_SIZER_BANKROLL"   MB_SIZER_KELLY_MULT="$MB_SIZER_KELLY_MULT"   MB_SIZER_CONCURRENCY="$MB_SIZER_CONCURRENCY"   MB_SIZER_MIN_VIABLE="$MB_SIZER_MIN_VIABLE"     /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_hypo_ledger.py" 2>&1 | grep -vE "^[0-9]{4}-"
} >> "$LOG"
{
  # backtest daily stage (operator GO 2026-09-06): incremental extract of
  # newly-complete firehose days -> label the new tokens -> both
  # leaderboards. Tailability bar 20 = operator ruling 2026-09-06.
  echo "===== $(date -u +%FT%TZ) backtest daily leaderboard ====="
  BT=/opt/pa2-shared/mb_copyable_data/backtest
  DATABASE_URL="$DBURL" PYTHONPATH="$D" \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_backtest.py" daily-extract \
      --files /opt/pa2-shared/mb_copyable_data/firehose/firehose_*.jsonl.gz \
      --max-conc 20 --outdir "$BT" 2>&1 | grep -vE "^[0-9]{4}-"
  if [ -s "$BT/sweep_tokens_new.jsonl" ]; then
    DATABASE_URL="$DBURL" PYTHONPATH=/opt/polymarket-ai-v2 \
      /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/shadow_label_supplement.py" \
        --shadow "$BT/sweep_tokens_new.jsonl" --write 2>&1 | tail -4
  else
    echo "[daily] 0 new tokens - label step skipped"
  fi
  # $algo basis (operator ruling 2026-09-08 #2): the boards size every
  # holdout wager with the sizer foursome - sourced, never hardcoded
  [ -f /opt/pa2-shared/mb_sizer.env ] && . /opt/pa2-shared/mb_sizer.env
  DATABASE_URL="$DBURL" PYTHONPATH="$D" \
  MB_SIZER_BANKROLL="${MB_SIZER_BANKROLL:-}" \
  MB_SIZER_KELLY_MULT="${MB_SIZER_KELLY_MULT:-}" \
  MB_SIZER_CONCURRENCY="${MB_SIZER_CONCURRENCY:-}" \
  MB_SIZER_MIN_VIABLE="${MB_SIZER_MIN_VIABLE:-}" \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_backtest.py" daily-replay \
      --rows "$BT/candidate_rows.jsonl" --outdir "$BT" --top 10 2>&1 \
      | grep -vE "^[0-9]{4}-|\[info|\[debug"
} >> "$LOG"
{
  # gamma window crawl (operator GO 2026-09-07 "1 do it"): labels for
  # OUTSIDE-DB tokens the DB-join supplement structurally cannot reach
  # (mb_gamma_window_labels.py; the 09-07 full capture-era run took
  # sweep-token coverage 33.1%->74.2%, CONFLICT=0). Rolling 72h endDate
  # window = ~3 daily chances per market; timeout -s INT so a hang
  # becomes a loud Traceback (chain-watch CRASHED), never a wedged cron.
  echo "===== $(date -u +%FT%TZ) gamma window crawl ====="
  BT=/opt/pa2-shared/mb_copyable_data/backtest
  timeout -s INT 2400 \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_gamma_window_labels.py" \
      --tokens "$BT/sweep_tokens.jsonl" \
      --start "$(date -u -d '72 hours ago' +%FT%TZ)" --rps 8 --write 2>&1 | tail -8
} >> "$LOG"
{
  # tailable list (docs/MB_TAILABLE_PLAN.md P1, operator deliverable
  # 2026-09-08): one row per wallet from the boards, dossiers, eligibility
  # reads, roster, forward status. Writes OUTSIDE the clone
  # (mb_copyable_data/tailable/). Sizer foursome sourced, never hardcoded.
  echo "===== $(date -u +%FT%TZ) tailable list ====="
  [ -f /opt/pa2-shared/mb_sizer.env ] && . /opt/pa2-shared/mb_sizer.env
  PYTHONPATH="$D" \
  MB_SIZER_BANKROLL="${MB_SIZER_BANKROLL:-}" \
  MB_SIZER_KELLY_MULT="${MB_SIZER_KELLY_MULT:-}" \
  MB_SIZER_CONCURRENCY="${MB_SIZER_CONCURRENCY:-}" \
  MB_SIZER_MIN_VIABLE="${MB_SIZER_MIN_VIABLE:-}" \
    timeout -s INT 900 \
    /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_tailable_list.py" 2>&1 \
      | grep -vE "^[0-9]{4}-|\[info|\[debug"
} >> "$LOG"
{
  /opt/polymarket-ai-v2/venv/bin/python "$D/scripts/mb_chain_watch.py" 2>&1
} >> "$LOG"
