#!/usr/bin/env python3
"""Tail backtest — 'if we had copied these whales at a realistic lag, would we make money?'

Simulates copying whale signals at OUR entry (the market price `lag` seconds
after the whale's print, never the whale's own price), holding to resolution,
netting fees, and bootstrapping whether the per-market edge clears zero — per
category, across a sweep of lags.

WHAT THE CORPUS IS (stated plainly — 2026-07-09 review finding): rows come from
mirror_rejected_signals, i.e. whale signals MB detected live but did NOT copy.
Membership is point-in-time (the live watchlist at detection time — no
survivorship in trader selection), BUT it is REJECTED-only: signals MB executed
are absent, and rejection reasons are heterogeneous (caps, dedup guards,
category blocks, gate scoring). The report prints the rejection_stage/side mix
so the operator sees the corpus composition; --stage narrows it. Rows whose
market_id is keyed by numeric gamma id (not condition_id) fail the markets join
and land in cat:unknown — counted there, not dropped.

THIS IS A PRE-SPREAD SCREEN, NOT AN ADMISSION GATE (2026-07-09 review finding):
p_lag comes from last prints/ticks (price_lookup.price_at), not the ask you
would actually cross, so a green result here is OPTIMISTIC by roughly the
half-spread plus impact. Per the standing operator decision (MB_REBUILD_PLAN §2)
only the PRECISE fill model (shadow_fills ladders, bots/mirror_backtest/gate.py)
can admit a rule. Therefore: a FAIL here is final for the slice; a PASS is
printed as `PASS*` (screen) and only licenses the next test, never a deploy.

REALISTIC-LAG DISCIPLINE (2026-07-09 review finding — the original version
could return a sample at or BEFORE the whale's print, silently measuring the
ceiling): a lagged fill is only counted when the price sample's own timestamp
is STRICTLY AFTER the whale's print. No post-print sample inside the staleness
window => the signal is UNCOVERED for that lag ('no post-print price estimate
available' — a data-availability statement, not proof we couldn't have filled).
Coverage is reported PER SLICE and gates each slice's own verdict.

UNITS (2026-07-09 review finding): the rule is equal CONTRACTS, and edge is in
probability points per contract: e_pts = o - p - fee*p (fee is per dollar, so
per contract it scales by price). A descriptive per-dollar return column
ret/$ = (o-p)/p - fee is printed alongside (fixed-notional view; longshot-
dominated, so the gate statistic stays e_pts). The paired 'tax' column is the
whale-price ceiling MINUS the lagged edge computed on the SAME covered signals.

MULTIPLICITY (2026-07-09 review finding): ~7 slices x 3 lags tested at
P>=0.95 gives >25% family-wise false-pass odds under a global null. So the
run pre-registers ONE primary cell (--primary-slice, default cat:sports — the
plan's knowledge-edge hypothesis — at --primary-lag, default 10s = operator-
measured copy latency) and requires lag-agreement (PASS* at the primary lag
AND at 30s) before recommending anything. Every other cell is DESCRIPTIVE.

CLUSTERING CAVEAT: bootstrap clusters are condition_ids; the markets table has
no event id, so neg-risk siblings (same election/tournament) count as
independent clusters and P(edge>0) is anti-conservative in proportion. The
nr% column (share of clustered markets flagged neg_risk) sizes that risk.

SAFETY: READ-ONLY. Corpus scan runs under SET LOCAL statement_timeout and a
server-side LIMIT sentinel (--max-signals+1) so an oversized corpus aborts
BEFORE transferring/materializing millions of rows. The per-signal lookup loop
prints progress to stderr and honors --sample (seeded) to bound wall time.
The resolution IN ('YES','NO') predicate is NOT index-backed on the ~17.5M-row
table (migration 073 indexes exclude it) — run in a quiet window, and use
--since for a bounded first run.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
      venv/bin/python scripts/backtest_tail_leaderboard.py --by-category \
      --since 2026-06-01 --sample 20000 | tee /tmp/tail_backtest.log   # bounded first run
    ...   --by-category | tee /tmp/tail_backtest.log                  # full run, quiet window
    ...   --self-test                                                 # offline math check, no DB

Provenance (CLAUDE.md Forbidden Patterns 8/9): cite THIS script's numbers with
their coverage + lag + sample size + PASS* qualifier. A slice that 'passes' on
5 covered markets or 10% slice coverage has not passed anything.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from collections import Counter
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


# ── Pure per-signal math (unit-tested in tests/unit/test_tail_backtest_script.py) ──
def edge_points(o: float, p: float, fee: float) -> float:
    """Equal-contracts edge in probability points; per-$ fee scaled by price."""
    return o - p - fee * p


def ret_per_dollar(o: float, p: float, fee: float) -> float:
    """Fixed-notional per-dollar return (descriptive; longshot-dominated)."""
    return (o - p) / p - fee


def fill_is_lagged(sample_ts: datetime, event_time: datetime) -> bool:
    """A lagged fill must be priced from a sample STRICTLY AFTER the whale's
    print — a sample at/before it is the ceiling, not a lagged fill."""
    return sample_ts > event_time


def verdict_for(lag: int, p: float, n_mkts: int, cov: float,
                p_min: float, min_markets: int, min_rho: float) -> str:
    """Per-slice verdict. PASS* is a pre-spread SCREEN pass, never an admission."""
    if lag == 0:
        return "ceiling"
    if cov < min_rho:
        return "low-cov"
    if n_mkts < min_markets:
        return "thin"
    return "PASS*" if p >= p_min else "fail"


# ── First mirrorable signal per (trader, market): the one MB would take ───────
# LIMIT sentinel (:cap1 = --max-signals + 1): oversize aborts server-side
# instead of after a full transfer + RAM materialization (2026-07-09 finding).
_SIGNALS_SQL = """
SELECT * FROM (
  SELECT DISTINCT ON (r.trader_address, r.market_id)
         r.trader_address AS trader,
         r.market_id      AS market_id,
         r.token_id       AS token_id,
         UPPER(r.side)    AS side,
         r.rejection_stage AS stage,
         r.price          AS whale_price,
         r.event_time     AS event_time,
         r.resolution     AS resolution,
         m.yes_token_id, m.no_token_id,
         COALESCE(m.neg_risk, FALSE) AS neg_risk,
         COALESCE(m.category, '') AS cat
  FROM mirror_rejected_signals r
  LEFT JOIN markets m ON m.condition_id = r.market_id
  WHERE r.resolution IN ('YES', 'NO')
    AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
    AND r.token_id IS NOT NULL AND r.token_id <> ''
    {stage} {since} {until}
  ORDER BY r.trader_address, r.market_id, r.event_time ASC
) q
LIMIT :cap1
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


def paired_tax(rows: list[tuple[str, float, float]]) -> float:
    """rows = [(market_id, edge_pts, ceil_pts)] for ONE (lag, slice), covered
    signals only. Tax = mean ceiling MINUS mean lagged edge on the SAME
    signals (2026-07-09 finding: an unpaired ceiling mixes signal sets)."""
    if not rows:
        return float("nan")
    return (sum(r[2] for r in rows) - sum(r[1] for r in rows)) / len(rows)


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
            stage="AND r.rejection_stage = :stage" if args.stage != "all" else "",
            since="AND r.event_time >= :since" if args.since else "",
            until="AND r.event_time < :until" if args.until else "",
        )
        params = {"pmin": args.pmin, "pmax": args.pmax,
                  "cap1": args.max_signals + 1}
        if args.stage != "all":
            params["stage"] = args.stage
        if args.since:
            params["since"] = datetime.fromisoformat(args.since)
        if args.until:
            params["until"] = datetime.fromisoformat(args.until)
        async with db.get_session() as s:
            await s.execute(text(f"SET LOCAL statement_timeout = '{args.timeout}s'"))
            rows = (await s.execute(text(sql), params)).fetchall()
        if len(rows) > args.max_signals:
            raise SystemExit(
                f"ABORT: corpus exceeds --max-signals {args.max_signals:,} "
                f"(LIMIT sentinel hit). Narrow with --since/--until/--stage or "
                f"raise the cap deliberately (no silent truncation)."
            )
        signals = []
        for r in rows:
            d = dict(r._mapping)
            o = _outcome(d)
            if o is None or d.get("event_time") is None:
                continue
            d["outcome"] = o
            signals.append(d)

        sampled_from = len(signals)
        if args.sample and len(signals) > args.sample:
            signals = random.Random(args.seed).sample(signals, args.sample)

        stage_mix = Counter(s.get("stage") for s in signals)
        side_mix = Counter(s.get("side") for s in signals)

        # Per-slice accounting (2026-07-09 finding: coverage MUST be per slice,
        # not corpus-wide — the PASS gate uses the slice's own rho).
        totals: dict[str, int] = Counter()
        covered: dict[tuple[int, str], int] = Counter()
        # rows_by[(lag, slice)] = [(market_id, edge_pts, ceil_pts, ret_$)]
        rows_by: dict[tuple[int, str], list] = {}
        negrisk_mkts: dict[str, set] = {}
        all_mkts: dict[str, set] = {}

        total = len(signals)
        for i, sig in enumerate(signals):
            if i and i % 2000 == 0:
                print(f"  ...progress {i:,}/{total:,} signals", file=sys.stderr, flush=True)
            slices = ["ALL"]
            if args.by_category:
                slices.append("cat:" + bucket_category(sig["cat"]))
            o = sig["outcome"]
            wp = float(sig["whale_price"])
            for c in slices:
                totals[c] += 1
                all_mkts.setdefault(c, set()).add(sig["market_id"])
                if sig.get("neg_risk"):
                    negrisk_mkts.setdefault(c, set()).add(sig["market_id"])
            ceil_pts = edge_points(o, wp, args.fee)
            for lag in lags_with_ceiling:
                if lag == 0:
                    p = wp
                else:
                    got = await price_at(db, sig["token_id"],
                                         sig["event_time"] + timedelta(seconds=lag),
                                         args.staleness)
                    # Strictly-after-the-print rule: a sample at/before the
                    # whale's print is the ceiling, not a lagged fill.
                    if got is None or not fill_is_lagged(got[1], sig["event_time"]):
                        continue
                    p = got[0]
                covered[(lag, "ALL")] += 1
                if args.by_category:
                    covered[(lag, slices[1])] += 1
                e_pts = edge_points(o, p, args.fee)
                r_dol = ret_per_dollar(o, p, args.fee)
                for c in slices:
                    rows_by.setdefault((lag, c), []).append(
                        (sig["market_id"], e_pts, ceil_pts, r_dol))
    finally:
        await db.close()

    _report(args, lags_with_ceiling, rows_by, covered, totals,
            negrisk_mkts, all_mkts, stage_mix, side_mix, sampled_from)
    return 0


def _report(args, lags, rows_by, covered, totals, negrisk_mkts, all_mkts,
            stage_mix, side_mix, sampled_from) -> None:
    slices = sorted({c for (_, c) in rows_by}, key=lambda k: (k != "ALL", k))
    n_all = totals.get("ALL", 0)
    print("\n" + "=" * 100)
    print("  TAIL BACKTEST — copy rejected-corpus whale signals at a realistic lag, hold to resolution")
    print("  *** PRE-SPREAD SCREEN: fills are last prints, not the ask. FAIL is final; PASS* only")
    print("      licenses the PRECISE-model gate (bots/mirror_backtest/gate.py). Never a deploy. ***")
    print(f"  corpus=mirror_rejected_signals (REJECTED-only; executed copies absent)  "
          f"stage={args.stage}")
    print(f"  signals={n_all:,}" + (f" (sampled from {sampled_from:,}, seed {args.seed})"
                                    if sampled_from != n_all else "")
          + f"  fee/$={args.fee}  staleness={args.staleness}s  n_boot={args.n_boot}")
    print(f"  stage mix: {dict(stage_mix)}   side mix: {dict(side_mix)}")
    print(f"  lags(s) = {lags}   (lag 0 = whale's own price = un-tailable ceiling)")
    print(f"  units: edge_pts = o - p - fee*p per CONTRACT; ret/$ = (o-p)/p - fee (descriptive)")
    print("=" * 100)
    print(f"  {'slice':<14}{'lag':>4}{'n_mkts':>7}{'cov':>7}{'nr%':>6}"
          f"{'edge_pts':>10}{'P(>0)':>8}{'ret/$':>9}{'tax':>8}  verdict")
    print("  " + "-" * 96)
    results: dict[tuple[str, int], dict] = {}
    for c in slices:
        tot_c = totals.get(c, 0)
        nr_pct = (100.0 * len(negrisk_mkts.get(c, set())) / len(all_mkts[c])
                  if all_mkts.get(c) else 0.0)
        for lag in lags:
            rws = rows_by.get((lag, c), [])
            me = per_market_edges([(r[0], r[1]) for r in rws])
            n_mkts = len(me)
            p, mean = boot_p_edge(me, args.n_boot, args.seed + lag)
            cov = covered.get((lag, c), 0) / tot_c if tot_c else 0.0
            tax = paired_tax([(r[0], r[1], r[2]) for r in rws]) if lag else float("nan")
            rd = (sum(r[3] for r in rws) / len(rws)) if rws else float("nan")
            v = verdict_for(lag, p, n_mkts, cov, args.p_min, args.min_markets,
                            args.min_rho)
            results[(c, lag)] = {"p": p, "n": n_mkts, "cov": cov, "verdict": v}
            print(f"  {c:<14}{lag:>4}{n_mkts:>7}{cov:>6.0%}{nr_pct:>6.1f}"
                  f"{_f(mean, 10, 4)}{p:>8.3f}{_f(rd, 9, 3)}{_f(tax, 8, 3)}  {v}")
        print("  " + "-" * 96)
    print(f"  PASS* = screen-level only: P>={args.p_min} AND n_mkts>={args.min_markets} "
          f"AND slice coverage>={args.min_rho:.0%} at a non-zero lag.")
    print("  nr% = share of the slice's markets flagged neg_risk: clusters are condition_ids")
    print("  (no event id in schema), so sibling markets inflate P(>0) roughly with nr%.")
    print("  'cov' counts signals with a post-print price sample — a data-availability rate.")

    # ── Pre-registered primary cell (multiplicity control) ──────────────────
    print("-" * 100)
    prim, plag, clag = args.primary_slice, args.primary_lag, args.confirm_lag
    print(f"  PRIMARY CELL (pre-registered): {prim} @ lag {plag}s, confirm @ {clag}s."
          f"  All other cells are DESCRIPTIVE.")
    a = results.get((prim, plag))
    b = results.get((prim, clag))
    if a is None:
        print(f"  primary slice absent from this run"
              f"{' (run with --by-category)' if prim.startswith('cat:') else ''} — no recommendation.")
    elif a["verdict"] == "PASS*" and b and b["verdict"] == "PASS*":
        print(f"  RESULT: PASS* at both lags — the screen supports the {prim} hypothesis.")
        print(f"  NEXT (not a deploy): run this slice through the PRECISE fill-model gate")
        print(f"  (shadow_fills ladders) per MB_REBUILD_PLAN §2 before any 'go'.")
    elif a["verdict"] in ("low-cov", "thin"):
        print(f"  RESULT: UNDERPOWERED at the primary cell ({a['verdict']}, n={a['n']},"
              f" cov={a['cov']:.0%}) — widen the window; not a negative.")
    else:
        print(f"  RESULT: primary cell does not pass the screen "
              f"(@{plag}s: {a['verdict']}, P={a['p']:.3f}"
              + (f"; @{clag}s: {b['verdict']}, P={b['p']:.3f}" if b else "") + ").")
        print(f"  A screen FAIL is final for this slice — pre-spread fills flatter the whales,")
        print(f"  so the true tailed edge is worse than what failed here.")
    print("=" * 100 + "\n")


def _f(v: float, w: int, prec: int) -> str:
    return f"{'nan':>{w}}" if (isinstance(v, float) and math.isnan(v)) else f"{v:>+{w}.{prec}f}"


# ── Offline self-test (no DB): edge math, clustering, bootstrap, lag rule ─────
def _self_test() -> int:
    print("SELF-TEST — tail-backtest math (no DB)\n")
    ok = True

    s_yes = {"resolution": "YES", "token_id": "T1", "yes_token_id": "T1",
             "no_token_id": "T2", "side": "YES"}
    s_lose = {"resolution": "NO", "token_id": "T1", "yes_token_id": "T1",
              "no_token_id": "T2", "side": "YES"}
    ok_out = _outcome(s_yes) == 1.0 and _outcome(s_lose) == 0.0
    print(f"  [outcome] won->1.0, lost->0.0 : {ok_out}")
    ok &= ok_out

    # units: fee scales with price in pts; per-$ is (o-p)/p - fee
    e = edge_points(1.0, 0.60, 0.02)          # 1 - .60 - .012 = .388
    r = ret_per_dollar(1.0, 0.60, 0.02)       # .4/.6 - .02 = .6467
    ok_units = abs(e - 0.388) < 1e-9 and abs(r - (0.4 / 0.6 - 0.02)) < 1e-9
    print(f"  [units] edge_pts {e:+.3f} (fee*p) ; ret/$ {r:+.4f} : {ok_units}")
    ok &= ok_units

    # strictly-after-print rule
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    ok_lag = (not fill_is_lagged(t0, t0)
              and not fill_is_lagged(t0 - timedelta(seconds=5), t0)
              and fill_is_lagged(t0 + timedelta(seconds=1), t0))
    print(f"  [lag rule] sample at/before print rejected, after accepted : {ok_lag}")
    ok &= ok_lag

    me = per_market_edges([("M", 0.1), ("M", 0.3), ("N", -0.2)])
    ok_clus = len(me) == 2 and abs(sorted(me)[1] - 0.2) < 1e-9
    print(f"  [cluster] 2 markets, M leg-mean 0.2 : {ok_clus}")
    ok &= ok_clus

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

    # paired tax on the SAME covered rows
    tax = paired_tax([("M", 0.32, 0.38), ("N", 0.10, 0.20)])
    ok_tax = abs(tax - 0.08) < 1e-9
    print(f"  [tax] paired mean(ceil)-mean(lag) = {tax:+.3f} (expect +0.080) : {ok_tax}")
    ok &= ok_tax

    # per-slice verdict gating
    ok_v = (verdict_for(10, 0.99, 50, 0.60, 0.95, 30, 0.40) == "PASS*"
            and verdict_for(10, 0.99, 50, 0.10, 0.95, 30, 0.40) == "low-cov"
            and verdict_for(10, 0.99, 5, 0.60, 0.95, 30, 0.40) == "thin"
            and verdict_for(0, 0.99, 50, 1.0, 0.95, 30, 0.40) == "ceiling"
            and verdict_for(10, 0.50, 50, 0.60, 0.95, 30, 0.40) == "fail")
    print(f"  [verdict] PASS*/low-cov/thin/ceiling/fail gating : {ok_v}")
    ok &= ok_v

    ok_cat = (bucket_category("Crypto") == "crypto"
              and bucket_category("NBA") == "sports"
              and bucket_category("") == "unknown")
    print(f"  [category] bucketing : {ok_cat}")
    ok &= ok_cat

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only tail backtest (copy-everyone, realistic lag; pre-spread screen)")
    ap.add_argument("--lags", default="10,30,60",
                    help="comma-separated copy latencies in seconds (default 10,30,60)")
    ap.add_argument("--by-category", action="store_true",
                    help="also slice per category (crypto/sports/esports/...)")
    ap.add_argument("--stage", choices=["all", "pre_gate", "gate"], default="all",
                    help="restrict corpus to a rejection_stage (default all; mix is printed)")
    ap.add_argument("--primary-slice", default="cat:sports", dest="primary_slice",
                    help="pre-registered primary cell slice (default cat:sports)")
    ap.add_argument("--primary-lag", type=int, default=10, dest="primary_lag",
                    help="pre-registered primary lag seconds (default 10 = measured latency)")
    ap.add_argument("--confirm-lag", type=int, default=30, dest="confirm_lag",
                    help="lag-agreement confirmation lag (default 30)")
    ap.add_argument("--fee", type=float, default=0.02, help="round-trip fee per $ (default 0.02)")
    ap.add_argument("--staleness", type=int, default=30,
                    help="max age (s) of the price sample at fill time (default 30)")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min",
                    help="min bootstrap P(edge>0) for PASS* (default 0.95)")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets",
                    help="min distinct markets for PASS* (default 30)")
    ap.add_argument("--min-rho", type=float, default=0.40, dest="min_rho",
                    help="min PER-SLICE coverage to trust a slice (default 0.40)")
    ap.add_argument("--n-boot", type=int, default=2000, dest="n_boot",
                    help="bootstrap replicates (default 2000)")
    ap.add_argument("--sample", type=int, default=0,
                    help="seeded random subsample of signals to bound the lookup loop (0=off)")
    ap.add_argument("--pmin", type=float, default=0.02, help="drop dust prices below (default 0.02)")
    ap.add_argument("--pmax", type=float, default=0.98, help="drop prices above (default 0.98)")
    ap.add_argument("--since", default="", help="ISO lower bound on event_time (use on first run)")
    ap.add_argument("--until", default="", help="ISO upper bound on event_time")
    ap.add_argument("--max-signals", type=int, default=100_000, dest="max_signals",
                    help="server-side LIMIT sentinel; abort above this (no silent truncation)")
    ap.add_argument("--timeout", type=int, default=300, help="corpus-scan statement_timeout s (default 300)")
    ap.add_argument("--seed", type=int, default=20260709, help="deterministic bootstrap/sample seed")
    ap.add_argument("--self-test", action="store_true", help="offline math check, no DB")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
