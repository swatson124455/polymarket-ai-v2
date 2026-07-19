# Curated esports_team_aliases additions — the 2026-07-17 name-gap class
# (EB_SHARP_LINE_NEXT_SESSION.md §0-S8, commit a95da18): PinnOdds short names
# vs Polymarket full names diverge beyond diacritics; every pair below was
# verified SAME-TEAM before inclusion (same opponent + same day + same league
# across both books, plus org-level evidence — KINGZERO/KingZone share captain
# Jin "Fiber" Aohan per Liquipedia + Ubisoft roster page).
#
# Idempotent: ON CONFLICT (canonical_name, alias, game) DO NOTHING.
# Dry-run by default; pass --apply to write. After applying, re-run
# deploy/vps/eb_dump_aliases.sh so the collector's aliases.json picks it up.
# NEVER add a pair here without same-team verification — a wrong alias is the
# S152/B2 wrong-attach loss class.
set -e
MODE="${1:-dry-run}"
export DATABASE_URL="$(grep -m1 '^DATABASE_URL=' /opt/pa2-shared/.env | cut -d= -f2- | tr -d '"\r')"
if [ -z "$DATABASE_URL" ]; then echo "no DATABASE_URL in /opt/pa2-shared/.env"; exit 1; fi
EB_ALIAS_MODE="$MODE" /opt/polymarket-ai-v2-esports/venv/bin/python3 - <<'PY'
import asyncio, json, os
import asyncpg

# (canonical_name, alias, game) — canonical matches the existing PandaScore
# identity row where one exists (1WIN, BetBoom Team, PARIVISION, Ninjas in
# Pyjamas, Game Hunters all have identity rows already).
CURATED = [
    ("1WIN",              "1W",          "cs2"),      # PM 1WIN vs GenOne == Pinn 1W vs GenOne, 2026-07-17 EPL
    ("BetBoom Team",      "BB Team",     "dota2"),    # PM BetBoom Team vs Nigma == Pinn BB Team vs Nigma, 2026-07-17 EWC
    ("PARIVISION",        "PVISION",     "dota2"),    # PM PARIVISION vs Team Yandex == Pinn PVISION vs Yandex, 2026-07-18 EWC
    ("Ninjas in Pyjamas", "NIP",         "cs2"),      # PM NIP vs Heroic == Pinn Ninjas In Pyjamas vs HEROIC, 2026-07-17 STK
    ("Game Hunters",      "GameHunters", "cs2"),      # PM Game Hunters vs ALKA == Pinn GameHunters vs Alka, 2026-07-17 CCT SA
    ("KingZone",          "KINGZERO",    "r6siege"),  # PM Kingzone vs All Gamers == Pinn KINGZERO vs All Gamers, 2026-07-18 CN League; same captain (Fiber) both names
    # 2026-07-19 population-census additions (root-cause-completeness pass — the
    # earlier 6 were symptom-driven from ONE tick; these are the remaining LIVE
    # fixable misses across the current slate, each same-team-verified):
    ("Nongshim Red Force","Nongshim Redforce","lol"), # PM "Nongshim Red Force vs BNK FEARX" (open LCK Rd3-4) == Pinn "Nongshim Redforce vs BNK FearX"; spacing variant of the LCK org (49 null rows / 3 matches)
    ("Titan Esports Club","Titan",        "valorant"),# PM "TEC Esports vs All Gamers" (open VCT China S2) == Pinn "Titan vs All Gamers"; Titan Esports Club = TEC (VLR/Liquipedia)
    ("Titan Esports Club","TEC Esports",  "valorant"),# links the PM outcome name "TEC Esports" into the Titan group
]

async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    apply = os.environ.get("EB_ALIAS_MODE") == "--apply"
    conn = await asyncpg.connect(url)
    try:
        inserted = 0
        for canon, alias, game in CURATED:
            if apply:
                r = await conn.execute(
                    "INSERT INTO esports_team_aliases "
                    "(canonical_name, alias, source, confidence, game) "
                    "VALUES ($1, $2, 'manual_curated', 1.0, $3) "
                    "ON CONFLICT (canonical_name, alias, game) DO NOTHING",
                    canon, alias, game)
                n = int(r.split()[-1])
                inserted += n
                print(f"  {'INSERTED' if n else 'exists  '} {canon!r} <- {alias!r} [{game}]")
            else:
                exists = await conn.fetchval(
                    "SELECT 1 FROM esports_team_aliases WHERE canonical_name=$1 "
                    "AND alias=$2 AND game=$3", canon, alias, game)
                print(f"  {'exists  ' if exists else 'WOULD ADD'} {canon!r} <- {alias!r} [{game}]")
    finally:
        await conn.close()
    if apply:
        print(f"inserted={inserted} of {len(CURATED)} (rest already present)")
    else:
        print(f"DRY-RUN — {len(CURATED)} curated pairs; re-run with --apply to write")

asyncio.run(main())
PY
