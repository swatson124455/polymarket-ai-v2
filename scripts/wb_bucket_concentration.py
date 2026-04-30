#!/usr/bin/env python3
"""WeatherBot Bucket-Concentration Check — S204 Lead 4(5c) verification.

Per S203_WB_PHASE6_HYPOTHESIS_TEST.md §7 caveat 3:
    "Aggregate-statistics bucket-concentration check (S172 Protocol candidates
     §2 at line 1648). Per the candidate discipline, a city's headline loss may
     be driven by a single (entry_date × side × triple) cluster. The top-5
     cities × SIDE breakdown showed all-NO concentration — but did NOT verify
     whether each city's loss is driven by a single date or distributed across
     dates. Filed as a follow-up."

This script runs the follow-up: for each WB top-loser city in the CLEAN
post-Day-2 cohort, decompose the per-city loss by entry_date and side. Output
flags any city whose top single (date × side) cluster accounts for >50% of
its total loss — that's the "concentration-by-single-cluster" signature.

Reuses bot_pnl.py block 5's contamination CTE and DISTINCT-ON-ENTRY pattern
to keep the cohort definition aligned with the canonical
S203_H0PRIME_BOT_PNL_OUTPUT.txt per-city totals.

Usage (operator on VPS, against prod DB):
    PYTHONPATH=. python scripts/wb_bucket_concentration.py \\
        --since 20260414_132211 --clean --top-n 10
"""
import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from base_engine.data.database import Database
from scripts.bot_pnl import _CONTAMINATION_CTE_BODY, parse_deploy_timestamp


def _build_concentration_sql(clean: bool) -> str:
    """Build the per-(city × entry_date × side) decomposition SQL.

    Pulled out so unit tests can verify the SQL shape without a live DB.
    """
    contamination_prefix = ""
    contamination_clause = ""
    if clean:
        contamination_prefix = f"WITH contaminated AS ({_CONTAMINATION_CTE_BODY})\n"
        contamination_clause = "AND r.market_id NOT IN (SELECT market_id FROM contaminated)\n"

    return f"""
        {contamination_prefix}
        SELECT e_entry.event_data->>'city' AS city,
               (e_entry.event_data->>'date')::date AS entry_date,
               e_entry.side AS side,
               COUNT(*) AS cnt,
               SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
        FROM trade_events r
        JOIN (
            SELECT DISTINCT ON (market_id) market_id, side, event_data
            FROM trade_events
            WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
            ORDER BY market_id, event_time DESC
        ) e_entry ON e_entry.market_id = r.market_id
        WHERE r.bot_name = 'WeatherBot'
          AND r.event_type IN ('RESOLUTION', 'EXIT')
          AND r.realized_pnl IS NOT NULL
          AND e_entry.event_data->>'city' IS NOT NULL
          AND e_entry.event_data->>'date' IS NOT NULL
          AND r.event_time >= :since_ts
          {contamination_clause}
        GROUP BY e_entry.event_data->>'city',
                 (e_entry.event_data->>'date')::date,
                 e_entry.side
        ORDER BY total_pnl ASC
    """


async def run_concentration_check(
    since_ts: datetime,
    clean: bool,
    top_n: int,
):
    """Run the per-(city × date × side) decomposition and print results.

    Output flags cities where a single (entry_date × side) cluster accounts
    for >50% of the city's total loss — the concentration-by-single-cluster
    signature that S203 §7 caveat 3 calls out as worth checking.
    """
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        sql = _build_concentration_sql(clean)
        params = {"since_ts": since_ts}
        if clean:
            params["bot_family"] = ["WeatherBot"]
        rows = (await s.execute(text(sql), params)).fetchall()

    await db.close()

    if not rows:
        print(f"No rows for since={since_ts.isoformat()}, clean={clean}.")
        return

    # Group: city -> [(date, side, cnt, wins, pnl), ...]
    by_city: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_city[r[0]].append((r[1], r[2], int(r[3]), int(r[4]), float(r[5])))

    # Total loss per city
    city_totals = sorted(
        ((c, sum(p for _, _, _, _, p in entries)) for c, entries in by_city.items()),
        key=lambda x: x[1],
    )

    scope = f"since={since_ts.isoformat()}, {'CLEAN' if clean else 'RAW'}"
    print(f"\n{'=' * 70}")
    print(f"WB BUCKET-CONCENTRATION CHECK ({scope}) — top {top_n} losing cities")
    print(f"{'=' * 70}")

    for city, total_pnl in city_totals[:top_n]:
        if total_pnl >= 0:
            continue  # only losing cities
        clusters = sorted(by_city[city], key=lambda x: x[4])  # ascending pnl (worst first)
        print(f"\n  {city}: total_pnl=${total_pnl:+.2f}  "
              f"({sum(c for _, _, c, _, _ in clusters)} closed trades)")
        # Top 3 worst clusters
        for date, side, cnt, wins, cluster_pnl in clusters[:3]:
            pct_of_city = (cluster_pnl / total_pnl * 100) if total_pnl != 0 else 0
            wr = (wins / cnt * 100) if cnt > 0 else 0
            flag = "  *** CONCENTRATION ***" if pct_of_city > 50 else ""
            print(f"    {date}  {side:<4}  n={cnt:<3}  "
                  f"wins={wins}  WR={wr:>5.1f}%  pnl=${cluster_pnl:>+9.2f}  "
                  f"({pct_of_city:>5.1f}% of city loss){flag}")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="WB bucket-concentration check (S204 Lead 4(5c))")
    p.add_argument("--since", type=parse_deploy_timestamp, required=True,
                   metavar="YYYYMMDD_HHMMSS",
                   help="Required: deploy-stamp window start.")
    p.add_argument("--clean", action="store_true", default=False,
                   help="Apply contamination CTE from bot_pnl.py (CLEAN scope).")
    p.add_argument("--top-n", type=int, default=10,
                   help="Number of top losing cities to decompose (default 10).")
    return p.parse_args(argv)


if __name__ == "__main__":
    ns = _parse_args(sys.argv[1:])
    asyncio.run(run_concentration_check(
        since_ts=ns.since,
        clean=ns.clean,
        top_n=ns.top_n,
    ))
