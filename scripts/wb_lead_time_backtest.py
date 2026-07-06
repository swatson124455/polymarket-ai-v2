#!/usr/bin/env python3
"""
6O — WeatherBot lead-time optimal window backtest.

================================================================================
⚠  OUTPUT IS NOT CANONICAL P&L. DO NOT CITE WITHOUT VALIDATION.                ⚠
================================================================================
This script produces bucket-level win-rate and realized-P&L numbers from its own
SQL against `trade_events`. Per Rule Zero and CLAUDE.md Forbidden Pattern 7, any
P&L, win-rate, or trade-count claim in a report, handoff, or memory entry MUST
source from `scripts/bot_pnl.py` — not from this script.

Before using any number this script emits in a document:
  1. Run `scripts/bot_pnl.py WeatherBot <hours>` covering the same time window.
  2. Cross-check this script's bucket totals against bot_pnl.py's totals.
  3. If totals disagree, `bot_pnl.py` is authoritative — this script's bucket
     logic is wrong and its breakdown is also wrong. Treat as quarantined.
  4. If totals agree, the bucket-level breakdown can be trusted at face value
     for the same window, and bot_pnl.py should still be cited as the source.

Future work: refactor this script to call `bot_pnl.py`'s resolution logic
internally rather than producing parallel SQL. Current shape is a Protocol-5-ish
anti-pattern (data surface doesn't match canonical source). Deferred pending
bucket-level measurement need vs rewrite cost.
================================================================================

Buckets resolved WB trades by entry `lead_time_hours` and reports WR + realized P&L per bucket.
Joins ENTRY to the latest RESOLUTION/EXIT per (market_id, side) to get realized_pnl.

Usage (local or VPS):
    PYTHONPATH=/opt/polymarket-ai-v2 python3 scripts/wb_lead_time_backtest.py
    PYTHONPATH=/opt/polymarket-ai-v2 python3 scripts/wb_lead_time_backtest.py --exclude-outliers

--exclude-outliers filters the 2026-03-26 NYC NO correlated-blowup event (a single-day /
single-city / single-side trade cluster that dominated the longest-lead bucket and masked
the lead-time signal — the "correlated blowups" pattern documented in WB S119 memory).
Keep off to see raw data; turn on to see non-event-driven lead-time behavior. Per the
aggregate-statistics bucket warning (§Protocol candidates in S172_CONSOLIDATED_PLAN.md),
any bucket dominated by a single (date × city × side) triple should be presented separately,
not as an in-aggregate data point.
"""
import argparse
import asyncio

from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

BUCKETS_SQL = """
WITH entry_events AS (
  SELECT e.market_id, e.side, e.size, e.price AS entry_price,
         CAST(e.event_data->>'lead_time_hours' AS DOUBLE PRECISION) AS lead_h,
         e.event_data->>'city' AS city,
         e.event_time AS entry_ts
  FROM trade_events e
  WHERE e.bot_name='WeatherBot' AND e.event_type='ENTRY'
    AND e.event_data ? 'lead_time_hours'
    {extra_filter}
),
resolved AS (
  SELECT DISTINCT ON (r.market_id, r.side) r.market_id, r.side, r.realized_pnl
  FROM trade_events r
  WHERE r.bot_name='WeatherBot'
    AND r.event_type IN ('RESOLUTION','EXIT')
    AND r.realized_pnl IS NOT NULL
  ORDER BY r.market_id, r.side, r.event_time DESC
),
joined AS (
  SELECT e.lead_h, e.side, e.entry_price,
         CAST(r.realized_pnl AS DOUBLE PRECISION) AS rpnl,
         CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END AS win
  FROM entry_events e
  JOIN resolved r ON r.market_id = e.market_id AND r.side = e.side
)
SELECT CASE
         WHEN lead_h < 12   THEN '00_<12h'
         WHEN lead_h < 24   THEN '01_12-24h'
         WHEN lead_h < 48   THEN '02_24-48h'
         WHEN lead_h < 72   THEN '03_48-72h'
         WHEN lead_h < 96   THEN '04_72-96h'
         WHEN lead_h < 120  THEN '05_96-120h'
         WHEN lead_h < 144  THEN '06_120-144h'
         WHEN lead_h < 168  THEN '07_144-168h'
         ELSE                     '08_>=168h'
       END AS bucket,
       COUNT(*) AS n,
       ROUND(AVG(win)::numeric * 100, 1) AS wr_pct,
       ROUND(SUM(rpnl)::numeric, 2) AS total_pnl,
       ROUND(AVG(rpnl)::numeric, 2) AS avg_pnl_per_trade,
       ROUND(AVG(entry_price)::numeric, 3) AS avg_entry_price
FROM joined
GROUP BY 1
ORDER BY 1;
"""


async def run(exclude_outliers: bool) -> None:
    db = Database()
    await db.init()
    extra = ""
    if exclude_outliers:
        extra = (
            "AND NOT (e.event_time::date='2026-03-26' "
            "AND e.event_data->>'city'='New York City' AND e.side='NO')"
        )
    sql = BUCKETS_SQL.format(extra_filter=extra)
    async with db.get_session() as s:
        from sqlalchemy import text
        rows = await s.execute(text(sql))
        header = f"{'bucket':<14} {'n':>5} {'wr%':>6} {'tot_pnl':>12} {'avg_pnl':>9} {'avg_px':>7}"
        print(header)
        print("-" * len(header))
        for r in rows.fetchall():
            print(f"{r.bucket:<14} {r.n:>5} {r.wr_pct:>6} {r.total_pnl:>12} {r.avg_pnl_per_trade:>9} {r.avg_entry_price:>7}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude-outliers", action="store_true",
                    help="Exclude 2026-03-26 NYC NO correlated-blowup (29 trades, -$134K)")
    args = ap.parse_args()
    asyncio.run(run(args.exclude_outliers))
