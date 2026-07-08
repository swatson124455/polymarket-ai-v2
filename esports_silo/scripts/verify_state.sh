#!/usr/bin/env bash
# esports_silo — READ-ONLY state verification. Proves the HANDOFF claims against the live box.
# Writes nothing, trades nothing. Run this FIRST in any fresh session before trusting the docs.
#
# USAGE (on the box):
#   cd ~/esports_silo_src && git pull && bash esports_silo/scripts/verify_state.sh
set -uo pipefail
PY=/opt/pa2-esports-shared/venv/bin/python
say(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

[ -f esports_silo/collectors/pinnodds_collector.py ] || { echo "run from the repo root (~/esports_silo_src)"; exit 2; }
[ -f /opt/esports_silo/.env ] && { set -a; . /opt/esports_silo/.env; set +a; } || echo "WARN: /opt/esports_silo/.env missing"

say "1. code + tests (expect: 14/14 green)"
echo -n "HEAD "; git rev-parse --short HEAD
"$PY" -m esports_silo.run.selftest 2>&1 | tail -1

say "2. collection timer (expect: an esports-silo-collect timer, recent last-run)"
systemctl list-timers --all 2>/dev/null | grep -i esports || echo "  ❌ NO esports timer installed"
journalctl -u esports-silo-collect -n 5 --no-pager 2>/dev/null | tail -5 || echo "  (no journal)"

say "3. DB freshness — read-only (expect: pinnodds odds + PM snapshots accumulating)"
psql "${DATABASE_URL:?DATABASE_URL not set}" -tA <<'SQL'
select '  odds_raw(pinnodds)      rows='||count(*)||'  newest='||coalesce(max(line_time)::text,'none') from odds_raw where aggregator='pinnodds';
select '  polymarket_snapshots    rows='||count(*)||'  newest='||coalesce(max(snapshot_time)::text,'none') from polymarket_snapshots;
select '  matches total='||count(*)||'  winner_set='||count(*) filter (where winner is not null) from matches;
select '  forward (source=pinnodds): total='||count(*)||'  winner_set='||count(*) filter (where winner is not null) from matches where source='pinnodds';
select '  predictions (forward ledger) rows='||count(*) from predictions;
SQL

say "4. keys (THE blocker: PandaScore must be VALID before matches settle)"
echo "  (OddsPapi is abandoned — its INVALID line is expected/irrelevant. Watch PandaScore.)"
"$PY" -m esports_silo.scripts.validate_keys 2>&1 | sed -n '1,12p'

say "5. pairing snapshot (expect: all 4 games with paired rows)"
"$PY" -m esports_silo.scripts.link_report 2>&1 | tail -18

say "6. HALT flag (MUST stay true until the forward skill gate passes)"
grep -E '^SILO_ENTRY_HALT' /opt/esports_silo/.env 2>/dev/null || echo "  not set (defaults true)"

echo
echo "READ THE ABOVE against esports_silo/HANDOFF.md. If timer is missing, odds/snapshots are"
echo "stale, or PandaScore is INVALID, that's the gap to close — see VERIFY_AND_PROCEED.md."
