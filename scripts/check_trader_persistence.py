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
  * A real verdict requires (a) a CALIBRATED PLACEBO and (b) MULTI-CUTOFF
    AGREEMENT. Both are built in: the placebo is a label permutation that breaks
    a trader's own period-1↔period-2 link while preserving every marginal (its
    null distribution is printed so you can SEE it is centred at zero); the test
    is run across several period cutoffs and only a verdict that agrees across
    cutoffs is reported as such.

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
  * --max-rows aborts loudly if the entry set is larger than expected rather
    than silently truncating (Forbidden Pattern: no silent caps).
  * Pure Python stdlib only (no numpy/scipy) so it runs anywhere the venv boots.

INVOCATION (on the VPS):
    cd /opt/polymarket-ai-v2 && \
      sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
      venv/bin/python scripts/check_trader_persistence.py            # trades corpus
    ... check_trader_persistence.py --source rejected               # mirror_rejected_signals
    ... check_trader_persistence.py --by-category                   # add per-category slices
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
    """n balanced calendar cutoffs at interior quantiles of entry time
    (default 3: 33rd/50th/67th percentile)."""
    if not times:
        return []
    st = sorted(times)
    qs = [(i + 1) / (n + 1) for i in range(n)]
    return [st[min(len(st) - 1, int(q * len(st)))] for q in qs]


# ── SQL (read-only; edge computed in-DB, one row per first (trader,market) entry) ──
_TRADES_SQL = """
SELECT DISTINCT ON (t.user_address, COALESCE(m.condition_id, t.market_id))
       t.user_address AS trader,
       t.timestamp    AS entry_time,
       (CASE WHEN t.token_id = m.yes_token_id
                  THEN (CASE WHEN m.resolution = 'YES' THEN 1.0 ELSE 0.0 END)
             WHEN t.token_id = m.no_token_id
                  THEN (CASE WHEN m.resolution = 'NO'  THEN 1.0 ELSE 0.0 END)
        END) - t.price AS edge,
       COALESCE(m.category, '') AS cat
FROM trades t
JOIN markets m
  ON (m.condition_id = t.market_id OR CAST(m.id AS TEXT) = t.market_id)
WHERE t.user_address IS NOT NULL AND t.user_address <> ''
  AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
  AND UPPER(t.side) = 'BUY'
  AND t.price > :pmin AND t.price < :pmax
  AND t.token_id IN (m.yes_token_id, m.no_token_id)
ORDER BY t.user_address, COALESCE(m.condition_id, t.market_id), t.timestamp ASC
"""

_REJECTED_SQL = """
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
ORDER BY r.trader_address, r.market_id, r.event_time ASC
"""


async def fetch_entries(db, source: str, pmin: float, pmax: float,
                        timeout_s: int, max_rows: int) -> list[dict]:
    from sqlalchemy import text
    sql = _TRADES_SQL if source == "trades" else _REJECTED_SQL
    async with db.get_session() as s:
        await s.execute(text(f"SET LOCAL statement_timeout = '{timeout_s}s'"))
        rows = (await s.execute(text(sql), {"pmin": pmin, "pmax": pmax})).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        if d.get("edge") is None or d.get("entry_time") is None:
            continue  # unmappable token / null → not scoreable
        out.append(d)
    if len(out) > max_rows:
        raise SystemExit(
            f"ABORT: {len(out):,} entries exceed --max-rows {max_rows:,}. This is "
            f"more (trader,market) first-entries than expected — narrow the corpus "
            f"or raise --max-rows deliberately (do not silently truncate)."
        )
    return out


# ── Verdict synthesis ────────────────────────────────────────────────────────
def slice_verdict(per_cutoff: list[dict], alpha: float, sp_thresh: float,
                  min_traders: int) -> str:
    valid = [c for c in per_cutoff if c["n"] >= min_traders]
    if len(valid) < 2:
        return "UNDERPOWERED"
    persistent = all(c["sp"] > sp_thresh and not math.isnan(c["sp_p"])
                     and c["sp_p"] < alpha for c in valid)
    if persistent:
        return "PERSISTENT"
    null = all(math.isnan(c["sp_p"]) or c["sp_p"] >= alpha
               or abs(c["sp"]) < sp_thresh for c in valid)
    if null:
        return "NULL"
    return "MIXED"


async def run(args) -> int:
    from dotenv import load_dotenv
    from base_engine.data.database import Database
    load_dotenv()

    db = Database()
    await db.init()
    try:
        entries = await fetch_entries(db, args.source, args.pmin, args.pmax,
                                      args.timeout, args.max_rows)
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
    print(f"  source={args.source}  entries(first-per trader,market)={len(entries):,}"
          f"  min_events/period={args.min_events}  n_perm={args.n_perm}")
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
        verdicts[name] = slice_verdict(per_cutoff, args.alpha, args.sp_threshold,
                                       args.min_traders)
        print(f"  → slice verdict: {verdicts[name]}"
              f"  (PERSISTENT needs rho>{args.sp_threshold} & p<{args.alpha} on"
              f" >=2 cutoffs with >={args.min_traders} traders)")

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
        print("  READ: cross-period edge autocorrelation is CLEARLY > 0 and holds across")
        print("        cutoffs against a calibrated placebo. Trader skill persists — the")
        print("        signal is real and the pooled ranking method lost it. Reworking is")
        print("        justified; prime move = score/validate PER CATEGORY, not pooled.")
    elif pooled == "NULL":
        if cat_persistent:
            print("  READ: pooled autocorrelation is ~0, BUT these category slices persist:")
            print(f"        {', '.join(cat_persistent)}.")
            print("        The pooled null MASKS a per-category signal (the pooling-crypto-")
            print("        with-sports hypothesis). Per-category rework is justified.")
        else:
            print("  READ: cross-period edge autocorrelation is ~0 across cutoffs and no")
            print("        category slice persists. No stable trader-level signal exists;")
            print("        NO ranking rework can recover one. The Stage-1 FAIL STANDS.")
    elif pooled == "UNDERPOWERED":
        print("  READ: too few traders qualify with >= MIN_EVENTS in both periods to")
        print("        conclude. Lower --min-events or widen the corpus (--source) and")
        print("        re-run; do NOT read an underpowered run as a negative.")
    else:  # MIXED
        print("  READ: cutoffs DISAGREE — no multi-cutoff consensus. Inconclusive; this is")
        print("        NOT a pass. Do not rework on a mixed result (that is p-hacking the")
        print("        cutoff). Report the disagreement.")
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

    allok = ok_cal and ok_sig and ok_pair and ok_cat
    print("\n  RESULT:", "PASS" if allok else "FAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only trader-skill persistence check")
    ap.add_argument("--source", choices=["trades", "rejected"], default="trades",
                    help="edge corpus: trades (ranking corpus) or mirror_rejected_signals")
    ap.add_argument("--cutoffs", default="",
                    help="comma-separated ISO period cutoffs (default: 33/50/67pct of time)")
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
    ap.add_argument("--timeout", type=int, default=300, help="statement_timeout seconds (default 300)")
    ap.add_argument("--max-rows", type=int, default=3_000_000, dest="max_rows",
                    help="abort if entry set exceeds this (no silent truncation)")
    ap.add_argument("--seed", type=int, default=20260708, help="deterministic permutation seed")
    ap.add_argument("--self-test", action="store_true", help="run offline stat/calibration self-test and exit")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
