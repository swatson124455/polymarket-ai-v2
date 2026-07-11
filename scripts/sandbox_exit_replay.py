#!/usr/bin/env python3
"""SANDBOX EXPERIMENT #5 — exit-ladder replay vs hold-to-resolution.

Question: every +EV counterfactual on file assumes hold-to-resolution. Does
MB's real exit ladder (bots/mirror_scoring/exit_replay.py, faithful port of
the live ladder) ADD or DESTROY per-$ edge on the same signals, same fills?

PRE-REGISTERED VERDICT (locked 2026-07-11, before first run — do not move):
  Delta = edge_net(ladder) - edge_net(hold), same signal, same entry fill.
  Aggregated as mean Delta per market (cluster), bootstrap over markets
  (gate._boot_p_edge, seed fixed).
    P(Delta > 0) >= 0.95  -> LADDER ADDS EV (keep ladder in counterfactuals)
    P(Delta < 0) >= 0.95  -> LADDER DESTROYS EV vs hold (recommend ladder
                             re-evaluation to the MB king session)
    otherwise             -> INCONCLUSIVE (report with coverage, no action)
  Minimum evidence: >= 30 distinct markets with a price path, else NO VERDICT.

Read-only: SELECTs only. Run from a /tmp clone with PYTHONPATH, never the
live tree. Results are research-grade recommendations for the MB king
session — nothing here ships a strategy or config change.

Known caveats (report carries them):
  * trader-sell exit rung not replayed (no per-whale sell stream attached
    here) -> ladder is replayed slightly LESS active than live.
  * price path = orderbook_snapshots mid at 60s buckets, 30-day retention:
    exits fill at MID, real exits cross the spread -> ladder result is
    slightly OPTIMISTIC. Both caveats bias in KNOWN directions and are
    printed with every run.
  * hard_stop_pct not replayed (operator-set shared limit, None default).

Usage (operator one-liner clones the session branch to /tmp first):
  DATABASE_URL=... python scripts/sandbox_exit_replay.py \
      --t0 2026-06-05 --t1 2026-07-05 [--max-signals 5000] [--size-usd 100]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bots.mirror_backtest.data_access import attach_fill_sources
from bots.mirror_backtest.gate import _boot_p_edge
from bots.mirror_backtest.replay import Decision, replay_signals
from bots.mirror_scoring.exit_replay import replay_exit

# Same cleaning recipe as bots/mirror_backtest/data_access._SIGNALS_SQL
# (DISTINCT ON first signal per trader/market/side; resolution known; price
# bounds) plus resolved_at, which the exit ladder needs for TTR rungs.
_SIGNALS_SQL = """
SELECT DISTINCT ON (r.trader_address, r.market_id, r.side)
       r.trader_address, r.market_id, r.token_id, r.side, r.price,
       r.event_time, r.resolution, r.resolved_at, r.rejection_stage
FROM mirror_rejected_signals r
WHERE r.resolution IN ('YES', 'NO')
  AND r.resolved_at IS NOT NULL
  AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
  AND r.event_time >= :t0 AND r.event_time < :t1
ORDER BY r.trader_address, r.market_id, r.side, r.event_time ASC
"""

_PATH_SQL = """
SELECT snapshot_time, mid_price FROM orderbook_snapshots
WHERE token_id = :tid AND snapshot_time > :entry AND snapshot_time <= :res
ORDER BY snapshot_time ASC
"""


class _Db:
    """Minimal shim exposing get_session() for data_access reuse."""

    def __init__(self, url: str):
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        self._engine = create_async_engine(url, pool_size=2, max_overflow=0)
        self._maker = async_sessionmaker(self._engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_session(self):
        async with self._maker() as s:
            yield s

    async def dispose(self):
        await self._engine.dispose()


def make_exit_fn(entry_confidence: float):
    """exit_fn(sig, fill) per the replay.py seam: returns o_x (per-$ outcome
    under the ladder). Falls back to resolution outcome when no price path —
    replay.py treats exceptions the same way, but we stay explicit."""

    def _fn(sig: dict, fill) -> float:
        series = sig.get("_price_series") or []
        res_at = sig.get("resolved_at")
        outcome = 1.0 if sig.get("resolution") == str(sig.get("side")).upper() else 0.0
        if not series or res_at is None:
            return outcome
        r = replay_exit(
            entry_price=fill.price,
            entry_time=sig["event_time"],
            price_series=series,
            resolution_time=res_at,
            resolution_outcome=outcome,
            entry_confidence=entry_confidence,
            trader_sell_times=None,  # caveat: rung not replayed
        )
        sig["_exit_reason"] = r.exit_reason
        return r.o_x

    return _fn


def summarize(delta_by_market: dict, reasons: dict, meta: dict) -> dict:
    edges = np.array([float(np.mean(v)) for v in delta_by_market.values()])
    n_mkts = int(edges.size)
    p_pos, mu = _boot_p_edge(edges, 2000, 20260711)
    p_neg, _ = _boot_p_edge(-edges, 2000, 20260711)
    if n_mkts < 30:
        verdict = f"NO VERDICT (markets with path {n_mkts} < 30 minimum)"
    elif p_pos >= 0.95:
        verdict = "LADDER ADDS EV (P(Delta>0) >= 0.95)"
    elif p_neg >= 0.95:
        verdict = "LADDER DESTROYS EV vs hold (P(Delta<0) >= 0.95)"
    else:
        verdict = "INCONCLUSIVE"
    return {
        "verdict": verdict,
        "mean_delta_per_market": None if np.isnan(mu) else round(mu, 5),
        "p_delta_positive": round(p_pos, 4),
        "p_delta_negative": round(p_neg, 4),
        "n_markets_with_path": n_mkts,
        "exit_reasons": reasons,
        **meta,
        "caveats": [
            "trader_sell rung not replayed (ladder under-active vs live)",
            "exits fill at 60s-bucket MID (ladder slightly optimistic)",
            "price paths limited by orderbook_snapshots 30-day retention",
            "counterfactual research-grade; UNVERIFIED until independently rerun",
        ],
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", required=True)
    ap.add_argument("--t1", required=True)
    ap.add_argument("--pmin", type=float, default=0.02)
    ap.add_argument("--pmax", type=float, default=0.98)
    ap.add_argument("--size-usd", type=float, default=100.0)
    ap.add_argument("--confidence", type=float, default=0.55)
    ap.add_argument("--max-signals", type=int, default=5000)
    ap.add_argument("--out", default="/tmp/sandbox_exit_replay_result.json")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 2
    db = _Db(url)
    try:
        t0 = datetime.fromisoformat(args.t0)
        t1 = datetime.fromisoformat(args.t1)
        async with db.get_session() as s:
            rows = (await s.execute(text(_SIGNALS_SQL), {
                "pmin": args.pmin, "pmax": args.pmax, "t0": t0, "t1": t1,
            })).fetchall()
        signals = [dict(r._mapping) for r in rows]
        capped = len(signals) > args.max_signals
        if capped:
            print(f"NO-SILENT-CAPS: {len(signals)} signals in window, "
                  f"replaying first {args.max_signals} by (trader,market,side) "
                  f"order — rerun with --max-signals 0 sentinel unsupported; "
                  f"widen window slicing instead.")
            signals = signals[: args.max_signals]

        signals = await attach_fill_sources(db, signals)

        n_path = 0
        for sig in signals:
            if not (sig.get("book_snapshot") or sig.get("ob_row")):
                continue
            async with db.get_session() as s:
                ticks = (await s.execute(text(_PATH_SQL), {
                    "tid": sig.get("token_id"),
                    "entry": sig["event_time"],
                    "res": sig["resolved_at"],
                })).fetchall()
            if ticks:
                sig["_price_series"] = [
                    (r[0], float(r[1])) for r in ticks if r[1] is not None
                ]
                n_path += 1

        rule = lambda _sig: Decision(size_usd=args.size_usd)  # accept-all
        hold = replay_signals(signals, rule)
        ladder = replay_signals(
            signals, rule, exit_fn=make_exit_fn(args.confidence)
        )

        # Pair by (market, trader) order — identical inputs, identical fills,
        # so replayed lists align 1:1; assert rather than assume.
        assert len(hold.replayed) == len(ladder.replayed), "replay misalignment"
        delta_by_market: dict[str, list] = {}
        for h, l in zip(hold.replayed, ladder.replayed):
            assert h.market_id == l.market_id and h.trader == l.trader
            delta_by_market.setdefault(h.market_id, []).append(
                l.edge_net - h.edge_net
            )
        reasons: dict[str, int] = {}
        for sig in signals:
            r = sig.get("_exit_reason")
            if r:
                reasons[r] = reasons.get(r, 0) + 1

        out = summarize(delta_by_market, reasons, {
            "window": [args.t0, args.t1],
            "n_signals_window": len(rows),
            "n_signals_replayed": hold.n_signals,
            "n_uncovered_no_fill_source": hold.n_uncovered,
            "n_with_price_path": n_path,
            "n_precise": hold.n_precise,
            "n_coarse": hold.n_coarse,
            "mean_edge_hold": round(
                float(np.mean([r.edge_net for r in hold.replayed])), 5
            ) if hold.replayed else None,
            "mean_edge_ladder": round(
                float(np.mean([r.edge_net for r in ladder.replayed])), 5
            ) if ladder.replayed else None,
            "capped": capped,
        })
        print(json.dumps(out, indent=2, default=str))
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\nwrote {args.out} — copy to /opt/pa2-shared for durability")
        return 0
    finally:
        await db.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
