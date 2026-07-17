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

PRE-REGISTERED DESIGN (do not tune after looking at results):
  Unit: resolved (trader, token) position with >=1 OK first-buy record.
  edge_first = outcome - first_ask          (our current policy)
  edge_stack = outcome - vwap(all OK-record asks, weighted by whale_size_usd)
  Delta      = edge_stack - edge_first      (per position, equal-weight)
  Verdict inputs: market-clustered bootstrap (seed 7, 2000 reps) on Delta.
  POWER BAR: >=30 resolved positions with >=2 OK BUY records ("multi" subset).
  DECISION RULE: Delta > 0 with P(Delta>0) >= 0.95 on the multi subset ->
  re-buy policy becomes a DESIGN PROPOSAL to the operator (it touches the
  one-bet-per-market guard — never auto-applied). Anything else -> first-buy-
  only stands. UNDERPOWERED below the bar: report, no verdict.
  Records: post-quote-fix epoch only (--trust-after, default 1783985376).
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
import json
import random
import sys

QUOTE_FIX_EPOCH = 1783985376  # 2026-07-13 23:29Z quote-swap fix deploy


def load_positions(lines, trust_after: float) -> dict:
    """(trader, token) -> list of OK BUY records (ask, usd, first_buy, ts)."""
    pos: dict = {}
    for line in lines:
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        if r.get("detect_ts", 0) < trust_after:
            continue
        if r.get("verdict") != "OK" or r.get("best_ask") is None:
            continue
        key = (r.get("trader", "").lower(), r.get("token_id"))
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


def cluster_bootstrap_p(rows: list[dict], reps: int = 2000, seed: int = 7
                        ) -> float:
    """P(mean Delta > 0), clustered by token (position ~ market here)."""
    random.seed(seed)
    by = {}
    for r in rows:
        by.setdefault(r["token"], []).append(r["d"])
    keys = list(by)
    if not keys:
        return float("nan")
    wins = 0
    for _ in range(reps):
        s = [d for k in (random.choice(keys) for _ in keys) for d in by[k]]
        if sum(s) / len(s) > 0:
            wins += 1
    return wins / reps


def report(rows: list[dict], power_bar: int, p_bar: float) -> str:
    multi = [r for r in rows if r["n_buys"] >= 2]
    lines = [f"resolved positions: {len(rows)}  multi-buy (the estimand): "
             f"{len(multi)}  power bar: {power_bar}"]
    if rows:
        lines.append(f"  all: mean edge_first {sum(r['ef'] for r in rows)/len(rows):+.4f}"
                     f"  mean edge_stack {sum(r['es'] for r in rows)/len(rows):+.4f}")
    if multi:
        md = sum(r["d"] for r in multi) / len(multi)
        p = cluster_bootstrap_p(multi)
        lines.append(f"  multi: mean Delta {md:+.4f}  P(Delta>0) {p:.3f}")
        if len(multi) >= power_bar:
            if md > 0 and p >= p_bar:
                lines.append("VERDICT: STACK BEATS FIRST-BUY — take a re-buy "
                             "policy DESIGN PROPOSAL to the operator (touches "
                             "the one-bet-per-market guard; never auto-apply).")
            else:
                lines.append("VERDICT: first-buy-only STANDS (stack does not "
                             "clear the pre-registered bar).")
        else:
            lines.append(f"UNDERPOWERED ({len(multi)}/{power_bar} multi-buy "
                         "resolved) — keep collecting, no verdict.")
    else:
        lines.append("UNDERPOWERED (0 multi-buy resolved) — keep collecting.")
    return "\n".join(lines)


def self_test() -> int:
    # position A: first ask .50, re-buy ask .40, equal $ -> vwap .45; win.
    # stack beats first by +0.05. position B: single-buy, excluded from multi.
    # position C: first record isn't first_buy -> excluded entirely.
    lines = [
        json.dumps({"trader": "0xa", "token_id": "t1", "verdict": "OK",
                    "best_ask": 0.50, "whale_size_usd": 100, "first_buy": True,
                    "detect_ts": QUOTE_FIX_EPOCH + 1}),
        json.dumps({"trader": "0xa", "token_id": "t1", "verdict": "OK",
                    "best_ask": 0.40, "whale_size_usd": 100, "first_buy": False,
                    "detect_ts": QUOTE_FIX_EPOCH + 2}),
        json.dumps({"trader": "0xa", "token_id": "t2", "verdict": "OK",
                    "best_ask": 0.30, "whale_size_usd": 50, "first_buy": True,
                    "detect_ts": QUOTE_FIX_EPOCH + 3}),
        json.dumps({"trader": "0xb", "token_id": "t3", "verdict": "OK",
                    "best_ask": 0.60, "whale_size_usd": 10, "first_buy": False,
                    "detect_ts": QUOTE_FIX_EPOCH + 4}),
        # pre-epoch and gated records must be ignored
        json.dumps({"trader": "0xa", "token_id": "t1", "verdict": "OK",
                    "best_ask": 0.10, "whale_size_usd": 900, "first_buy": False,
                    "detect_ts": QUOTE_FIX_EPOCH - 5}),
        json.dumps({"trader": "0xa", "token_id": "t1",
                    "verdict": "PRICE_RAN_AWAY", "best_ask": 0.99,
                    "whale_size_usd": 900, "first_buy": False,
                    "detect_ts": QUOTE_FIX_EPOCH + 9}),
    ]
    pos = load_positions(lines, QUOTE_FIX_EPOCH)
    rows = grade(pos, {"t1": 1.0, "t2": 0.0, "t3": 1.0})
    assert len(rows) == 2, rows  # t3 excluded (no first-buy record)
    a = next(r for r in rows if r["token"] == "t1")
    assert abs(a["ef"] - 0.50) < 1e-9 and abs(a["es"] - 0.55) < 1e-9
    assert abs(a["d"] - 0.05) < 1e-9 and a["n_buys"] == 2
    b = next(r for r in rows if r["token"] == "t2")
    assert b["n_buys"] == 1 and abs(b["ef"] - (-0.30)) < 1e-9
    p = cluster_bootstrap_p([a])
    assert p == 1.0, p  # single positive cluster -> always > 0
    out = report(rows, power_bar=30, p_bar=0.95)
    assert "UNDERPOWERED (1/30" in out, out
    print("self-test PASS")
    return 0


async def run(args) -> int:
    # DB labels via the same pattern as shadow_readout (fresh, token-keyed)
    sys.path.insert(0, ".")
    from base_engine.data.database import Database  # noqa: PLC0415
    db = Database()
    await db.init()
    with open(args.log) as f:
        pos = load_positions(f, args.trust_after)
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
    ap.add_argument("--power-bar", type=int, default=30)
    ap.add_argument("--p-bar", type=float, default=0.95)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
