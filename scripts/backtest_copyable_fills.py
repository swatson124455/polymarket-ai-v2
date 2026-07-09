#!/usr/bin/env python3
"""Fill-quality gate — for a NAMED list of copyable traders, does copying their
entries survive the spread at our lag?

Sequenced AFTER find_copyable_traders.py: that script finds traders whose edge
is a repeatable process (qualify on P1, judge on P2). This one asks the second,
narrower question about ONLY those traders — the one the tail backtest could not
answer because it priced fills at last-trade, not the ask:

    If we had copied trader X's entries `lag` seconds later, paying the ACTUAL
    best-ask (crossing the spread) for our size, and held to resolution — net of
    fees — is the edge still positive?

This is the operator-decided admission bar's fill side (MB_REBUILD_PLAN §2): a
realistic fill, not a mid-price proxy. It reuses the AUDITED coarse fill model
(bots/mirror_backtest/fill_models.coarse_fill — pessimistic: charges the worst
price of the smallest depth bucket that covers the order, so its edge is a
LOWER bound) over orderbook_snapshots (best bid/ask + 1%/5% depth buckets at
the fill moment).

INPUTS
  --from-json : find_copyable_traders.py's --out JSON (default
                /tmp/copyable_traders.json). Runs on its QUALIFIED addresses;
                --only-primary keeps just the VOL-sourced, non-truncated ones
                (the set the primary verdict was computed on).
  --traders   : explicit comma-separated addresses (overrides --from-json).
  --cache     : the per-address history cache find_copyable already wrote
                (default /tmp/copyable_cache) — no re-pull, same records.

METHOD (per trader, over the JUDGE window entries by default — the bets they
were certified on, not the ones used to pick them):
  entry = first BUY per (trader, market) [same estimand as the copyable script].
  fill  = coarse_fill(best-ask book at entry_time + --lag, "BUY", --size-usd);
          no orderbook snapshot within --staleness of the fill moment => the
          entry is UNCOVERED ("no book to fill against" — a real tradeability
          statement, reported per trader, never silently dropped).
  edge_fill = outcome - fill_price - fee*fill_price   (prob-points/contract)
  edge_ceiling = outcome - whale_price - fee*whale_price   (same covered entries)
  spread tax = mean(ceiling) - mean(fill), paired on covered entries — the cost
               the tail backtest hid by using last-trade prices.
  Market-clustered bootstrap P(edge_fill>0) per trader and pooled.

VERDICT per trader: SURVIVES iff coverage >= --min-cov AND market-clustered
P(edge_fill>0) >= --p-min on >= --min-markets markets. NO-BOOK if coverage is
too low to say (orderbook_snapshots covers ~top-200 markets by volume — a
trader who bets elsewhere is un-verifiable here, which is itself decision-grade:
we cannot confirm a fill for them). Pooled verdict across survivors is the
headline: is there a copyable, FILLABLE edge after the spread.

WHAT IT STILL DOESN'T DO: exact per-level L2 (orderbook_snapshots is aggregated
buckets, not a ladder — the precise model needs shadow_fills, which only exist
at ~12.7k MB decision moments, not these whale moments). The coarse model is
pessimistic by construction, so a SURVIVE here is trustworthy; a fail on thin
coverage is "unverifiable", not "disproven". A --lag sweep shows sensitivity.

SAFETY: READ-ONLY DB (batched markets + per-entry orderbook point queries under
statement_timeout), no network (histories come from the cache), pure reuse of
audited fill code + the copyable script's tested helpers.

INVOCATION (on the VPS, after find_copyable_traders.py has run):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc \
      venv/bin/python /tmp/mbpc/scripts/backtest_copyable_fills.py \
      --from-json /tmp/copyable_traders.json --only-primary \
      --cache /tmp/copyable_cache | tee /tmp/copyable_fills.log
    ... --self-test        # offline fill-math check, no DB
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

# Import the copyable script's tested helpers + the audited coarse fill model.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_copyable_traders as fc  # noqa: E402


def _import_coarse_fill():
    """Deferred import so --self-test runs without base_engine on the path."""
    from bots.mirror_backtest.fill_models import coarse_fill
    return coarse_fill


# ── Pure fill-edge math (offline-testable) ───────────────────────────────────
def fill_edge(outcome: float, fill_price: float, fee: float) -> float:
    """Net edge in prob-points per contract at the realistic ask (fee per $)."""
    return outcome - fill_price - fee * fill_price


def ceiling_edge(outcome: float, whale_price: float, fee: float) -> float:
    return outcome - whale_price - fee * whale_price


def spread_tax(rows: list[tuple[str, float, float]]) -> float:
    """rows=[(market, edge_fill, edge_ceiling)] covered entries → mean(ceiling)
    - mean(fill), paired on the same entries (what the ask costs vs the whale's
    own price)."""
    if not rows:
        return float("nan")
    return (sum(r[2] for r in rows) - sum(r[1] for r in rows)) / len(rows)


def trader_verdict(p: float, n_mkts: int, cov: float, p_min: float,
                   min_markets: int, min_cov: float) -> str:
    if cov < min_cov:
        return "NO-BOOK"          # can't price a fill for enough of their bets
    if n_mkts < min_markets:
        return "THIN"
    return "SURVIVES" if p >= p_min else "FILL-KILLED"


# ── Order-book snapshot at the fill moment (read-only) ───────────────────────
_OB_SQL = (
    "SELECT best_bid, best_ask, mid_price, "
    "bid_depth_1pct, ask_depth_1pct, bid_depth_5pct, ask_depth_5pct, snapshot_time "
    "FROM orderbook_snapshots "
    "WHERE token_id = :tid AND snapshot_time >= :at AND snapshot_time <= :hi "
    "ORDER BY snapshot_time ASC LIMIT 1"
)


async def book_at(db, token_id: str, at: datetime, staleness_s: int,
                  timeout_s: int) -> Optional[dict]:
    """First orderbook snapshot at-or-after the fill moment, within staleness.
    at-or-AFTER because we fill against the book showing when we act."""
    from sqlalchemy import text
    if not token_id:
        return None
    async with db.get_session() as s:
        await s.execute(text(f"SET LOCAL statement_timeout = '{timeout_s}s'"))
        row = (await s.execute(text(_OB_SQL), {
            "tid": token_id, "at": at.replace(tzinfo=None),
            "hi": (at + timedelta(seconds=staleness_s)).replace(tzinfo=None),
        })).fetchone()
    return dict(row._mapping) if row is not None else None


def load_addresses(args) -> list[str]:
    if args.traders:
        return [a.strip() for a in args.traders.split(",") if a.strip().startswith("0x")]
    with open(args.from_json) as f:
        blob = json.load(f)
    q = blob.get("qualified", {})
    out = []
    for addr, meta in q.items():
        if args.only_primary and ("VOL" not in (meta.get("src") or "")
                                  or meta.get("truncated")):
            continue
        out.append(addr)
    return out


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    coarse_fill = _import_coarse_fill()

    addrs = load_addresses(args)
    if not addrs:
        print("no addresses to test (check --from-json / --only-primary / --traders)")
        return 1
    split = fc.parse_ts(args.split_date)

    db = Database()
    await db.init()
    try:
        # Load cached histories + label via markets (same pipeline as copyable).
        histories: dict[str, list[dict]] = {}
        for a in addrs:
            cp = os.path.join(args.cache, f"{a}.json")
            if not os.path.exists(cp):
                continue
            with open(cp) as f:
                blob = json.load(f)
            trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
            for t in trades:
                t["_ts"] = fc.parse_ts(t.get("timestamp"))
            histories[a] = trades
        if not histories:
            print(f"no cached histories in {args.cache} for the requested addresses")
            return 1

        keys = sorted({t.get("marketId") or "" for h in histories.values()
                       for t in h} - {""})
        markets = await fc.load_markets(db, keys, args.timeout)

        lags = [int(x) for x in args.lags.split(",")]
        # per (lag, trader): list of (market, edge_fill, edge_ceiling); coverage counts
        results: dict[int, dict[str, dict]] = {lag: {} for lag in lags}
        for a, h in histories.items():
            entries = fc.first_buys(h, args.pmin, args.pmax)
            labeled = []
            for t in entries:
                mkt = markets.get(t.get("marketId") or "")
                o = fc.label_outcome(t.get("tokenId") or "", t.get("side") or "", mkt)
                if o is None or t["_ts"] is None:
                    continue
                if split is not None and t["_ts"] <= split:
                    continue  # judge window only (bets they were certified on)
                labeled.append((t, o, mkt))
            for lag in lags:
                rows, n_total, n_cov = [], 0, 0
                for t, o, mkt in labeled:
                    n_total += 1
                    book = await book_at(db, t.get("tokenId") or "",
                                         t["_ts"] + timedelta(seconds=lag),
                                         args.staleness, args.timeout)
                    if book is None:
                        continue
                    fr = coarse_fill(book, "BUY", args.size_usd)
                    if fr is None:
                        continue
                    n_cov += 1
                    ef = fill_edge(o, fr.price, args.fee)
                    ec = ceiling_edge(o, float(t["price"]), args.fee)
                    rows.append((t["marketId"], ef, ec,
                                 fc.bucket_category((mkt or {}).get("category"))))
                results[lag][a] = {"rows": rows, "n_total": n_total, "n_cov": n_cov}
    finally:
        await db.close()

    _report(args, addrs, histories, results, lags, split)
    return 0


def _pool(rows_all: list[tuple[str, float]], n_boot: int, seed: int) -> dict:
    me = fc.per_market_edges(rows_all)
    p, mean, upper = fc.boot_stats(me, n_boot, seed)
    return {"p": p, "edge": mean, "upper95": upper, "n_markets": len(me),
            "n_bets": len(rows_all)}


def _report(args, addrs, histories, results, lags, split) -> None:
    print("\n" + "=" * 104)
    print("  FILL-QUALITY GATE — copy named traders at the ACTUAL best-ask (spread crossed), hold to resolution")
    print(f"  traders={len(addrs)} requested / {len(histories)} with cached history"
          f"  size=${args.size_usd}  fee/$={args.fee}  lags={lags}s  staleness={args.staleness}s")
    print(f"  fill = audited coarse model (pessimistic; edge is a LOWER bound) over orderbook_snapshots")
    print(f"  judge window: entries after {split.date() if split else '(all)'}"
          f"   [SURVIVES = cov>={args.min_cov:.0%} AND P(fill edge>0)>={args.p_min} on >={args.min_markets} mkts]")
    print("=" * 104)

    for lag in lags:
        per = results[lag]
        print(f"\n  ── lag {lag}s ──")
        print(f"  {'trader':<14}{'cat':<9}{'bets':>6}{'cov':>6}{'fill edge':>11}"
              f"{'P(>0)':>8}{'tax':>8}  verdict")
        print("  " + "-" * 100)
        survivors, pooled_rows = [], []
        for a in sorted(per, key=lambda x: -_safe_edge(per[x], args.n_boot, args.seed)):
            r = per[a]
            cov = r["n_cov"] / r["n_total"] if r["n_total"] else 0.0
            me = fc.per_market_edges([(x[0], x[1]) for x in r["rows"]])
            p, mean, _ = fc.boot_stats(me, args.n_boot, args.seed)
            tax = spread_tax([(x[0], x[1], x[2]) for x in r["rows"]])
            cat = Counter(x[3] for x in r["rows"]).most_common(1)
            v = trader_verdict(p, len(me), cov, args.p_min, args.min_markets, args.min_cov)
            if v == "SURVIVES":
                survivors.append(a)
                pooled_rows.extend((x[0], x[1]) for x in r["rows"])
            print(f"  {a[:12]:<14}{(cat[0][0] if cat else '-'):<9}{r['n_total']:>6}"
                  f"{cov:>6.0%}{_f(mean):>11}{p:>8.3f}{_f(tax):>8}  {v}")
        print("  " + "-" * 100)
        pooled = _pool(pooled_rows, args.n_boot, args.seed + 9) if pooled_rows else None
        if pooled:
            print(f"  POOLED over {len(survivors)} survivors: fill edge {pooled['edge']:+.4f}"
                  f"  P(>0)={pooled['p']:.3f}  upper95={pooled['upper95']:+.4f}"
                  f"  {pooled['n_markets']} mkts / {pooled['n_bets']} bets")
        else:
            print(f"  POOLED: no survivors at lag {lag}s "
                  f"(all NO-BOOK/THIN/FILL-KILLED — see column).")
    print("-" * 104)
    print("  READ: SURVIVES at the primary lag = copyable AND fillable after spread — these are")
    print("  the addresses worth a forward paper deploy. NO-BOOK = we can't price their fills")
    print("  (they trade outside orderbook_snapshots coverage) — unverifiable, not disproven.")
    print("  FILL-KILLED = real copyable edge eaten by the spread at our lag. 'tax' = the cost")
    print("  the last-trade tail backtest hid. Coarse model is pessimistic, so SURVIVES is safe.")
    print("=" * 104 + "\n")


def _safe_edge(r: dict, n_boot: int, seed: int) -> float:
    me = fc.per_market_edges([(x[0], x[1]) for x in r["rows"]])
    return fc.boot_stats(me, n_boot, seed)[1] if me else float("-inf")


def _f(v: float) -> str:
    return f"{'nan':>11}" if (isinstance(v, float) and math.isnan(v)) else f"{v:>+11.4f}"


# ── Offline self-test (no DB): fill math + verdict + coarse-model sanity ──────
def _self_test() -> int:
    print("SELF-TEST — fill-quality math (no DB)\n")
    ok = True

    # fill vs ceiling: same outcome, worse (ask) price → lower edge; tax positive
    o = 1.0
    ef = fill_edge(o, 0.66, 0.02)         # crossed to ask 0.66
    ec = ceiling_edge(o, 0.60, 0.02)      # whale paid 0.60
    ok_edge = ef < ec and abs(ec - (1 - 0.60 - 0.012)) < 1e-9
    print(f"  [edge] fill {ef:+.3f} < ceiling {ec:+.3f} (spread cost) : {ok_edge}"); ok &= ok_edge

    tax = spread_tax([("M", ef, ec), ("N", fill_edge(0, 0.34, 0.02),
                                          ceiling_edge(0, 0.30, 0.02))])
    ok_tax = tax > 0
    print(f"  [tax] paired mean(ceiling)-mean(fill) = {tax:+.4f} (>0) : {ok_tax}"); ok &= ok_tax

    ok_v = (trader_verdict(0.99, 40, 0.6, 0.95, 30, 0.4) == "SURVIVES"
            and trader_verdict(0.99, 40, 0.1, 0.95, 30, 0.4) == "NO-BOOK"
            and trader_verdict(0.99, 5, 0.6, 0.95, 30, 0.4) == "THIN"
            and trader_verdict(0.50, 40, 0.6, 0.95, 30, 0.4) == "FILL-KILLED")
    print(f"  [verdict] SURVIVES/NO-BOOK/THIN/FILL-KILLED : {ok_v}"); ok &= ok_v

    # coarse model actually crosses the spread (BUY charged >= best_ask)
    try:
        cf = _import_coarse_fill()
        row = {"mid_price": 0.50, "best_ask": 0.52, "best_bid": 0.48,
               "ask_depth_1pct": 1000, "ask_depth_5pct": 5000,
               "bid_depth_1pct": 1000, "bid_depth_5pct": 5000}
        fr = cf(row, "BUY", 50.0)
        ok_cf = fr is not None and fr.price >= 0.52
        print(f"  [coarse] BUY charged {fr.price:.3f} >= best_ask 0.52 : {ok_cf}")
        ok &= ok_cf
    except Exception as e:
        print(f"  [coarse] SKIPPED (base_engine not importable offline: {str(e)[:50]})")

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fill-quality gate for copyable traders (real ask, spread crossed)")
    ap.add_argument("--from-json", default="/tmp/copyable_traders.json", dest="from_json",
                    help="find_copyable_traders.py --out JSON (its qualified addresses)")
    ap.add_argument("--only-primary", action="store_true", dest="only_primary",
                    help="keep only VOL-sourced, non-truncated qualifiers (the primary set)")
    ap.add_argument("--traders", default="", help="explicit comma-separated addresses (overrides --from-json)")
    ap.add_argument("--cache", default="/tmp/copyable_cache", help="history cache dir written by find_copyable")
    ap.add_argument("--lags", default="10,30", help="copy latencies in seconds (default 10,30)")
    ap.add_argument("--size-usd", type=float, default=50.0, dest="size_usd",
                    help="copy order notional for slippage (default 50)")
    ap.add_argument("--fee", type=float, default=0.02, help="round-trip fee per $ (default 0.02)")
    ap.add_argument("--staleness", type=int, default=60,
                    help="max age (s) of the book snapshot after the fill moment (default 60)")
    ap.add_argument("--split-date", default=fc.SPLIT_DEFAULT, dest="split_date",
                    help=f"only judge entries AFTER this (default {fc.SPLIT_DEFAULT}); '' = all")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--min-cov", type=float, default=0.40, dest="min_cov",
                    help="min book-coverage to rule a trader (below = NO-BOOK)")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot")
    ap.add_argument("--pmin", type=float, default=0.02)
    ap.add_argument("--pmax", type=float, default=0.98)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260709)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
