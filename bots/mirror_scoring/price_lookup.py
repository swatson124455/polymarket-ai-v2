"""Staleness-safe historical price access for the scoring engine.

Database.get_price_at (database.py:3279) is NOT used here — verified
2026-07-02 that it has no staleness bound, does not return the sample
timestamp, and silently mixes YES/NO tokens when token_id is omitted.
This module runs its own queries (read-only) through db.get_session()
without modifying any shared module.

market_prices density is bimodal (near-tick for WS-subscribed markets,
~hourly CLOB backfill elsewhere), so every lookup returns its own
timestamp and every batch reports coverage rho. Trades-table prints are
used as a secondary source where market_prices is sparse.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def price_at(
    db,
    token_id: str,
    at_time: datetime,
    max_staleness_s: int = 30,
) -> Optional[tuple[float, datetime]]:
    """Latest price for token_id at-or-before at_time, but only if the
    sample is within max_staleness_s. Returns (price, sample_ts) or None.
    token_id is REQUIRED (no side mixing)."""
    if not token_id:
        return None
    at_n = _naive(at_time)
    lo = at_n - timedelta(seconds=max_staleness_s)
    sql = text(
        "SELECT price, timestamp FROM market_prices "
        "WHERE token_id = :tid AND timestamp <= :at AND timestamp >= :lo "
        "ORDER BY timestamp DESC LIMIT 1"
    )
    async with db.get_session() as s:
        row = (await s.execute(sql, {"tid": token_id, "at": at_n, "lo": lo})).fetchone()
    if row is None:
        # Secondary source: trade prints (global via WS trade events).
        sql2 = text(
            "SELECT price, timestamp FROM trades "
            "WHERE token_id = :tid AND timestamp <= :at AND timestamp >= :lo "
            "ORDER BY timestamp DESC LIMIT 1"
        )
        async with db.get_session() as s:
            row = (await s.execute(sql2, {"tid": token_id, "at": at_n, "lo": lo})).fetchone()
    if row is None:
        return None
    return float(row[0]), row[1]


async def price_series(
    db, token_id: str, start: datetime, end: datetime, max_rows: int = 20000
) -> list[tuple[datetime, float]]:
    """Chronological (ts, price) for the token across [start, end], merging
    market_prices with trade prints, deduped per timestamp."""
    if not token_id:
        return []
    a, b = _naive(start), _naive(end)
    out: dict[datetime, float] = {}
    for table in ("market_prices", "trades"):
        sql = text(
            f"SELECT timestamp, price FROM {table} "
            "WHERE token_id = :tid AND timestamp >= :a AND timestamp <= :b "
            "ORDER BY timestamp ASC LIMIT :lim"
        )
        async with db.get_session() as s:
            rows = (await s.execute(
                sql, {"tid": token_id, "a": a, "b": b, "lim": max_rows}
            )).fetchall()
        for ts, px in rows:
            out.setdefault(ts, float(px))
    return sorted(out.items())


async def coverage_rho(
    db,
    lookups: list[tuple[str, datetime]],
    delta_s: int,
    max_staleness_s: int,
) -> tuple[float, list[bool]]:
    """Fraction of (token_id, entry_time) lookups with a usable sample at
    entry_time + delta_s. Returns (rho, per-lookup covered flags)."""
    flags: list[bool] = []
    for token_id, t in lookups:
        got = await price_at(
            db, token_id, t + timedelta(seconds=delta_s), max_staleness_s
        )
        flags.append(got is not None)
    rho = sum(flags) / len(flags) if flags else 0.0
    return rho, flags
