#!/usr/bin/env python3
"""Forward stack-vs-first-buy test on SHADOW records (pre-registered 2026-07-17).

QUESTION (operator, 2026-07-17): for tracked traders, does following their
re-buys ("the stack") beat our one-bet-per-market first-buy-only policy —
measured at OUR executable prices, not theirs?

WHY FORWARD-ONLY: the retrospective arm is VOID — the data-api trade record
is a structural SUBSET of chain truth (probe 0xf705fa: 28,926 API BUYs vs
60,576 chain BUYs; MB_STATE §0 item 8). The shadow JSONL records EVERY roster
BUY with the executable ask at detection time, which is the decision-relevant
price. Zero extra collection needed.

PRE-REGISTERED DESIGN (corrected 2026-07-19 by adversarial review BEFORE any
data — legitimate pre-registration fix; do not tune after looking at results):
  Prices: EXECUTABLE best_ask from az.repair_records (the /book-ladder repair
  the money-gate readout uses), NOT the raw /price field (finding #2/#4).
  Unit: resolved (trader, token) position with >=1 OK first-buy record.
  edge_first = outcome - first_ask          (our current policy)
  edge_stack = outcome - vwap(all OK-record asks, weighted by whale_size_usd)
  Delta      = edge_stack - edge_first      (per position, equal-weight)
  Verdict inputs: TOKEN-CLUSTERED bootstrap (seed 7, 2000 reps) on Delta.
  POWER BAR: >=30 DISTINCT resolved multi-buy MARKETS (token clusters — the
  bootstrap's inference unit), NOT position count: cross-trader token overlap
  makes positions >> clusters, so powering on positions could fire a verdict
  on ~2 markets (finding #5). Concentration (top market + top trader) is
  disclosed inline before any aggregate (standing operator rule).
  DECISION RULE: Delta > 0 with P(Delta>0) >= 0.95 AND >=30 clusters ->
  re-buy policy becomes a DESIGN PROPOSAL to the operator (it touches the
  one-bet-per-market guard — never auto-applied). Anything else -> first-buy-
  only stands. UNDERPOWERED below the bar: report, no verdict.
  Records: post-quote-fix epoch only (--trust-after, default 1783985376),
  enforced by repair_records' ladderless-trust rule.
  Resolution labels: markets table via yes/no_token_id (fresh, never the
  stale analyze_shadow gamma cache — §7 landmine).

INVOCATION (VPS):
  set -a; . /opt/pa2-shared/.env 2>/dev/null; set +a   # or pass DATABASE_URL
  python3 scripts/stack_vs_firstbuy_forward.py \
      --log /opt/pa2-shared/mirror3_shadow.jsonl
  python3 scripts/stack_vs_firstbuy_forward.py --self-test   # offline
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402  (canonical repair + load, reused)

QUOTE_FIX_EPOCH = 1783985376  # 2026-07-13 23:29Z quote-swap fix deploy


def load_positions(records) -> dict:
    """(trader, token) -> list of OK records (ask, usd, first_buy, ts), sorted
    by time. INPUT MUST BE az.repair_records() OUTPUT — best_ask/verdict are
    then the canonical /book-ladder-repaired EXECUTABLE values, identical to
    what the money-gate readout uses (adversarial review 2026-07-19, finding
    #2/#4: reading RAW /price best_ask/verdict diverged from every canonical
    consumer — a stale/crossed /price quote would enter the estimand at a
    flattered price or dodge a gate the ladder repair would have failed).
    repair_records already applies the trust_after / ladderless rule, so this
    does NO epoch filtering of its own."""
    pos: dict = {}
    for r in records:
        if r.get("verdict") != "OK" or r.get("best_ask") is None:
            continue
        key = (str(r.get("trader", "")).lower(), r.get("token_id"))
        pos.setdefault(key, []).append(
            (float(r["best_ask"]), float(r.get("whale_size_usd") or 0.0),
             bool(r.get("first_buy")), float(r.get("detect_ts", 0))))
    for v in pos.values():
        v.sort(key=lambda t: t[3])
    return pos


def grade(pos: dict, outcomes: dict) -> list[dict]:
    """Per resolved position: edge_first, edge_stack, Delta. outcomes maps
    token_id -> 0.0/1.0."""
    rows = []
    for (trader, tok), recs in pos.items():
        if tok not in outcomes or not recs or not recs[0][2]:
            continue  # unresolved, empty, or the first OK record isn't the
            # first-buy (entry missed by gates -> not our policy's position)
        out = outcomes[tok]
        first_ask = recs[0][0]
        wsum = sum(u for _, u, _, _ in recs)
        vwap = (sum(a * u for a, u, _, _ in recs) / wsum) if wsum > 0 else \
            sum(a for a, _, _, _ in recs) / len(recs)
        rows.append({"trader": trader, "token": tok, "n_buys": len(recs),
                     "ef": out - first_ask, "es": out - vwap,
                     "d": (out - vwap) - (out - first_ask)})
    return rows


def delta_by_cluster(rows: list[dict]) -> dict:
    """{token: [Delta, ...]} — the token cluster is the bootstrap unit."""
    by: dict = {}
    for r in rows:
        by.setdefault(r["token"], []).append(r["d"])
    return by


def report(rows: list[dict], power_bar: int, p_bar: float) -> str:
    multi = [r for r in rows if r["n_buys"] >= 2]
    # The bootstrap's inference unit is the TOKEN CLUSTER, not the position;
    # cross-trader token overlap (routine — tracked traders pile into the same
    # hot markets) means positions can far exceed distinct clusters. Power the
    # verdict on DISTINCT CLUSTERS so a 2-market sample can't clear a 30 bar
    # (adversarial review 2026-07-19, finding #5). Concentration disclosed
    # inline before any aggregate (standing operator rule 2026-07-15).
    clusters = {r["token"] for r in multi}
    n_clusters = len(clusters)
    lines = [f"resolved positions: {len(rows)}  multi-buy positions: "
             f"{len(multi)}  distinct multi-buy markets (power unit): "
             f"{n_clusters}  power bar: {power_bar}"]
    if rows:
        lines.append(f"  all: mean edge_first {sum(r['ef'] for r in rows)/len(rows):+.4f}"
                     f"  mean edge_stack {sum(r['es'] for r in rows)/len(rows):+.4f}")
    if multi:
        md = sum(r["d"] for r in multi) / len(multi)
        # canonical bootstrap (the money-gate readout's own), not a copy
        # (root-cause audit 2026-07-19: a copy-pasted statistic can drift)
        p = az.cluster_bootstrap_p(delta_by_cluster(multi))
        by_tok = Counter(r["token"] for r in multi)
        by_tr = Counter(r["trader"] for r in multi)
        top_tok_n = by_tok.most_common(1)[0][1]
        top_tr, top_tr_n = by_tr.most_common(1)[0]
        lines.append(f"  multi: mean Delta {md:+.4f}  P(Delta>0) {p:.3f}  "
                     f"conc top-market {top_tok_n}/{len(multi)} pos, "
                     f"top-trader {top_tr[:10]}… {top_tr_n}/{len(multi)} pos")
        if n_clusters >= power_bar:
            if md > 0 and p >= p_bar:
                lines.append("VERDICT: STACK BEATS FIRST-BUY — take a re-buy "
                             "policy DESIGN PROPOSAL to the operator (touches "
                             "the one-bet-per-market guard; never auto-apply). "
                             "Present WITH the concentration + a leave-one-out.")
            else:
                lines.append("VERDICT: first-buy-only STANDS (stack does not "
                             "clear the pre-registered bar).")
        else:
            lines.append(f"UNDERPOWERED ({n_clusters}/{power_bar} distinct "
                         "multi-buy markets) — keep collecting, no verdict.")
    else:
        lines.append("UNDERPOWERED (0 multi-buy resolved) — keep collecting.")
    return "\n".join(lines)


def self_test() -> int:
    # Runs the CANONICAL pipeline load->repair->load_positions on RAW records
    # (with /book ladders), proving the forward test prices off the repaired
    # ladder ask (finding #2/#4) and powers on clusters (finding #5).
    E = QUOTE_FIX_EPOCH

    def rec(tr, tok, ladder_ask, ladder_bid, wp, usd, first, dt):
        return {"trader": tr, "token_id": tok, "verdict": "OK",
                "best_ask": wp, "best_bid": None, "whale_price": wp,
                "whale_size_usd": usd, "first_buy": first, "detect_ts": dt,
                "book_asks": [{"price": ladder_ask}],
                "book_bids": [{"price": ladder_bid}]}

    # t1 (0xa): first RAW best_ask .50 but /book min-ask .52 -> repair prices
    # it at .52 (raw-ask bypass fixed); re-buy ladder .40; equal $ -> vwap .46;
    # win -> ef=1-.52=.48, es=1-.46=.54, d=+.06. A 3rd fill's ladder repairs to
    # PRICE_RAN_AWAY (book .60 > wp .50 + .02) -> gate-EXCLUDED, must not enter.
    raw = [
        rec("0xa", "t1", 0.52, 0.50, 0.50, 100, True, E + 1),
        rec("0xa", "t1", 0.40, 0.38, 0.40, 100, False, E + 2),
        rec("0xa", "t1", 0.60, 0.55, 0.50, 900, False, E + 3),  # -> RAN_AWAY
        rec("0xa", "t2", 0.30, 0.28, 0.30, 50, True, E + 4),    # single-buy
    ]
    recs, _ = az.repair_records([dict(r) for r in raw], 0.02, 0.05, E)
    rows = grade(load_positions(recs), {"t1": 1.0, "t2": 0.0})
    a = next(r for r in rows if r["token"] == "t1")
    assert a["n_buys"] == 2, a                       # RAN_AWAY fill excluded
    assert abs(a["ef"] - 0.48) < 1e-9, a             # priced off REPAIRED .52
    assert abs(a["es"] - 0.54) < 1e-9 and abs(a["d"] - 0.06) < 1e-9, a
    print("  [repair] prices off /book ladder + gate-excludes RAN_AWAY : True")

    # finding #5: 30 positions across only 2 tokens (cross-trader overlap) must
    # be UNDERPOWERED at power_bar 30 (2 clusters), never a POWERED verdict.
    many = []
    for i in range(15):
        for tok in ("tA", "tB"):
            many.append(rec(f"0x{i:02d}", tok, 0.50, 0.49, 0.50, 100, True, E + 10))
            many.append(rec(f"0x{i:02d}", tok, 0.40, 0.39, 0.40, 100, False, E + 11))
    mrecs, _ = az.repair_records([dict(r) for r in many], 0.02, 0.05, E)
    mrows = grade(load_positions(mrecs), {"tA": 1.0, "tB": 1.0})
    multi = [r for r in mrows if r["n_buys"] >= 2]
    out = report(mrows, power_bar=30, p_bar=0.95)
    assert len(multi) == 30, len(multi)              # 30 positions...
    assert "UNDERPOWERED (2/30 distinct" in out, out  # ...but only 2 clusters
    assert "conc top-market" in out and "top-trader" in out, out
    print("  [power]  30 positions / 2 clusters -> UNDERPOWERED : True")
    print("  [conc]   concentration disclosed before verdict : True")

    # a genuine 30-cluster win DOES clear the bar (sanity: gate not too strict)
    win = []
    for i in range(30):
        win.append(rec("0xw", f"m{i}", 0.50, 0.49, 0.50, 100, True, E + 20))
        win.append(rec("0xw", f"m{i}", 0.40, 0.39, 0.40, 100, False, E + 21))
    wrecs, _ = az.repair_records([dict(r) for r in win], 0.02, 0.05, E)
    wrows = grade(load_positions(wrecs), {f"m{i}": 1.0 for i in range(30)})
    wout = report(wrows, power_bar=30, p_bar=0.95)
    assert "STACK BEATS FIRST-BUY" in wout, wout      # 30 clusters, all d>0
    print("  [power]  30 distinct clusters, all Delta>0 -> POWERED : True")
    print("self-test PASS")
    return 0


async def run(args) -> int:
    # DB labels via the same pattern as shadow_readout (fresh, token-keyed)
    sys.path.insert(0, ".")
    from base_engine.data.database import Database  # noqa: PLC0415
    db = Database()
    await db.init()
    # canonical pipeline (finding #2/#4): load -> repair (re-derives executable
    # best_ask from the /book ladder + re-adjudicates the gate, applying the
    # trust_after rule) -> then group. Identical to the money-gate readout.
    recs = az.load_records(args.log)
    recs, _ = az.repair_records(recs, args.max_chase, args.max_spread,
                                args.trust_after)
    pos = load_positions(recs)
    tokens = sorted({tok for (_, tok) in pos if tok})
    outcomes: dict = {}
    async with db.get_session() as s:
        from sqlalchemy import text  # noqa: PLC0415
        for i in range(0, len(tokens), 200):
            batch = tokens[i:i + 200]
            q = await s.execute(text(
                "SELECT yes_token_id, no_token_id, resolution FROM markets "
                "WHERE resolved = true AND (yes_token_id = ANY(:t) "
                "OR no_token_id = ANY(:t))"), {"t": batch})
            for yes_tok, no_tok, res in q:
                if res == "YES":
                    outcomes[yes_tok] = 1.0
                    outcomes[no_tok] = 0.0
                elif res == "NO":
                    outcomes[yes_tok] = 0.0
                    outcomes[no_tok] = 1.0
    rows = grade(pos, outcomes)
    print(report(rows, args.power_bar, args.p_bar))
    await db.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--trust-after", type=float, default=QUOTE_FIX_EPOCH)
    ap.add_argument("--power-bar", type=int, default=30,
                    help="min DISTINCT resolved multi-buy markets (clusters) "
                         "for a verdict")
    ap.add_argument("--p-bar", type=float, default=0.95)
    ap.add_argument("--max-chase", type=float, default=0.02, dest="max_chase")
    ap.add_argument("--max-spread", type=float, default=0.05, dest="max_spread")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
