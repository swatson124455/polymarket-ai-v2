"""Read-only data layer for the harness. Execution is gated on M0-DB
(MB_REBUILD_PLAN.md milestone M2) — run scripts/verify_salvage_data.py on the
VPS first; every count/coverage figure this module touches is UNVERIFIED
until then.

Cleaning recipe applied to mirror_rejected_signals (salvage manifest §Tier-2,
verifier-corrected): first signal per (trader, market, side) via DISTINCT ON;
resolution IS NOT NULL; price bounds. The gate-stage stale-price window is a
gate-stage-only concern and does not apply to pre_gate whale prints.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

_SIGNALS_SQL = """
SELECT DISTINCT ON (r.trader_address, r.market_id, r.side)
       r.trader_address, r.market_id, r.token_id, r.side, r.price,
       r.event_time, r.resolution, r.rejection_stage
FROM mirror_rejected_signals r
WHERE r.resolution IN ('YES', 'NO')
  AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
  AND r.event_time >= :t0 AND r.event_time < :t1
ORDER BY r.trader_address, r.market_id, r.side, r.event_time ASC
"""

# Precise source: the stored ladder nearest AT-OR-BEFORE the signal on the
# same market (shadow_fills is written at MB decision time, so a snapshot at
# or just before the signal reflects the pre-trade book).
_LADDER_SQL = """
SELECT book_snapshot, created_at FROM shadow_fills
WHERE bot_name = 'MirrorBot' AND market_id = :mid
  AND book_snapshot IS NOT NULL
  AND created_at <= :at AND created_at >= :lo
ORDER BY created_at DESC LIMIT 1
"""

_OB_ROW_SQL = """
SELECT best_bid, best_ask, mid_price, spread,
       bid_depth_1pct, ask_depth_1pct, bid_depth_5pct, ask_depth_5pct,
       snapshot_time
FROM orderbook_snapshots
WHERE token_id = :tid AND snapshot_time <= :at AND snapshot_time >= :lo
ORDER BY snapshot_time DESC LIMIT 1
"""


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def fetch_signals(
    db, t0: datetime, t1: datetime, pmin: float = 0.02, pmax: float = 0.98,
) -> list[dict]:
    async with db.get_session() as s:
        rows = (await s.execute(text(_SIGNALS_SQL), {
            "pmin": pmin, "pmax": pmax, "t0": _naive(t0), "t1": _naive(t1),
        })).fetchall()
    return [dict(r._mapping) for r in rows]


async def attach_fill_sources(
    db, signals: list[dict],
    ladder_staleness_s: int = 120,
    ob_staleness_s: int = 120,
) -> list[dict]:
    """Attach book_snapshot (precise) and/or ob_row (coarse) to each signal.
    Signals ending up with neither are left bare — replay counts them
    uncovered rather than dropping them silently."""
    for sig in signals:
        at = _naive(sig["event_time"])
        async with db.get_session() as s:
            lad = (await s.execute(text(_LADDER_SQL), {
                "mid": sig["market_id"], "at": at,
                "lo": at - timedelta(seconds=ladder_staleness_s),
            })).fetchone()
            ob = None
            if sig.get("token_id"):
                ob = (await s.execute(text(_OB_ROW_SQL), {
                    "tid": sig["token_id"], "at": at,
                    "lo": at - timedelta(seconds=ob_staleness_s),
                })).fetchone()
        if lad is not None and lad[0]:
            sig["book_snapshot"] = lad[0]
        if ob is not None:
            sig["ob_row"] = dict(ob._mapping)
    return signals
