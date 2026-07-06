#!/usr/bin/env python3
"""esports_silo — read-only two-sided PAIRING reconciliation: pinnodds ↔ Polymarket.

Every current match lands in exactly one bucket, so "matcher failed" is never conflated
with "the other side has no line":
  * PAIRED        — upcoming pinnodds match linked to a fresh PM market: raw pinnacle odds
                    next to the PM team_a price (both RAW — no transformation, Cmd 2).
  * PINNODDS-ONLY — pinnodds quotes it, PM has no market (typically low-tier).
  * PM-ONLY       — PM lists it, pinnodds has no *upcoming* line (typically in-play now —
                    we are pre-match only — or a line not yet posted; sampling re-polls).
Plus near-miss triage on PM-only rows whose best partial score is high (alias candidates),
and the paired rate over the HONEST denominator (matches present on both sides).

Writes nothing. Needs DATABASE_URL (the silo DB).

USAGE (on the box):
    set -a && . /opt/esports_silo/.env && set +a && \
      python -m esports_silo.scripts.link_report
"""
from __future__ import annotations

import asyncio
import os

SNAPSHOT_MAX_AGE_MIN = int(os.getenv("LINK_SNAPSHOT_MAX_AGE_MIN", "120"))
MATCH_WINDOW_H = int(os.getenv("LINK_MATCH_WINDOW_H", "96"))

_SNAPSHOTS_SQL = """
SELECT DISTINCT ON (market_id) market_id, question, game, team_a, team_b, yes_price, snapshot_time
  FROM polymarket_snapshots
 WHERE snapshot_time > now() - ($1 || ' minutes')::interval
 ORDER BY market_id, snapshot_time DESC
"""

# upcoming pinnodds matches (the bot's tradeable universe: pre-match only)
_UPCOMING_SQL = """
SELECT match_id, game, team_a, team_b, start_time FROM matches
 WHERE source = 'pinnodds' AND start_time > now()
   AND start_time < now() + ($1 || ' hours')::interval
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
        upcoming = [dict(r) for r in await conn.fetch(_UPCOMING_SQL, str(MATCH_WINDOW_H))]
        print(f"fresh PM snapshots (≤{SNAPSHOT_MAX_AGE_MIN} min): {len(snaps)}   |   "
              f"upcoming pinnodds matches (≤{MATCH_WINDOW_H}h): {len(upcoming)}")
        if not snaps and not upcoming:
            print("nothing on either side — has the timer run since the tag fix?")
            return 1

        by_game_cands = {}
        alias_maps = {}
        for m in upcoming:
            by_game_cands.setdefault(str(m["game"]), []).append(m)
        for g in set(list(by_game_cands) + [str(s["game"]) for s in snaps]):
            alias_maps[g] = await load_alias_map(conn, g)

        paired = []            # (snap, MatchLink)
        pm_only = []           # (snap, near_miss)
        linked_match_ids = set()
        for s in snaps:
            g = str(s["game"])
            res = link_market(str(s["question"] or ""), by_game_cands.get(g, []), alias_maps[g])
            if res.link:
                paired.append((s, res.link))
                linked_match_ids.add(res.link.match_id)
            else:
                pm_only.append((s, res.near_miss))
        pinnodds_only = [m for m in upcoming if str(m["match_id"]) not in linked_match_ids]

        odds_by_match = {}
        if linked_match_ids:
            for r in await conn.fetch(_LATEST_ODDS_SQL, list(linked_match_ids)):
                odds_by_match[str(r["match_id"])] = dict(r)

        print(f"\n=== PAIRED ({len(paired)}) — raw pinnacle odds vs PM team_a price ===")
        print(f"{'game':<9}{'pinnodds match':<42}{'odds_a':>7}{'odds_b':>7}   PM market (team_a price)")
        for s, l in sorted(paired, key=lambda x: str(x[0]["game"])):
            o = odds_by_match.get(l.match_id, {})
            oa, ob = o.get("team_a_odds"), o.get("team_b_odds")
            oa_s = f"{oa:.3f}" if isinstance(oa, float) else "-"
            ob_s = f"{ob:.3f}" if isinstance(ob, float) else "-"
            pm = f"{s['team_a']} vs {s['team_b']} @ {s['yes_price']}"
            print(f"{s['game']:<9}{(l.team_a + ' vs ' + l.team_b)[:40]:<42}{oa_s:>7}{ob_s:>7}   {pm[:58]}")

        print(f"\n=== PINNODDS-ONLY ({len(pinnodds_only)}) — sharp line exists, no PM market ===")
        for m in pinnodds_only:
            print(f"  [{m['game']}] {m['team_a']} vs {m['team_b']}  (starts {m['start_time']:%m-%d %H:%M} UTC)")

        print(f"\n=== PM-ONLY ({len(pm_only)}) — PM market exists, no upcoming pinnodds line ===")
        print("(usually: match is IN-PLAY now — we are pre-match only — or the line isn't posted yet;")
        print(" a HIGH near-miss score against a real upcoming match = alias gap worth fixing)")
        triage = sorted(pm_only, key=lambda x: -(x[1] or {}).get("score", 0))
        for s, nm in triage[:15]:
            if nm and nm.get("score", 0) >= 50:
                print(f"  ⚠ [{s['game']}] {str(s['question'])[:64]}")
                print(f"       near-miss {nm['score']:.0f}: {nm['team_a']} vs {nm['team_b']}  <-- possible alias gap")
            else:
                print(f"  [{s['game']}] {str(s['question'])[:64]}")
        if len(pm_only) > 15:
            print(f"  ... and {len(pm_only) - 15} more (all near-miss < 50)")

        # honest rate: of pinnodds matches that COULD pair (same-game PM market exists at all),
        # how many did? Approximate the could-pair universe per game.
        print("\n=== paired rate over the overlap universe ===")
        pm_games = {str(s["game"]) for s in snaps}
        for g in sorted(by_game_cands):
            ups = by_game_cands[g]
            n_p = sum(1 for _, l in paired if any(str(m["match_id"]) == l.match_id for m in ups))
            if g not in pm_games:
                print(f"  {g:<9} {len(ups)} upcoming pinnodds matches, PM lists NO {g} markets at all")
            else:
                print(f"  {g:<9} {n_p}/{len(ups)} upcoming pinnodds matches paired to a PM market")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
