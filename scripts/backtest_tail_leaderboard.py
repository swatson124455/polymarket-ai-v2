#!/usr/bin/env python3
"""Tail backtest — 'if we had copied these whales at a realistic lag, would we make money?'

This is the DIRECT question, not a proxy: take the whale signals MB actually
detected, simulate copying each one at OUR entry (the market price `lag` seconds
after the whale's print, not the whale's own price), hold to resolution, net
fees, and bootstrap whether the per-market edge clears zero. Run it across a
sweep of lags and sliced per category.

WHY THIS CORPUS (survivorship-safe): signals come from mirror_rejected_signals,
i.e. traders MB's live watchlist (elite_watchlist per-category leaderboards)
flagged AT DETECTION TIME. Membership is therefore point-in-time — we are not
selecting traders we now know turned out well. (Caveat: 'point-in-time' = 'on
the watchlist when the signal was logged'; if the watchlist logic changed over
the window, the universe shifts with it. Stated, not hidden.)

WHY THE LAG SWEEP: the copy latency is the load-bearing assumption behind
'crypto is un-tailable'. It should be MEASURED (the v3 collector's
feed_lag_p95_s), not guessed. Until then this sweeps {10,30,60}s (override with
--lags) and also reports the whale-price ceiling (lag=0, their fill) so the
TAILABILITY TAX — how much edge the lag eats — is explicit per category.

WHAT IT IS NOT: no ranker, no learned admission, no rule selection. The rule is
'copy every detected signal at a fixed notional'. If copy-everyone is not +EV
after a realistic fill, no ranker built on the same corpus can be trusted to
find a +EV subset without out-of-sample proof. If copy-everyone IS +EV in a
category, that category is where a ranker is worth building.

EDGE per signal:  e = outcome - p_lag - FEE_ROUNDTRIP
  outcome = 1.0 if the bought token won at resolution else 0.0
  p_lag   = market price of that token at (whale_print_time + lag), staleness-
            bounded via price_lookup.price_at (the same read-only, side-safe,
            staleness-checked accessor the scoring engine uses). No sample in
            the staleness window => the signal is UNCOVERED (counted, never
            silently dropped), because in reality we could not have filled.

STATISTIC: per-market mean edge (cluster = market_id, so correlated legs of one
event do not fake significance), then a market-level percentile bootstrap for
P(mean edge > 0). A category 'passes' at P >= --p-min AND >= --min-markets
distinct markets AND coverage rho >= --min-rho (low coverage = untrustworthy).

SAFETY: READ-ONLY (SELECTs only), SET LOCAL statement_timeout on the corpus
scan, --max-signals aborts loudly rather than truncating. Per-signal price
lookups are indexed point queries; narrow with --since/--until if slow. Pure
stdlib except price_lookup (which the mirror_scoring package already ships).

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
      venv/bin/python scripts/backtest_tail_leaderboard.py --by-category \
      | tee /tmp/tail_backtest.log
    ... --lags 10 --since 2026-04-01           # single lag, windowed
    ... --self-test                            # offline math check, no DB

Provenance (CLAUDE.md Forbidden Patterns 8/9): cite THIS script's numbers with
their coverage + lag + sample size. A category that 'passes' on 5 covered
markets has not passed anything — read the n_markets and rho columns.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
from datetime import datetime, timedelta
from typing import Optional


# ── Category bucketing (shared shape with check_trader_persistence.py) ────────
def bucket_category(cat: Optional[str]) -> str:
    c = (cat or "").lower().strip()
    if not c:
        return "unknown"
    if "esport" in c or any(k in c for k in
            ("cs2", "csgo", "counter-strike", "league of legends", "dota",
             "valorant", "overwatch", "rocket league")):
        return "esports"
    if "crypto" in c or any(k in c for k in
            ("bitcoin", "btc", "ethereum", "ether", "solana", "dogecoin",
             "xrp", "cardano", "altcoin")):
        return "crypto"
    if "sport" in c or any(k in c for k in
            ("nba", "nfl", "mlb", "nhl", "soccer", "football", "tennis", "ufc",
             "basketball", "baseball", "hockey", "boxing", "formula 1", "f1",
             "golf", "cricket", "olympic")):
        return "sports"
    if any(k in c for k in ("politic", "election", "president", "senate",
                            "congress", "geopolit")):
        return "politics"
    return "other"


# ── First mirrorable signal per (trader, market): the one MB would take ───────
_SIGNALS_SQL = """
SELECT DISTINCT ON (r.trader_address, r.market_id)
       r.trader_address AS trader,
       r.market_id      AS market_id,
       r.token_id       AS token_id,
       UPPER(r.side)    AS side,
       r.price          AS whale_price,
       r.event_time     AS event_time,
       r.resolution     AS resolution,
       m.yes_token_id, m.no_token_id,
       COALESCE(m.category, '') AS cat
FROM mirror_rejected_signals r
LEFT JOIN markets m ON m.condition_id = r.market_id
WHERE r.resolution IN ('YES', 'NO')
  AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
  AND r.token_id IS NOT NULL AND r.token_id <> ''
  {since} {until}
ORDER BY r.trader_address, r.market_id, r.event_time ASC
"""


def _outcome(sig: dict) -> Optional[float]:
    """1.0 if the bought token won, 0.0 if it lost, None if unmappable."""
    res, side = sig.get("resolution"), str(sig.get("side") or "").upper()
    tok = sig.get("token_id")
    if res not in ("YES", "NO"):
        return None
    if tok and sig.get("yes_token_id") and tok == sig["yes_token_id"]:
        return 1.0 if res == "YES" else 0.0
    if tok and sig.get("no_token_id") and tok == sig["no_token_id"]:
        return 1.0 if res == "NO" else 0.0
    if side in ("YES", "NO"):
        return 1.0 if res == side else 0.0
    return None


# ── Market-cluster bootstrap (pure stdlib) ───────────────────────────────────
def per_market_edges(rows: list[tuple[str, float]]) -> list[float]:
    """rows = [(market_id, edge)]. Returns per-market mean edge."""
    buckets: dict[str, list[float]] = {}
    for mid, e in rows:
        buckets.setdefault(mid, []).append(e)
    return [sum(v) / len(v) for v in buckets.values()]


def boot_p_edge(market_edges: list[float], n_boot: int, seed: int
                ) -> tuple[float, float]:
    """(P(mean edge > 0), mean edge) via market-level percentile bootstrap.
    n<2 or zero variance => P=0.0 (no evidence, no pass)."""
    n = len(market_edges)
    if n == 0:
        return 0.0, float("nan")
    mean = sum(market_edges) / n
    if n < 2 or all(e == market_edges[0] for e in market_edges):
        return 0.0, mean
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_boot):
        s = sum(market_edges[rng.randrange(n)] for _ in range(n))
        if s > 0:
            hits += 1
    return hits / n_boot, mean


# ── DB run ───────────────────────────────────────────────────────────────────
async def run(args) -> int:
    from dotenv import load_dotenv
    from sqlalchemy import text
    from base_engine.data.database import Database
    from bots.mirror_scoring.price_lookup import price_at
    load_dotenv()

    lags = [int(x) for x in args.lags.split(",")]
    lags_with_ceiling = ([0] + lags) if 0 not in lags else lags

    db = Database()
    await db.init()
    try:
        sql = _SIGNALS_SQL.format(
            since="AND r.event_time >= :since" if args.since else "",
            until="AND r.event_time < :until" if args.until else "",
        )
        params = {"pmin": args.pmin, "pmax": args.pmax}
        if args.since:
            params["since"] = datetime.fromisoformat(args.since)
        if args.until:
            params["until"] = datetime.fromisoformat(args.until)
        async with db.get_session() as s:
            await s.execute(text(f"SET LOCAL statement_timeout = '{args.timeout}s'"))
            rows = (await s.execute(text(sql), params)).fetchall()
        signals = []
        for r in rows:
            d = dict(r._mapping)
            o = _outcome(d)
            if o is None or d.get("event_time") is None:
                continue
            d["outcome"] = o
            signals.append(d)
        if len(signals) > args.max_signals:
            raise SystemExit(
                f"ABORT: {len(signals):,} signals exceed --max-signals "
                f"{args.max_signals:,}. Narrow with --since/--until or raise the "
                f"cap deliberately (no silent truncation)."
            )

        # Per (lag, category): collect (market_id, edge) for covered signals.
        # edge = outcome - p_lag - fee ; p_lag from price_at at event_time+lag.
        # lag=0 uses the whale's own print price (the un-tailable ceiling).
        data: dict[tuple[int, str], list[tuple[str, float]]] = {}
        covered: dict[int, int] = {lag: 0 for lag in lags_with_ceiling}
        total = len(signals)
        for sig in signals:
            cats = ["ALL"]
            if args.by_category:
                cats.append("cat:" + bucket_category(sig["cat"]))
            o = sig["outcome"]
            for lag in lags_with_ceiling:
                if lag == 0:
                    p = float(sig["whale_price"])
                else:
                    got = await price_at(db, sig["token_id"],
                                         sig["event_time"] + timedelta(seconds=lag),
                                         args.staleness)
                    if got is None:
                        continue  # uncovered: could not have filled
                    p = got[0]
                covered[lag] += 1
                edge = o - p - args.fee
                for c in cats:
                    data.setdefault((lag, c), []).append((sig["market_id"], edge))
    finally:
        await db.close()

    _report(args, lags_with_ceiling, data, covered, total)
    return 0


def _report(args, lags, data, covered, total) -> None:
    cats = sorted({c for (_, c) in data}, key=lambda k: (k != "ALL", k))
    print("\n" + "=" * 92)
    print("  TAIL BACKTEST — copy every detected whale signal at a realistic lag, hold to resolution")
    print(f"  corpus=mirror_rejected_signals (point-in-time watchlist)  first-per(trader,market)")
    print(f"  signals={total:,}  fee_roundtrip={args.fee}  staleness={args.staleness}s  "
          f"n_boot={args.n_boot}")
    print(f"  lags(s) = {lags}   (lag 0 = whale's own price = un-tailable ceiling)")
    print("=" * 92)
    print(f"  {'slice':<16}{'lag':>5}{'n_mkts':>8}{'coverage':>10}"
          f"{'mean_edge':>11}{'P(edge>0)':>11}  verdict")
    print("  " + "-" * 88)
    for c in cats:
        for lag in lags:
            rows = data.get((lag, c), [])
            me = per_market_edges(rows)
            n_mkts = len(me)
            p, mean = boot_p_edge(me, args.n_boot, args.seed + lag)
            cov = covered[lag] / total if total else 0.0  # coverage is corpus-wide
            passed = (lag != 0 and p >= args.p_min and n_mkts >= args.min_markets
                      and cov >= args.min_rho)
            verdict = ("PASS" if passed else
                       "ceiling" if lag == 0 else
                       "low-cov" if cov < args.min_rho else
                       "thin" if n_mkts < args.min_markets else "fail")
            mean_s = "  nan" if math.isnan(mean) else f"{mean:+.4f}"
            print(f"  {c:<16}{lag:>5}{n_mkts:>8}{cov:>9.1%}"
                  f"{mean_s:>11}{p:>11.3f}  {verdict}")
        print("  " + "-" * 88)
    print(f"  PASS = P(edge>0) >= {args.p_min} AND n_mkts >= {args.min_markets} "
          f"AND coverage >= {args.min_rho:.0%}, at a non-zero lag.")
    print("  Read the lag=0 ceiling row per slice: (ceiling edge - lagged edge) = the")
    print("  tailability tax. If the ceiling itself is <=0, the whales have no")
    print("  hold-to-resolution edge to tail in that slice regardless of latency.")
    print("=" * 92 + "\n")


# ── Offline self-test (no DB): edge, clustering, bootstrap calibration ────────
def _self_test() -> int:
    print("SELF-TEST — tail-backtest math (no DB)\n")
    ok = True

    # outcome mapping
    s_yes = {"resolution": "YES", "token_id": "T1", "yes_token_id": "T1",
             "no_token_id": "T2", "side": "YES"}
    s_lose = {"resolution": "NO", "token_id": "T1", "yes_token_id": "T1",
              "no_token_id": "T2", "side": "YES"}
    ok_out = _outcome(s_yes) == 1.0 and _outcome(s_lose) == 0.0
    print(f"  [outcome] won->1.0, lost->0.0 : {ok_out}")
    ok &= ok_out

    # clustering: 3 legs of market M average to one point
    me = per_market_edges([("M", 0.1), ("M", 0.3), ("N", -0.2)])
    ok_clus = len(me) == 2 and abs(sorted(me)[1] - 0.2) < 1e-9
    print(f"  [cluster] 2 markets, M leg-mean 0.2 : {ok_clus}")
    ok &= ok_clus

    # bootstrap: strong +edge -> P high; centered-at-0 -> P near 0.5; n<2 -> 0
    rng = random.Random(0)
    pos = [rng.gauss(0.05, 0.02) for _ in range(40)]
    p_pos, _ = boot_p_edge(pos, 2000, 1)
    null = [rng.gauss(0.0, 0.05) for _ in range(40)]
    p_null, _ = boot_p_edge(null, 2000, 2)
    p_deg, _ = boot_p_edge([0.1], 2000, 3)
    ok_boot = p_pos > 0.95 and 0.3 < p_null < 0.7 and p_deg == 0.0
    print(f"  [bootstrap] +edge P={p_pos:.3f}(>.95)  null P={p_null:.3f}(~.5)  "
          f"n<2 P={p_deg:.1f}(0) : {ok_boot}")
    ok &= ok_boot

    # tailability tax logic: same outcome, worse fill price -> lower edge
    ceiling = 1.0 - 0.60 - 0.02          # o=1, whale price 0.60
    lagged = 1.0 - 0.66 - 0.02           # price ran to 0.66 in the lag
    ok_tax = ceiling > lagged and abs((ceiling - lagged) - 0.06) < 1e-9
    print(f"  [tax] ceiling {ceiling:+.3f} > lagged {lagged:+.3f}, tax 0.060 : {ok_tax}")
    ok &= ok_tax

    ok_cat = (bucket_category("Crypto") == "crypto"
              and bucket_category("NBA") == "sports"
              and bucket_category("") == "unknown")
    print(f"  [category] bucketing : {ok_cat}")
    ok &= ok_cat

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only tail backtest (copy-everyone, realistic lag)")
    ap.add_argument("--lags", default="10,30,60",
                    help="comma-separated copy latencies in seconds (default 10,30,60)")
    ap.add_argument("--by-category", action="store_true",
                    help="also slice per category (crypto/sports/esports/...)")
    ap.add_argument("--fee", type=float, default=0.02, help="round-trip fee per $ (default 0.02)")
    ap.add_argument("--staleness", type=int, default=30,
                    help="max age (s) of the price sample at fill time (default 30)")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min",
                    help="min bootstrap P(edge>0) to PASS (default 0.95)")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets",
                    help="min distinct markets to PASS (default 30)")
    ap.add_argument("--min-rho", type=float, default=0.40, dest="min_rho",
                    help="min corpus coverage to trust a slice (default 0.40)")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot",
                    help="bootstrap replicates (default 2000)")
    ap.add_argument("--pmin", type=float, default=0.02, help="drop dust prices below (default 0.02)")
    ap.add_argument("--pmax", type=float, default=0.98, help="drop prices above (default 0.98)")
    ap.add_argument("--since", default="", help="ISO lower bound on event_time")
    ap.add_argument("--until", default="", help="ISO upper bound on event_time")
    ap.add_argument("--max-signals", type=int, default=100_000, dest="max_signals",
                    help="abort above this many signals (no silent truncation)")
    ap.add_argument("--timeout", type=int, default=300, help="corpus-scan statement_timeout s (default 300)")
    ap.add_argument("--seed", type=int, default=20260709, help="deterministic bootstrap seed")
    ap.add_argument("--self-test", action="store_true", help="offline math check, no DB")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
