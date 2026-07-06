#!/usr/bin/env python3
"""Data-ground the v3 signal collector's population choices — READ-ONLY.

The v3 collector (mirror_v3/signal_collector.py) inherited two population-shaping
filters from the DEAD whale-copy bot, not from data:
  1. a $100 whale-size floor (MIRROR_MIN_WHALE_TRADE_USD)
  2. logging only entry (YES/NO) signals, per stage

But the acceptance gate (bots/mirror_backtest/data_access.py:fetch_signals)
imposes NO whale-size floor and NO stage filter — it consumes every resolved,
price-bounded, deduped row. So a floor applied UPSTREAM in the collector would
remove signal the gate is supposed to measure for itself, and if v3 becomes the
sole writer that signal is gone forever.

This script tests those filters against the existing labeled corpus so the
choice is data-based, not old-bot-based. For every RESOLVED signal the whale
entered `side` at `price`; realized entry edge = (1 if resolution==side else 0)
- price (the Phase-B counterfactual: would copying this print at its own entry
price have been +EV?). We bucket that edge by whale_trade_usd and by stage.

READ IT AS: if the sub-$100 buckets' edge CI straddles 0 while >=$100 is clearly
positive, the floor is data-justified — keep it. If sub-$100 ALSO carries edge,
the floor destroys signal the gate wants — drop it from the collector and let the
gate set thresholds (algo proposes, backtest disposes).

NOT bot P&L (scripts/bot_pnl.py cannot bucket rejected-signal edge by trade size)
— this is a dedicated read-only verifier following the verify_salvage_data.py
pattern: SELECT-only, statement_timeout, windowed, degrades to a REVIEW row on
timeout rather than hammering the shared live DB.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && \
      sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
      venv/bin/python scripts/verify_signal_population.py                 # 30d window
    ... scripts/verify_signal_population.py --days 90 --timeout 60        # deeper, quiet DB
    ... scripts/verify_signal_population.py --dedup                       # gate-shape dedup

Every number printed is UNVERIFIED until this runs on the VPS — there is no DB in
a fresh clone. Provenance rule (CLAUDE.md 7/8/10): cite THIS script's output, not
the old RC-diagnostic comment, when defending the floor.
"""
from __future__ import annotations

import argparse
import asyncio
import math
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

from base_engine.data.database import Database

load_dotenv()

# Realized entry edge for a resolved binary signal: pays 1 if the market resolved
# to the side the whale entered, minus what they paid (price). Averaged, this is
# the counterfactual copy edge at the whale's own entry price.
_EDGE = "(CASE WHEN resolution = side THEN 1.0 ELSE 0.0 END) - price"

# Common filter: resolved, real side, gate price bounds (data_access uses 0.02/0.98).
_BASE_WHERE = (
    "resolution IN ('YES','NO') AND side IN ('YES','NO') "
    "AND price IS NOT NULL AND price > 0.02 AND price < 0.98 "
    "AND event_time >= NOW() - make_interval(days => :days)"
)

_BUCKET_CASE = (
    "CASE "
    "WHEN whale_trade_usd IS NULL OR whale_trade_usd <= 0 THEN '0: unknown' "
    "WHEN whale_trade_usd < 25 THEN '1: <$25' "
    "WHEN whale_trade_usd < 100 THEN '2: $25-100' "
    "WHEN whale_trade_usd < 500 THEN '3: $100-500' "
    "ELSE '4: >=$500' END"
)

# --dedup replicates the gate's DISTINCT ON (trader, market, side) first-print
# collapse so the shape matches what fetch_signals actually trains on.
_INNER_DEDUP = f"""
    SELECT DISTINCT ON (trader_address, market_id, side)
           whale_trade_usd, rejection_stage,
           {_EDGE} AS edge
    FROM mirror_rejected_signals
    WHERE {_BASE_WHERE}
    ORDER BY trader_address, market_id, side, event_time ASC
"""

_INNER_RAW = f"""
    SELECT whale_trade_usd, rejection_stage, {_EDGE} AS edge
    FROM mirror_rejected_signals
    WHERE {_BASE_WHERE}
"""


def _agg_sql(inner: str, group_expr: str) -> str:
    return (
        f"SELECT {group_expr} AS grp, COUNT(*) AS n, "
        f"AVG(edge) AS mean_edge, STDDEV(edge) AS sd "
        f"FROM ({inner}) q GROUP BY grp ORDER BY grp"
    )


class Verifier:
    def __init__(self, db: Database, days: int, timeout_s: int, dedup: bool):
        self.db = db
        self.days = days
        self.timeout_ms = timeout_s * 1000
        self.inner = _INNER_DEDUP if dedup else _INNER_RAW
        self.dedup = dedup
        self.rows: list[tuple[str, list]] = []  # (section_title, [ (grp,n,mean,sd,note) ])
        self.notes: list[str] = []

    async def _rows(self, session, sql: str) -> Any:
        try:
            await session.execute(text(f"SET LOCAL statement_timeout = {self.timeout_ms}"))
            r = await session.execute(text(sql), {"days": self.days})
            return r.fetchall()
        except Exception as e:
            try:
                await session.rollback()
            except Exception:
                pass
            msg = str(e).lower()
            return "TIMEOUT" if ("timeout" in msg or "canceling statement" in msg) else f"ERR:{str(e)[:80]}"

    @staticmethod
    def _ci95(n: int, mean: float, sd: float) -> tuple[float, float]:
        if not n or n < 2 or sd is None:
            return (float("nan"), float("nan"))
        se = sd / math.sqrt(n)
        return (mean - 1.96 * se, mean + 1.96 * se)

    def _fmt_group(self, res) -> list:
        out = []
        if isinstance(res, str):
            self.notes.append(res)
            return out
        for grp, n, mean, sd in res:
            n = int(n or 0)
            mean = float(mean) if mean is not None else float("nan")
            sd = float(sd) if sd is not None else None
            lo, hi = self._ci95(n, mean, sd or 0.0)
            straddles = (lo <= 0.0 <= hi) if not math.isnan(lo) else True
            note = "edge~0 (CI includes 0)" if straddles else (
                "POSITIVE edge" if lo > 0 else "NEGATIVE edge")
            out.append((str(grp), n, mean, (lo, hi), note))
        return out

    async def run(self):
        async with self.db.get_session() as s:
            by_bucket = self._fmt_group(await self._rows(s, _agg_sql(self.inner, _BUCKET_CASE)))
            self.rows.append((f"Realized entry edge by whale-size bucket "
                              f"({'gate-dedup' if self.dedup else 'raw rows'}, "
                              f"last {self.days}d)", by_bucket))
            by_stage = self._fmt_group(await self._rows(s, _agg_sql(self.inner, "rejection_stage")))
            self.rows.append(("Realized entry edge by rejection_stage", by_stage))

        self._floor_verdict(by_bucket)

    def _floor_verdict(self, by_bucket: list):
        """The headline: is the $100 floor data-justified?"""
        below = [r for r in by_bucket if r[0] in ("1: <$25", "2: $25-100")]
        above = [r for r in by_bucket if r[0] in ("3: $100-500", "4: >=$500")]

        def _pooled_positive(rows) -> tuple[bool, bool, int]:
            # weighted mean edge + whether ANY sub-bucket is clearly positive
            n = sum(r[1] for r in rows)
            any_pos = any((not math.isnan(r[3][0])) and r[3][0] > 0 for r in rows)
            all_straddle = all(math.isnan(r[3][0]) or r[3][0] <= 0 <= r[3][1] for r in rows)
            return any_pos, all_straddle, n

        if not below or not above:
            self.verdict = ("REVIEW", "insufficient buckets to judge the floor "
                            "(empty window or all rows in one bucket)")
            return
        below_pos, below_straddle, below_n = _pooled_positive(below)
        above_pos, _, above_n = _pooled_positive(above)
        if below_pos:
            self.verdict = ("DROP-FLOOR", f"sub-$100 cohort ({below_n:,} rows) carries "
                            f"positive edge — the $100 floor destroys signal the gate wants. "
                            f"Log the full distribution; let the gate set the threshold.")
        elif below_straddle and above_pos:
            self.verdict = ("KEEP-FLOOR", f"sub-$100 edge ~0 ({below_n:,} rows) while >=$100 "
                            f"carries edge — the floor is data-justified.")
        else:
            self.verdict = ("REVIEW", "neither cohort shows clear edge — the corpus may be "
                            "too small/noisy in this window; widen --days or eyeball counts.")

    def print_report(self):
        print("\n" + "=" * 82)
        print("  v3 signal-population verification — is the collector's $100 whale floor")
        print("  data-justified, or inherited from the dead bot?")
        print(f"  window: last {self.days}d · statement_timeout {self.timeout_ms}ms · "
              f"{'gate-dedup' if self.dedup else 'raw rows'}")
        print("=" * 82)
        for title, groups in self.rows:
            print(f"\n  {title}")
            print(f"    {'bucket'.ljust(14)} {'n'.rjust(10)}  {'mean_edge'.rjust(10)}  "
                  f"{'95% CI'.ljust(20)}  note")
            if not groups:
                print("      (no rows — see notes below)")
            for grp, n, mean, (lo, hi), note in groups:
                ci = f"[{lo:+.3f}, {hi:+.3f}]" if not math.isnan(lo) else "[n/a]"
                print(f"    {grp.ljust(14)} {n:>10,}  {mean:>+10.4f}  {ci.ljust(20)}  {note}")
        if self.notes:
            print("\n  NOTES:")
            for nline in self.notes:
                print(f"    - {nline}")
        v, why = self.verdict
        print("\n" + "-" * 82)
        print(f"  FLOOR VERDICT: {v}")
        print(f"    {why}")
        print("-" * 82)
        print("  Reminder: edge = mean( (resolution==side ? 1:0) - price ). Positive means")
        print("  the whales' entries in that bucket beat their own entry price on realized")
        print("  outcomes. This is directional counterfactual edge, not a live-tradable P&L.")
        print("=" * 82 + "\n")


async def _main(days: int, timeout_s: int, dedup: bool) -> int:
    db = Database()
    await db.init()
    try:
        if db.session_factory is None:
            print("DB init failed — DATABASE_URL invalid/unreachable.")
            return 2
        v = Verifier(db, days=days, timeout_s=timeout_s, dedup=dedup)
        await v.run()
        v.print_report()
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only v3 signal-population / whale-floor verifier")
    ap.add_argument("--days", type=int, default=30, help="lookback window in days (default 30)")
    ap.add_argument("--timeout", type=int, default=30, help="per-statement timeout in seconds (default 30)")
    ap.add_argument("--dedup", action="store_true",
                    help="apply the gate's DISTINCT ON (trader,market,side) first-print collapse")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.days, args.timeout, args.dedup)))
