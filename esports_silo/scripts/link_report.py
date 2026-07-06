#!/usr/bin/env python3
"""esports_silo — read-only PAIRING report: pinnodds matches ↔ Polymarket markets.

For every fresh polymarket_snapshots row (latest per market), run the two-team gate
(`markets/match_matcher.link_market`) against upcoming `matches` rows and print:
  * LINKED pairs side-by-side — raw pinnacle odds next to the Polymarket price
    (both RAW: odds keep their vig, price is the team_a token — no transformation, Cmd 2);
  * UNLINKED markets with their best near-miss (alias-triage data);
  * per-game link rate.

Writes nothing. Needs DATABASE_URL (the silo DB).

USAGE (on the box):
    DATABASE_URL=... python -m esports_silo.scripts.link_report
    # or rely on /opt/esports_silo/.env via:  set -a && . /opt/esports_silo/.env && set +a
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

SNAPSHOT_MAX_AGE_MIN = int(os.getenv("LINK_SNAPSHOT_MAX_AGE_MIN", "120"))
MATCH_WINDOW_H = int(os.getenv("LINK_MATCH_WINDOW_H", "72"))

_SNAPSHOTS_SQL = """
SELECT DISTINCT ON (market_id) market_id, question, game, team_a, team_b, yes_price, snapshot_time
  FROM polymarket_snapshots
 WHERE snapshot_time > now() - ($1 || ' minutes')::interval
 ORDER BY market_id, snapshot_time DESC
"""

_MATCHES_SQL = """
SELECT match_id, team_a, team_b, start_time FROM matches
 WHERE game = $1 AND start_time > now() - interval '2 hours'
   AND start_time < now() + ($2 || ' hours')::interval
"""

_LATEST_ODDS_SQL = """
SELECT DISTINCT ON (match_id) match_id, team_a_odds, team_b_odds, line_time
  FROM odds_raw
 WHERE match_id = ANY($1::text[]) AND aggregator = 'pinnodds'
 ORDER BY match_id, line_time DESC
"""


async def main() -> int:
    import asyncpg
    try:
        from ..config import CONFIG
        from ..markets.match_matcher import link_market, load_alias_map
    except ImportError:
        from config import CONFIG  # type: ignore
        from markets.match_matcher import link_market, load_alias_map  # type: ignore

    if not CONFIG.database_url:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(CONFIG.database_url)
    try:
        snaps = [dict(r) for r in await conn.fetch(_SNAPSHOTS_SQL, str(SNAPSHOT_MAX_AGE_MIN))]
        games = sorted({str(s["game"]) for s in snaps})
        print(f"fresh Polymarket snapshots (≤{SNAPSHOT_MAX_AGE_MIN} min): {len(snaps)} "
              f"across games {games or '—'}")
        if not snaps:
            print("no fresh snapshots — has the timer's polymarket pass run since the tag fix?")
            return 1

        linked, unlinked = [], []
        cache = {}
        for s in snaps:
            game = str(s["game"])
            if game not in cache:
                cands = [dict(r) for r in await conn.fetch(_MATCHES_SQL, game, str(MATCH_WINDOW_H))]
                cache[game] = (cands, await load_alias_map(conn, game))
            cands, alias_map = cache[game]
            res = link_market(str(s["question"] or ""), cands, alias_map)
            if res.link:
                linked.append((s, res.link))
            else:
                unlinked.append((s, res.near_miss))

        ids = [l.match_id for _, l in linked]
        odds_by_match = {}
        if ids:
            for r in await conn.fetch(_LATEST_ODDS_SQL, ids):
                odds_by_match[str(r["match_id"])] = dict(r)

        print(f"\n=== LINKED ({len(linked)}) — raw pinnacle odds vs Polymarket team_a price ===")
        print(f"{'game':<9}{'pinnodds match':<42}{'odds_a':>7}{'odds_b':>7}   {'PM market (team_a price)'}")
        for s, l in sorted(linked, key=lambda x: str(x[0]['game'])):
            o = odds_by_match.get(l.match_id, {})
            oa, ob = o.get("team_a_odds"), o.get("team_b_odds")
            oa_s = f"{oa:.3f}" if isinstance(oa, float) else "-"
            ob_s = f"{ob:.3f}" if isinstance(ob, float) else "-"
            pm = f"{s['team_a']} vs {s['team_b']} @ {s['yes_price']}"
            print(f"{s['game']:<9}{(l.team_a + ' vs ' + l.team_b)[:40]:<42}{oa_s:>7}{ob_s:>7}   {pm[:60]}")

        print(f"\n=== UNLINKED ({len(unlinked)}) — near-miss triage (add aliases where sensible) ===")
        for s, nm in unlinked[:30]:
            if nm:
                print(f"  [{s['game']}] {str(s['question'])[:70]}")
                print(f"       best partial: {nm['team_a']} vs {nm['team_b']} (score {nm['score']:.0f})")
            else:
                print(f"  [{s['game']}] {str(s['question'])[:70]}  (no candidate matches in window)")
        if len(unlinked) > 30:
            print(f"  ... and {len(unlinked) - 30} more")

        print("\n=== link rate per game ===")
        for g in games:
            n_l = sum(1 for s, _ in linked if s["game"] == g)
            n_u = sum(1 for s, _ in unlinked if s["game"] == g)
            tot = n_l + n_u
            print(f"  {g:<9} {n_l}/{tot} linked ({(100 * n_l / tot):.0f}%)" if tot else f"  {g:<9} 0/0")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
