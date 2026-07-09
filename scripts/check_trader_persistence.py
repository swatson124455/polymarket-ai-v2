#!/usr/bin/env python3
"""Trader-skill persistence check — the one honest go/no-go before any MB algo rework.

The v3 whale trader-ranking engine (bots/mirror_scoring/) FAILED its Stage-1
acceptance gate under a now-calibrated permutation test. Before spending any
effort reworking the ranking, we answer the single prerequisite question that
no amount of modeling can create if the answer is no:

    Do traders with positive edge in period 1 have positive edge in period 2?

This is trader-skill PERSISTENCE. If raw cross-period edge autocorrelation is
≈ 0, there is no stable trader-level signal to rank, and no ranking method can
recover one — the FAIL stands. If it is clearly > 0, the signal is real and the
method lost it (leading hypothesis: pooling un-tailable crypto latency-whales
with tailable sports/esports knowledge-whales), which justifies reworking the
engine to score/validate PER CATEGORY rather than pooled.

WHAT THIS DELIBERATELY IS NOT (anti-p-hack discipline, per the handoff hard rules):
  * NO modeling. No features, no learned ranker, no admitted/non-admitted split.
    Just the raw estimand edge e_i = o_i - p_i (matches estimand.py:47) averaged
    per trader per calendar period.
  * NO rule reverse-engineered from outcomes then tested on those same outcomes.
  * MULTI-CUTOFF AGREEMENT is required: the test runs across several period
    cutoffs and only a verdict consistent across cutoffs is reported as such.

STATISTICAL CAVEAT (2026-07-09 review, confirmed): the placebo permutation
shuffles e2 across traders, which preserves both marginals and breaks the
within-trader pairing — but traders SHARE MARKETS, so their e2 values are
positively correlated and full-trader exchangeability does NOT hold. The
permutation null is therefore somewhat too tight (anti-conservative p-values):
a PERSISTENT verdict here is SUPPORTING evidence, not proof. The primary
instrument is scripts/backtest_tail_leaderboard.py (market-clustered); use
this script to corroborate, and weight rho magnitude + cross-cutoff agreement
over raw p-values. A NULL verdict is not affected in that direction (an
anti-conservative test that still finds nothing is a stronger nothing).

METHOD (per slice = pooled ALL, then per category bucket):
  1. Estimand entry: first BUY per (trader, condition_id) — the single mirrorable
     signal MB's one-bet-per-market guard would actually take (estimand.py:73).
     edge = (1 if the bought token won else 0) - price paid.
  2. Split each trader's entries into period 1 (entry time <= cutoff) and
     period 2 (entry time > cutoff) by entry timestamp.
  3. Keep traders with >= MIN_EVENTS resolved entries in BOTH periods. Per such
     trader compute e1 = mean edge in P1, e2 = mean edge in P2.
  4. Persistence statistics over the paired (e1, e2):
       - Spearman rank correlation  (headline; rank-based, robust to outliers)
       - directional lift  P(e2>0 | e1>0) - P(e2>0 | e1<=0)
       - tercile gap  mean e2 of top-third-by-e1 minus bottom-third-by-e1
  5. One-sided upper permutation p-value for each, by shuffling e2 across traders
     (n_perm shuffles). Placebo null summary (mean/std/p95) printed alongside.

SAFETY (the live bots share this DB; keep it read-only and gentle):
  * READ-ONLY — a single SELECT per source. No INSERT/UPDATE/DDL.
  * The scan runs under SET LOCAL statement_timeout (default 300s, --timeout);
    it aggregates to at most one row per (trader, market) entry, not raw ticks.
  * --max-rows is a server-side LIMIT sentinel: an oversized corpus aborts
    BEFORE transfer/materialization, loudly (no silent truncation). Narrow with
    --since/--until (2026-07-09 review: the abort message used to suggest
    narrowing with a knob that did not exist).
  * The trades corpus joins markets in two UNION ALL equi-join branches
    (condition_id keying and numeric-id keying) instead of an OR-join, so the
    planner can hash-join (2026-07-09 review: the OR-join forced nested loops).
  * The resolution/resolved filters are NOT index-backed on the big tables —
    run in a quiet window; use --since for a bounded first run.
  * Pure Python stdlib only (no numpy/scipy) so it runs anywhere the venv boots.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && \
      sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
      venv/bin/python scripts/check_trader_persistence.py            # trades corpus
    ... check_trader_persistence.py --source rejected               # mirror_rejected_signals
    ... check_trader_persistence.py --by-category                   # add per-category slices
    ... check_trader_persistence.py --since 2026-03-01              # bounded first run
    ... check_trader_persistence.py --cutoffs 2026-04-10,2026-05-10  # explicit cutoffs

Self-test (no DB needed, verifies the statistics + placebo calibration):
    python3 scripts/check_trader_persistence.py --self-test

Provenance rule (CLAUDE.md Forbidden Patterns 8/9): the numbers this script
prints are measurements. Cite THIS script's output; do not paraphrase them into
a report without the source. A statistically impossible result means the query
is wrong — stop and fix it, do not explain it away.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
from datetime import datetime
from typing import Optional

# ── Category bucketing (heuristic over markets.category — Polymarket's field,
#    ~61% unknown per mirror_bot.py:3529; the 'unknown' bucket size is reported
#    so the per-category slices carry their own coverage caveat). ──────────────
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


# ── Pure-stdlib statistics ───────────────────────────────────────────────────
def _avg_ranks(xs: list[float]) -> list[float]:
    """Average (fractional) ranks — ties share the mean of their rank block."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank of the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return float("nan")
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / math.sqrt(sxx * syy)


def spearman(x: list[float], y: list[float]) -> float:
    if len(x) < 2:
        return float("nan")
    return pearson(_avg_ranks(x), _avg_ranks(y))


def directional_lift(e1: list[float], e2: list[float]) -> float:
    """P(e2>0 | e1>0) - P(e2>0 | e1<=0). NaN if either group is empty."""
    pos = [b for a, b in zip(e1, e2) if a > 0]
    npos = [b for a, b in zip(e1, e2) if a <= 0]
    if not pos or not npos:
        return float("nan")
    return (sum(1 for b in pos if b > 0) / len(pos)
            - sum(1 for b in npos if b > 0) / len(npos))


def tercile_gap(e1: list[float], e2: list[float]) -> float:
    """mean(e2 | top third by e1) - mean(e2 | bottom third by e1)."""
    n = len(e1)
    if n < 6:
        return float("nan")
    order = sorted(range(n), key=lambda i: e1[i])
    k = n // 3
    bottom = [e2[i] for i in order[:k]]
    top = [e2[i] for i in order[-k:]]
    if not bottom or not top:
        return float("nan")
    return sum(top) / len(top) - sum(bottom) / len(bottom)


class _Perm:
    """One-sided (upper) permutation test bundle over shuffles of e2.

    The null is 'a trader's period-1 edge carries no information about their own
    period-2 edge': shuffling e2 across traders preserves both marginal edge
    distributions and destroys only the within-trader pairing. Exchangeable
    under that H0 by construction, so the null is calibrated (its printed
    mean/p95 should sit at ~0 / small-positive for a true no-signal corpus)."""

    def __init__(self, e1: list[float], e2: list[float], n_perm: int, seed: int):
        self.e1, self.e2 = e1, e2
        self.n = len(e1)
        self.obs = {
            "spearman": spearman(e1, e2),
            "lift": directional_lift(e1, e2),
            "tercile": tercile_gap(e1, e2),
        }
        self.null = {"spearman": [], "lift": [], "tercile": []}
        if self.n >= 2 and n_perm > 0:
            rng = random.Random(seed)
            shuffled = list(e2)
            for _ in range(n_perm):
                rng.shuffle(shuffled)
                self.null["spearman"].append(spearman(e1, shuffled))
                self.null["lift"].append(directional_lift(e1, shuffled))
                self.null["tercile"].append(tercile_gap(e1, shuffled))

    def pvalue(self, stat: str) -> float:
        obs = self.obs[stat]
        vals = [v for v in self.null[stat] if not math.isnan(v)]
        if math.isnan(obs) or not vals:
            return float("nan")
        ge = sum(1 for v in vals if v >= obs)
        return (1 + ge) / (len(vals) + 1)  # +1 both sides: obs is one draw

    def null_summary(self, stat: str) -> tuple[float, float, float]:
        vals = [v for v in self.null[stat] if not math.isnan(v)]
        if not vals:
            return float("nan"), float("nan"), float("nan")
        m = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
        p95 = sorted(vals)[min(len(vals) - 1, int(0.95 * len(vals)))]
        return m, sd, p95


# ── Entry pairing ────────────────────────────────────────────────────────────
def pair_periods(
    entries: list[tuple[str, datetime, float]], cutoff: datetime, min_events: int
) -> tuple[list[float], list[float], int]:
    """entries = [(trader, entry_time, edge)]. Returns (e1_means, e2_means,
    n_traders) over traders with >= min_events resolved entries in BOTH periods."""
    by_trader: dict[str, tuple[list[float], list[float]]] = {}
    for trader, ts, edge in entries:
        p1, p2 = by_trader.setdefault(trader, ([], []))
        (p1 if ts <= cutoff else p2).append(edge)
    e1m, e2m = [], []
    for _, (p1, p2) in by_trader.items():
        if len(p1) >= min_events and len(p2) >= min_events:
            e1m.append(sum(p1) / len(p1))
            e2m.append(sum(p2) / len(p2))
    return e1m, e2m, len(e1m)


def auto_cutoffs(times: list[datetime], n: int = 3) -> list[datetime]:
    """n interior quantile cutoffs of entry time at (i+1)/(n+1) — i.e. the
    25th/50th/75th percentiles for the default n=3 (doc corrected 2026-07-09;
    an earlier docstring wrongly said 33/50/67)."""
    if not times:
        return []
    st = sorted(times)
    qs = [(i + 1) / (n + 1) for i in range(n)]
    return [st[min(len(st) - 1, int(q * len(st)))] for q in qs]


# ── SQL (read-only; one row per FIRST (trader,market) BUY, estimand-faithful) ──
# 2026-07-09 review fixes baked in:
#  * First entry is selected BEFORE any token-mappability filter (the old
#    token filter could promote a later re-entry to "first", deviating from
#    estimand.select_first_entries). Unmappable-token firsts come back with
#    edge NULL and are dropped in Python WITH A PRINTED COUNT.
#  * The markets join is two UNION ALL equi-join branches (planner can hash
#    join) instead of an OR-join (forced nested loop).
#  * LIMIT :cap1 sentinel bounds transfer/materialization server-side.
_TRADES_SQL = """
WITH joined AS (
  SELECT t.user_address, t.timestamp, t.token_id, t.price,
         m.condition_id AS mkey, m.resolution, m.yes_token_id, m.no_token_id,
         COALESCE(m.category, '') AS cat
  FROM trades t
  JOIN markets m ON m.condition_id = t.market_id
  WHERE t.user_address IS NOT NULL AND t.user_address <> ''
    AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
    AND UPPER(t.side) = 'BUY'
    AND t.price > :pmin AND t.price < :pmax
    {since_t} {until_t}
  UNION ALL
  SELECT t.user_address, t.timestamp, t.token_id, t.price,
         COALESCE(m.condition_id, t.market_id) AS mkey,
         m.resolution, m.yes_token_id, m.no_token_id,
         COALESCE(m.category, '') AS cat
  FROM trades t
  JOIN markets m ON CAST(m.id AS TEXT) = t.market_id
  WHERE t.market_id NOT LIKE '0x%'
    AND t.user_address IS NOT NULL AND t.user_address <> ''
    AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
    AND UPPER(t.side) = 'BUY'
    AND t.price > :pmin AND t.price < :pmax
    {since_t} {until_t}
),
firsts AS (
  SELECT DISTINCT ON (user_address, mkey) *
  FROM joined
  ORDER BY user_address, mkey, timestamp ASC
)
SELECT user_address AS trader, timestamp AS entry_time,
       (CASE WHEN token_id = yes_token_id
                  THEN (CASE WHEN resolution = 'YES' THEN 1.0 ELSE 0.0 END)
             WHEN token_id = no_token_id
                  THEN (CASE WHEN resolution = 'NO'  THEN 1.0 ELSE 0.0 END)
        END) - price AS edge,
       cat
FROM firsts
LIMIT :cap1
"""

_REJECTED_SQL = """
SELECT * FROM (
  SELECT DISTINCT ON (r.trader_address, r.market_id)
         r.trader_address AS trader,
         r.event_time     AS entry_time,
         (CASE WHEN r.token_id = m.yes_token_id
                    THEN (CASE WHEN r.resolution = 'YES' THEN 1.0 ELSE 0.0 END)
               WHEN r.token_id = m.no_token_id
                    THEN (CASE WHEN r.resolution = 'NO'  THEN 1.0 ELSE 0.0 END)
               WHEN UPPER(r.side) IN ('YES', 'NO')
                    THEN (CASE WHEN r.resolution = UPPER(r.side) THEN 1.0 ELSE 0.0 END)
          END) - r.price AS edge,
         COALESCE(m.category, '') AS cat
  FROM mirror_rejected_signals r
  LEFT JOIN markets m ON m.condition_id = r.market_id
  WHERE r.resolution IN ('YES', 'NO')
    AND r.price IS NOT NULL AND r.price > :pmin AND r.price < :pmax
    {stage_r} {since_r} {until_r}
  ORDER BY r.trader_address, r.market_id, r.event_time ASC
) q
LIMIT :cap1
"""


async def fetch_entries(db, source: str, pmin: float, pmax: float,
                        timeout_s: int, max_rows: int,
                        since: str = "", until: str = "", stage: str = "all",
                        ) -> tuple[list[dict], int]:
    """Returns (entries, n_unmappable_dropped).

    stage (rejected source only): 'gate'/'pre_gate' engages the
    (rejection_stage, event_time) index — the full-table resolution scan is
    NOT index-backed and times out on the live 17.5M-row table (verified on
    the VPS 2026-07-09). 'gate' is also the cleaner corpus: signals MB
    actually scored, not mechanical pre_gate rejections.
    """
    from sqlalchemy import text
    if source == "trades":
        sql = _TRADES_SQL.format(
            since_t="AND t.timestamp >= :since" if since else "",
            until_t="AND t.timestamp < :until" if until else "",
        )
    else:
        sql = _REJECTED_SQL.format(
            stage_r="AND r.rejection_stage = :stage" if stage != "all" else "",
            since_r="AND r.event_time >= :since" if since else "",
            until_r="AND r.event_time < :until" if until else "",
        )
    params: dict = {"pmin": pmin, "pmax": pmax, "cap1": max_rows + 1}
    if source != "trades" and stage != "all":
        params["stage"] = stage
    if since:
        params["since"] = datetime.fromisoformat(since)
    if until:
        params["until"] = datetime.fromisoformat(until)
    async with db.get_session() as s:
        await s.execute(text(f"SET LOCAL statement_timeout = '{timeout_s}s'"))
        rows = (await s.execute(text(sql), params)).fetchall()
    if len(rows) > max_rows:
        raise SystemExit(
            f"ABORT: corpus exceeds --max-rows {max_rows:,} (LIMIT sentinel hit). "
            f"Narrow with --since/--until or raise --max-rows deliberately "
            f"(do not silently truncate)."
        )
    out, dropped = [], 0
    for r in rows:
        d = dict(r._mapping)
        if d.get("entry_time") is None:
            continue
        if d.get("edge") is None:
            dropped += 1  # true first entry, unmappable token → count, not hide
            continue
        out.append(d)
    return out, dropped


# ── Verdict synthesis (reworked 2026-07-09: NULL no longer silently discards
#    underpowered-but-significant cutoffs, and significant-but-small rho is its
#    own outcome instead of being folded into 'autocorrelation is ~0') ────────
def slice_verdict(per_cutoff: list[dict], alpha: float, sp_thresh: float,
                  min_traders: int) -> tuple[str, str]:
    """Returns (verdict, note). Verdicts:
      PERSISTENT             ALL powered cutoffs: p<alpha AND rho>sp_thresh
      SIGNIFICANT-BUT-SMALL  ALL powered cutoffs p<alpha, but rho<=sp_thresh on some
      NULL                   ALL cutoffs (powered AND testable-underpowered) non-sig
      MIXED                  disagreement, or nulls alongside excluded-significant
      UNDERPOWERED           fewer than 2 powered cutoffs
    """
    def _sig(c: dict) -> bool:
        return (not math.isnan(c["sp_p"])) and c["sp_p"] < alpha

    valid = [c for c in per_cutoff if c["n"] >= min_traders]
    excluded = [c for c in per_cutoff if c["n"] < min_traders]
    excl_sig = [c for c in excluded if c["n"] >= 2 and _sig(c)]
    note = (f"{len(excl_sig)} underpowered cutoff(s) individually significant"
            if excl_sig else "")
    if len(valid) < 2:
        return "UNDERPOWERED", note
    if all(_sig(c) and c["sp"] > sp_thresh for c in valid):
        return "PERSISTENT", note
    if all(_sig(c) for c in valid):
        return "SIGNIFICANT-BUT-SMALL", (note + ("; " if note else "")
                + f"all powered cutoffs p<{alpha} but rho<={sp_thresh} on some")
    if all(not _sig(c) for c in valid):
        if excl_sig:
            return "MIXED", (note + ("; " if note else "")
                    + "powered cutoffs null but an excluded cutoff is significant"
                      " — widen power before calling this NULL")
        return "NULL", note
    return "MIXED", note


async def run(args) -> int:
    from dotenv import load_dotenv
    from base_engine.data.database import Database
    load_dotenv()

    db = Database()
    await db.init()
    try:
        entries, n_unmappable = await fetch_entries(
            db, args.source, args.pmin, args.pmax,
            args.timeout, args.max_rows, args.since, args.until, args.stage)
    finally:
        await db.close()

    if not entries:
        print("No scoreable entries returned — check corpus/filters.")
        return 1

    times = [e["entry_time"] for e in entries]
    cutoffs = ([datetime.fromisoformat(c) for c in args.cutoffs.split(",")]
               if args.cutoffs else auto_cutoffs(times, args.n_cutoffs))

    # Build slices: ALL always; category buckets under --by-category.
    slices: dict[str, list[dict]] = {"ALL": entries}
    if args.by_category:
        for e in entries:
            slices.setdefault("cat:" + bucket_category(e["cat"]), []).append(e)

    print("\n" + "=" * 82)
    print("  Trader-skill PERSISTENCE check — raw cross-period edge autocorrelation")
    print("  SUPPORTING instrument: traders share markets, so the shuffle null is")
    print("  anti-conservative — corroborate with backtest_tail_leaderboard.py (primary).")
    print(f"  source={args.source}"
          + (f" stage={args.stage}" if args.source == "rejected" else "")
          + f"  entries(first-per trader,market)={len(entries):,}"
          f"  unmappable-token firsts dropped={n_unmappable:,}")
    print(f"  min_events/period={args.min_events}  n_perm={args.n_perm}"
          + (f"  window-args=[{args.since or '-inf'},{args.until or '+inf'})" if (args.since or args.until) else ""))
    print(f"  edge = (won?1:0) - price   window = {min(times)} → {max(times)}")
    print(f"  cutoffs = {', '.join(c.isoformat() for c in cutoffs)}")
    print("=" * 82)

    verdicts: dict[str, str] = {}
    for name in sorted(slices, key=lambda k: (k != "ALL", k)):
        sl = [(e["trader"], e["entry_time"], float(e["edge"])) for e in slices[name]]
        tag = name + (f"  ({len(sl):,} entries)" if name != "ALL" else "")
        print(f"\n── slice: {tag} " + "─" * max(0, 60 - len(tag)))
        per_cutoff = []
        for cut in cutoffs:
            e1, e2, n = pair_periods(sl, cut, args.min_events)
            if n < 2:
                print(f"  cutoff {cut.date()}  paired_traders={n:<4}  (too few to test)")
                per_cutoff.append({"n": n, "sp": float("nan"), "sp_p": float("nan")})
                continue
            perm = _Perm(e1, e2, args.n_perm, args.seed)
            sp, sp_p = perm.obs["spearman"], perm.pvalue("spearman")
            lift, lift_p = perm.obs["lift"], perm.pvalue("lift")
            terc, terc_p = perm.obs["tercile"], perm.pvalue("tercile")
            nm, nsd, np95 = perm.null_summary("spearman")
            flag = "  << UNDERPOWERED" if n < args.min_traders else ""
            print(f"  cutoff {cut.date()}  paired_traders={n:<4}{flag}")
            print(f"      spearman rho = {sp:+.3f}  perm_p = {_pf(sp_p)}   "
                  f"[placebo null: mean {nm:+.3f} sd {nsd:.3f} p95 {np95:+.3f}]")
            print(f"      directional lift = {lift:+.3f}  perm_p = {_pf(lift_p)}"
                  f"      (P(e2>0|e1>0) - P(e2>0|e1<=0))")
            print(f"      tercile gap e2   = {terc:+.4f}  perm_p = {_pf(terc_p)}"
                  f"      (top-third e1 minus bottom-third e1)")
            per_cutoff.append({"n": n, "sp": sp, "sp_p": sp_p})
        v, note = slice_verdict(per_cutoff, args.alpha, args.sp_threshold,
                                args.min_traders)
        verdicts[name] = v
        # 2026-07-09 fix: criterion text matches the code — ALL powered cutoffs
        # must pass, not "any 2" (a 2-of-3 run is MIXED, not PERSISTENT).
        print(f"  → slice verdict: {v}"
              f"  (PERSISTENT needs rho>{args.sp_threshold} & p<{args.alpha} on ALL"
              f" cutoffs with >={args.min_traders} traders, min 2 such cutoffs)"
              + (f"\n    note: {note}" if note else ""))

    _print_interpretation(verdicts, args)
    return 0


def _pf(p: float) -> str:
    return "  nan" if math.isnan(p) else f"{p:.4f}"


def _print_interpretation(verdicts: dict[str, str], args) -> None:
    print("\n" + "=" * 82)
    print("  VERDICT")
    print("=" * 82)
    pooled = verdicts.get("ALL", "UNDERPOWERED")
    cat_persistent = [k for k, v in verdicts.items()
                      if k.startswith("cat:") and v == "PERSISTENT"]
    print(f"  pooled (ALL): {pooled}")
    for k in sorted(v for v in verdicts if v != "ALL"):
        print(f"  {k}: {verdicts[k]}")
    print("-" * 82)
    if pooled == "PERSISTENT":
        print("  READ: cross-period edge autocorrelation is > 0 across all powered cutoffs.")
        print("        CAVEAT (2026-07-09): shared-market overlap makes the shuffle null")
        print("        anti-conservative — treat this as SUPPORTING evidence that skill")
        print("        persists; confirm on the tail backtest (primary) before reworking")
        print("        anything. If confirmed: per-category rework, not pooled.")
    elif pooled == "SIGNIFICANT-BUT-SMALL":
        print("  READ: statistically significant but SMALL autocorrelation on all powered")
        print("        cutoffs. Not 'no signal', but likely too weak to rank on alone —")
        print("        weigh against the tail backtest before deciding anything.")
    elif pooled == "NULL":
        if cat_persistent:
            print("  READ: pooled autocorrelation is ~0, BUT these category slices persist:")
            print(f"        {', '.join(cat_persistent)}.")
            print("        The pooled null MASKS a per-category signal (the pooling-crypto-")
            print("        with-sports hypothesis). Per-category rework is justified.")
        else:
            print("  READ: cross-period edge autocorrelation is ~0 across cutoffs and no")
            print("        category slice persists — and NULL survives even though the test")
            print("        is anti-conservative, which strengthens it. No stable trader-")
            print("        level signal here; the Stage-1 FAIL STANDS on this corpus.")
    elif pooled == "UNDERPOWERED":
        print("  READ: too few traders qualify with >= MIN_EVENTS in both periods to")
        print("        conclude. Lower --min-events or widen the corpus (--source) and")
        print("        re-run; do NOT read an underpowered run as a negative.")
    else:  # MIXED
        print("  READ: cutoffs DISAGREE (or powered cutoffs are null while an excluded one")
        print("        is significant) — no consensus. Inconclusive; this is NOT a pass.")
        print("        Do not rework on a mixed result (that is p-hacking the cutoff).")
    print("=" * 82 + "\n")


# ── Self-test: no DB. Confirms the statistics detect real persistence AND that
#    the placebo is calibrated (rejects at ~alpha on pure-null data). ──────────
def _self_test() -> int:
    rng = random.Random(1234)
    print("SELF-TEST — statistics + placebo calibration (no DB)\n")

    # 1. Pure null: e1, e2 independent per trader → expect ~0 rho, p not sig,
    #    placebo null centred at 0, and false-positive rate ~alpha over repeats.
    false_pos = 0
    REPEATS = 40
    for r in range(REPEATS):
        rng2 = random.Random(999 + r)
        e1 = [rng2.gauss(0, 0.1) for _ in range(80)]
        e2 = [rng2.gauss(0, 0.1) for _ in range(80)]
        perm = _Perm(e1, e2, 500, seed=7 + r)
        if perm.pvalue("spearman") < 0.05:
            false_pos += 1
    fp_rate = false_pos / REPEATS
    print(f"  [null] false-positive rate over {REPEATS} independent draws = {fp_rate:.2f}"
          f"  (calibrated ≈ 0.05; must be well under ~0.20)")
    ok_cal = fp_rate <= 0.20

    # 2. Real persistence: e2 = 0.7*latent + noise, e1 = latent + noise.
    e1, e2 = [], []
    for _ in range(120):
        latent = rng.gauss(0, 0.1)
        e1.append(latent + rng.gauss(0, 0.03))
        e2.append(0.7 * latent + rng.gauss(0, 0.03))
    perm = _Perm(e1, e2, 1000, seed=42)
    sp, p = perm.obs["spearman"], perm.pvalue("spearman")
    nm, _, np95 = perm.null_summary("spearman")
    print(f"  [signal] spearman rho = {sp:+.3f}  perm_p = {p:.4f}"
          f"   placebo null mean {nm:+.3f} p95 {np95:+.3f}")
    ok_sig = sp > 0.3 and p < 0.05 and abs(nm) < 0.1

    # 3. Pairing/period logic.
    t0, t1 = datetime(2026, 1, 1), datetime(2026, 6, 1)
    ents = ([("A", t0, 0.2)] * 3 + [("A", t1, 0.15)] * 3
            + [("B", t0, -0.1)] * 3 + [("B", t1, -0.08)] * 3
            + [("C", t0, 0.05)] * 1)  # C dropped: <min_events in P1/P2
    e1, e2, n = pair_periods(ents, datetime(2026, 3, 1), min_events=3)
    ok_pair = (n == 2 and abs(e1[0] - 0.2) < 1e-9 and abs(e2[0] - 0.15) < 1e-9)
    print(f"  [pairing] qualifying traders = {n} (expect 2; C dropped on min_events)")

    ok_cat = (bucket_category("Crypto") == "crypto"
              and bucket_category("NBA") == "sports"
              and bucket_category("") == "unknown"
              and bucket_category("CS2 Majors") == "esports")
    print(f"  [category] bucketing sanity = {ok_cat}")

    # 5. slice_verdict contract (reworked 2026-07-09).
    def _c(n, sp, p):
        return {"n": n, "sp": sp, "sp_p": p}
    v1, _ = slice_verdict([_c(50, 0.3, 0.01), _c(50, 0.3, 0.01)], 0.05, 0.10, 20)
    v2, _ = slice_verdict([_c(50, 0.05, 0.01), _c(50, 0.3, 0.01)], 0.05, 0.10, 20)
    v3, _ = slice_verdict([_c(50, 0.0, 0.8), _c(50, 0.0, 0.7)], 0.05, 0.10, 20)
    v4, n4 = slice_verdict([_c(50, 0.0, 0.8), _c(50, 0.0, 0.7), _c(5, 0.5, 0.01)],
                           0.05, 0.10, 20)
    v5, _ = slice_verdict([_c(50, 0.3, 0.01), _c(50, 0.0, 0.8)], 0.05, 0.10, 20)
    v6, _ = slice_verdict([_c(5, 0.3, 0.01)], 0.05, 0.10, 20)
    ok_sv = (v1 == "PERSISTENT" and v2 == "SIGNIFICANT-BUT-SMALL" and v3 == "NULL"
             and v4 == "MIXED" and "excluded" in n4.replace("underpowered", "excluded")
             and v5 == "MIXED" and v6 == "UNDERPOWERED")
    print(f"  [verdict] PERSISTENT/SBS/NULL/MIXED(excl-sig)/MIXED/UNDERPOWERED = "
          f"{v1[:4]}/{v2[:3]}/{v3}/{v4}/{v5}/{v6[:5]} : {ok_sv}")

    allok = ok_cal and ok_sig and ok_pair and ok_cat and ok_sv
    print("\n  RESULT:", "PASS" if allok else "FAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only trader-skill persistence check")
    ap.add_argument("--source", choices=["trades", "rejected"], default="trades",
                    help="edge corpus: trades (ranking corpus) or mirror_rejected_signals")
    ap.add_argument("--stage", choices=["all", "pre_gate", "gate"], default="all",
                    help="rejected source only: restrict rejection_stage (gate is "
                         "index-backed + the cleaner scored corpus; default all "
                         "may time out on the full table)")
    ap.add_argument("--cutoffs", default="",
                    help="comma-separated ISO period cutoffs (default: interior "
                         "quartiles, 25/50/75 pct of entry time)")
    ap.add_argument("--n-cutoffs", type=int, default=3, dest="n_cutoffs",
                    help="number of auto cutoffs when --cutoffs unset (default 3)")
    ap.add_argument("--min-events", type=int, default=8, dest="min_events",
                    help="min resolved entries per trader PER period (default 8)")
    ap.add_argument("--min-traders", type=int, default=20, dest="min_traders",
                    help="paired-trader count below which a cutoff is UNDERPOWERED (default 20)")
    ap.add_argument("--by-category", action="store_true",
                    help="also report per-category slices (crypto/sports/esports/...)")
    ap.add_argument("--n-perm", type=int, default=2000, dest="n_perm",
                    help="placebo permutation replicates (default 2000)")
    ap.add_argument("--alpha", type=float, default=0.05, help="one-sided level (default 0.05)")
    ap.add_argument("--sp-threshold", type=float, default=0.10, dest="sp_threshold",
                    help="min spearman rho to call 'clearly > 0' (default 0.10)")
    ap.add_argument("--pmin", type=float, default=0.02, help="drop dust prices below (default 0.02)")
    ap.add_argument("--pmax", type=float, default=0.98, help="drop prices above (default 0.98)")
    ap.add_argument("--since", default="", help="ISO lower bound on entry time (use on first run)")
    ap.add_argument("--until", default="", help="ISO upper bound on entry time")
    ap.add_argument("--timeout", type=int, default=300, help="statement_timeout seconds (default 300)")
    ap.add_argument("--max-rows", type=int, default=3_000_000, dest="max_rows",
                    help="server-side LIMIT sentinel; abort above this (no silent truncation)")
    ap.add_argument("--seed", type=int, default=20260708, help="deterministic permutation seed")
    ap.add_argument("--self-test", action="store_true", help="run offline stat/calibration self-test and exit")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
