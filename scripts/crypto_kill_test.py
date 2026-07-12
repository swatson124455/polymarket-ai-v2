#!/usr/bin/env python3
"""Crypto kill-test — does the crypto whale edge survive OUR latency?

WHY (MB_STATE §"NEXT ACTIONS"/backlog, [build, unblocked]): crypto is 73% of
the ~5.06M labeled whale signals (docs/m0_db_results_2026-07-02.md) and the
standing hypothesis (MB_STATE strategy note) is that it is a LATENCY edge —
the whale's information is priced in seconds, so a copier at ~10s detection
buys after the move: un-tailable. This script runs that hypothesis as a
formal kill test so crypto can be dropped (or kept) on evidence instead of
vibes. It is about focus, not new money.

METHOD (reuses the AUDITED harness end-to-end; nothing re-derived):
  population : mirror_rejected_signals first prints (DISTINCT ON trader,
               market, side; resolution labeled; price bounds) JOINed to
               markets.category, bucketed by the tested
               find_copyable_traders.bucket_category -> keep 'crypto'.
               Deterministic md5-ordered subsample of --max-signals
               (REPORTED, never silent).
  fill       : bots/mirror_backtest coarse_fill (pessimistic, worst-of-
               bucket) on the FIRST orderbook_snapshots row with
               snapshot_time in (t+lag, t+lag+staleness] — the first book
               we could hit acting `lag` seconds after the whale's print.
               No row there = uncovered at that lag (reported).
  edge       : hold-to-resolution via bots/mirror_backtest/replay.py
               (edge_net = outcome - fill*fill_fraction - fee), pooled with
               a MARKET-clustered bootstrap.
  lags       : 0 (instant-copy ceiling), 10 (decision lag: measured REST
               detection; the on-chain watcher does 2-4s), 30 (robustness).

PRE-REGISTERED VERDICT (fixed before any data is seen; econ floor +0.02
per docs/MB_STATE.md walk-forward convention), evaluated at --decision-lag:
  KILLED       coverage >= --min-cov AND covered markets >= --min-markets
               AND bootstrap upper95(pooled edge) < +0.02 — with 95%
               confidence the crypto edge at our latency is below the
               economic floor. Crypto is formally dropped.
  SURVIVES     same power conditions AND P(edge > +0.02) >= 0.95 — crypto
               stays in scope (would contradict the latency hypothesis).
  INCONCLUSIVE anything else — the test says nothing; widen data (more
               signals, longer window), NEVER loosen thresholds.
The lag-0 column is the paired control: if edge collapses from lag 0 to the
decision lag, the latency trap is confirmed specifically; if lag 0 is
already below floor, crypto is dead at ANY latency (kill stands either way,
labeled differently). Coarse-model pessimism cancels in that comparison.

SAFETY: READ-ONLY DB (SELECTs under the session's timeouts). No network, no
orders, no writes anywhere but --out. Shared modules are imported, never
modified.

INVOCATION (VPS, from a /tmp clone — never the deployed tree):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbre \
      venv/bin/python /tmp/mbre/scripts/crypto_kill_test.py \
      --days 180 --max-signals 10000 | tee /tmp/crypto_kill.log
    ... --self-test   # offline logic check, no DB
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import find_copyable_traders as fc  # noqa: E402

_SIGNALS_SQL = """
SELECT q.trader_address, q.market_id, q.token_id, q.side, q.price,
       q.event_time, q.resolution, q.category
FROM (
    SELECT DISTINCT ON (r.trader_address, r.market_id, r.side)
           r.trader_address, r.market_id, r.token_id, r.side, r.price,
           r.event_time, r.resolution, m.category
    FROM mirror_rejected_signals r
    JOIN markets m ON m.condition_id = r.market_id
    WHERE r.resolution IN ('YES', 'NO')
      AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
      AND r.event_time >= :t0 AND r.event_time < :t1
      AND r.token_id IS NOT NULL
    ORDER BY r.trader_address, r.market_id, r.side, r.event_time ASC
) q
ORDER BY md5(q.market_id || q.trader_address || :salt)
LIMIT :cap
"""

# First book we could HIT acting `lag` seconds after the print: earliest
# snapshot strictly after t+lag, within staleness. ASC — never a pre-signal
# book (that would erase the very move the test measures).
_OB_AT_LAG_SQL = """
SELECT best_bid, best_ask, mid_price, spread,
       bid_depth_1pct, ask_depth_1pct, bid_depth_5pct, ask_depth_5pct,
       snapshot_time
FROM orderbook_snapshots
WHERE token_id = :tid AND snapshot_time > :lo AND snapshot_time <= :hi
ORDER BY snapshot_time ASC LIMIT 1
"""


# ── Pure, offline-testable core ──────────────────────────────────────────────
def keep_crypto(rows: list[dict]) -> list[dict]:
    """Bucket with the tested bucketer; keep only 'crypto'."""
    return [r for r in rows
            if fc.bucket_category(r.get("category")) == "crypto"]


def per_market_edges(replayed: list) -> dict[str, float]:
    """Market-clustered aggregation: mean net edge per market."""
    buckets: dict[str, list[float]] = {}
    for r in replayed:
        buckets.setdefault(r.market_id, []).append(r.edge_net)
    return {m: sum(v) / len(v) for m, v in buckets.items()}


def boot_pooled(edges: list[float], n_boot: int, seed: int,
                floor: float) -> dict:
    """Bootstrap the pooled mean by resampling MARKETS (clusters).
    Returns mean, p_above_floor, lower95, upper95."""
    import numpy as np
    e = np.asarray(edges, dtype=float)
    if e.size == 0:
        return {"mean": None, "p_above_floor": None,
                "lower95": None, "upper95": None}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, e.size, size=(n_boot, e.size))
    means = e[idx].mean(axis=1)
    return {"mean": float(e.mean()),
            "p_above_floor": float((means > floor).mean()),
            "lower95": float(np.quantile(means, 0.025)),
            "upper95": float(np.quantile(means, 0.975))}


def kill_verdict(coverage: Optional[float], n_markets: int, stats: dict,
                 min_cov: float, min_markets: int, floor: float) -> str:
    """The pre-registered rule from the module docstring. Fixed before data."""
    if (coverage is None or coverage < min_cov or n_markets < min_markets
            or stats.get("upper95") is None):
        return "INCONCLUSIVE"
    if stats["upper95"] < floor:
        return "KILLED"
    if stats["p_above_floor"] >= 0.95:
        return "SURVIVES"
    return "INCONCLUSIVE"


def paired_decay(rep0: list, rep_lag: list) -> Optional[dict]:
    """Mean edge drop lag0 -> decision lag on the COMMON covered signals
    (paired: model pessimism and market mix cancel). Keyed by
    (market, trader) — the estimand's identity."""
    e0 = {(r.market_id, r.trader): r.edge_net for r in rep0}
    el = {(r.market_id, r.trader): r.edge_net for r in rep_lag}
    common = sorted(set(e0) & set(el))
    if not common:
        return None
    d = [e0[k] - el[k] for k in common]
    return {"n_paired": len(common),
            "mean_edge_lag0": sum(e0[k] for k in common) / len(common),
            "mean_edge_lag": sum(el[k] for k in common) / len(common),
            "mean_decay": sum(d) / len(d)}


# ── Network/DB run ───────────────────────────────────────────────────────────
async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    if os.path.exists("/opt/pa2-shared/.env"):
        load_dotenv("/opt/pa2-shared/.env")
    from sqlalchemy import text

    from base_engine.data.database import Database
    from bots.mirror_backtest.replay import Decision, replay_signals

    db = Database()
    t1 = datetime.now(timezone.utc).replace(tzinfo=None)
    t0 = t1 - timedelta(days=args.days)
    print(f"fetching crypto-candidate first prints {t0:%Y-%m-%d}..{t1:%Y-%m-%d} "
          f"(md5-sampled cap {args.max_signals} — REPORTED, not silent)",
          file=sys.stderr)
    async with db.get_session() as s:
        rows = (await s.execute(text(_SIGNALS_SQL), {
            "pmin": args.pmin, "pmax": args.pmax, "t0": t0, "t1": t1,
            "salt": str(args.seed), "cap": args.max_signals,
        })).fetchall()
    sigs = keep_crypto([dict(r._mapping) for r in rows])
    print(f"  fetched={len(rows)} rows, crypto-bucketed={len(sigs)} "
          f"(bucketer: find_copyable_traders.bucket_category)", file=sys.stderr)
    if not sigs:
        print("no crypto signals in the window — INCONCLUSIVE by construction")
        return 2

    lags = sorted({int(x) for x in args.lags.split(",")} | {0, args.decision_lag})
    results: dict[str, Any] = {}
    reports: dict[int, Any] = {}
    rule = lambda _sig: Decision(size_usd=args.size_usd)  # noqa: E731
    for lag in lags:
        covered = 0
        lag_sigs = []
        for i, sig in enumerate(sigs):
            at = sig["event_time"]
            async with db.get_session() as s:
                ob = (await s.execute(text(_OB_AT_LAG_SQL), {
                    "tid": sig["token_id"],
                    "lo": at + timedelta(seconds=lag),
                    "hi": at + timedelta(seconds=lag + args.staleness),
                })).fetchone()
            sc = dict(sig)
            if ob is not None:
                sc["ob_row"] = dict(ob._mapping)
                covered += 1
            lag_sigs.append(sc)
            if (i + 1) % 1000 == 0:
                print(f"  lag={lag}s  {i + 1}/{len(sigs)} books attached "
                      f"({covered} covered)", file=sys.stderr)
        rep = replay_signals(lag_sigs, rule, fee_roundtrip=args.fee)
        reports[lag] = rep
        mk = per_market_edges(rep.replayed)
        stats = boot_pooled(list(mk.values()), args.n_boot,
                            args.seed + lag, args.econ_floor)
        coverage = covered / len(sigs) if sigs else None
        results[str(lag)] = {
            "n_signals": len(sigs), "n_covered": covered,
            "coverage": round(coverage, 4) if coverage is not None else None,
            "n_markets": len(mk), **stats}
        print(f"  lag={lag:>3}s  covered={covered}/{len(sigs)} "
              f"markets={len(mk)}  mean={stats['mean']}  "
              f"p>{args.econ_floor}={stats['p_above_floor']}  "
              f"CI95=[{stats['lower95']},{stats['upper95']}]", file=sys.stderr)

    dl = str(args.decision_lag)
    verdict = kill_verdict(results[dl]["coverage"], results[dl]["n_markets"],
                           results[dl], args.min_cov, args.min_markets,
                           args.econ_floor)
    decay = paired_decay(reports[0].replayed,
                         reports[args.decision_lag].replayed)

    print("\n" + "=" * 78)
    print(f"  CRYPTO KILL-TEST — pre-registered verdict at lag={args.decision_lag}s: "
          f"{verdict}")
    for lag in lags:
        r = results[str(lag)]
        print(f"    lag={lag:>3}s  cov={r['coverage']}  mkts={r['n_markets']}  "
              f"mean={r['mean']}  CI95=[{r['lower95']}, {r['upper95']}]")
    if decay:
        print(f"    paired decay lag0->{args.decision_lag}s on {decay['n_paired']} "
              f"common signals: {decay['mean_edge_lag0']:.4f} -> "
              f"{decay['mean_edge_lag']:.4f} (drop {decay['mean_decay']:.4f})")
    print("  READ: KILLED = upper95 below the +0.02 econ floor with adequate")
    print("  coverage — crypto formally dropped from roster/strategy scope.")
    print("  SURVIVES = the latency hypothesis is wrong, crypto stays in scope.")
    print("  INCONCLUSIVE = underpowered; widen data, never loosen thresholds.")
    print("=" * 78)
    fc.write_json_atomic(args.out, fc.json_safe(
        {"verdict": verdict, "decision_lag_s": args.decision_lag,
         "econ_floor": args.econ_floor, "per_lag": results, "decay": decay,
         "params": {"days": args.days, "max_signals": args.max_signals,
                    "size_usd": args.size_usd, "fee": args.fee,
                    "staleness": args.staleness, "min_cov": args.min_cov,
                    "min_markets": args.min_markets, "seed": args.seed}}))
    print(f"full results -> {args.out}")
    return 0


# ── Self-test (no DB) ────────────────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — crypto kill-test logic (no DB)\n")
    ok = True

    rows = [{"category": "Crypto"}, {"category": "Bitcoin price"},
            {"category": "NBA"}, {"category": None}, {"category": "Ethereum"}]
    ok1 = len(keep_crypto(rows)) == 3
    print(f"  [bucket] crypto kept, sports/none dropped : {ok1}"); ok &= ok1

    good = boot_pooled([0.10] * 40, 500, 7, 0.02)
    dead = boot_pooled([-0.05] * 40, 500, 7, 0.02)
    ok2 = good["p_above_floor"] == 1.0 and dead["upper95"] < 0.02
    print(f"  [bootstrap] all-positive passes, all-negative kills : {ok2}"); ok &= ok2

    ok3 = (kill_verdict(0.5, 40, dead, 0.4, 30, 0.02) == "KILLED"
           and kill_verdict(0.5, 40, good, 0.4, 30, 0.02) == "SURVIVES"
           and kill_verdict(0.2, 40, dead, 0.4, 30, 0.02) == "INCONCLUSIVE"
           and kill_verdict(0.5, 10, dead, 0.4, 30, 0.02) == "INCONCLUSIVE"
           and kill_verdict(None, 0, {"upper95": None}, 0.4, 30, 0.02)
           == "INCONCLUSIVE")
    print(f"  [verdict] killed/survives/underpowered pre-registered rule : {ok3}")
    ok &= ok3

    from dataclasses import dataclass

    @dataclass
    class R:
        market_id: str
        trader: str
        edge_net: float

    rep0 = [R("m1", "a", 0.10), R("m2", "a", 0.08), R("m3", "b", 0.05)]
    repl = [R("m1", "a", 0.01), R("m2", "a", -0.02)]
    d = paired_decay(rep0, repl)
    ok4 = (d["n_paired"] == 2 and abs(d["mean_decay"] - 0.095) < 1e-9)
    print(f"  [decay] paired on common signals only : {ok4}"); ok &= ok4

    mk = per_market_edges([R("m1", "a", 0.1), R("m1", "b", 0.3),
                           R("m2", "a", -0.1)])
    ok5 = abs(mk["m1"] - 0.2) < 1e-9 and mk["m2"] == -0.1
    print(f"  [cluster] per-market mean aggregation : {ok5}"); ok &= ok5

    # integration: replay + coarse fill on synthetic rows, no DB
    from bots.mirror_backtest.replay import Decision, replay_signals
    ob = {"mid_price": 0.50, "best_bid": 0.49, "best_ask": 0.51,
          "bid_depth_1pct": 500.0, "ask_depth_1pct": 500.0,
          "bid_depth_5pct": 2000.0, "ask_depth_5pct": 2000.0}
    sigs = [{"market_id": "m1", "trader_address": "a", "side": "YES",
             "price": 0.50, "resolution": "YES", "ob_row": dict(ob)},
            {"market_id": "m2", "trader_address": "a", "side": "YES",
             "price": 0.50, "resolution": "NO", "ob_row": dict(ob)},
            {"market_id": "m3", "trader_address": "a", "side": "YES",
             "price": 0.50, "resolution": "YES"}]  # no book -> uncovered
    rep = replay_signals(sigs, lambda _s: Decision(size_usd=50.0))
    ok6 = rep.n_signals == 2 and rep.n_uncovered == 1 and rep.n_coarse == 2
    print(f"  [replay] coarse fills + uncovered accounting : {ok6}"); ok &= ok6

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Pre-registered crypto latency kill-test (coarse fills)")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--max-signals", type=int, default=10_000, dest="max_signals",
                    help="deterministic md5-ordered subsample cap (reported)")
    ap.add_argument("--lags", default="0,10,30",
                    help="comma-separated copy latencies in seconds")
    ap.add_argument("--decision-lag", type=int, default=10, dest="decision_lag",
                    help="the lag the verdict is evaluated at (measured REST "
                         "detection ~10s; the on-chain watcher does 2-4s)")
    ap.add_argument("--size-usd", type=float, default=50.0, dest="size_usd")
    ap.add_argument("--fee", type=float, default=0.02)
    ap.add_argument("--staleness", type=int, default=120,
                    help="max seconds after t+lag to accept the first book")
    ap.add_argument("--econ-floor", type=float, default=0.02, dest="econ_floor")
    ap.add_argument("--min-cov", type=float, default=0.40, dest="min_cov")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot")
    ap.add_argument("--pmin", type=float, default=0.02)
    ap.add_argument("--pmax", type=float, default=0.98)
    ap.add_argument("--seed", type=int, default=20260712)
    ap.add_argument("--out", default="/tmp/crypto_kill.json")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
