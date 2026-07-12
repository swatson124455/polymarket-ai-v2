#!/usr/bin/env python3
"""Shadow-log analysis — the pre-registered readout for the v3 copy watcher.

INPUT: the JSONL written by mirror_v3/copy_watcher.py (one record per
detected roster fill: verdict, shadow_fill (real CLOB ask), whale_price,
detect_lag_s, first_buy, ...).

WHAT IT ANSWERS (registered in docs/MB_STATE.md §0 BEFORE data was read):
  1. Operability  — gate rates: OK / PRICE_RAN_AWAY / SPREAD_TOO_WIDE /
                    NO_BOOK, overall and per trader. Pre-registered bar:
                    OK-rate >= 50% on first-buys.
  2. Latency      — detect_lag_s p50/p95 (target ~2-4s vs the ~10s REST lag).
  3. Cost (tax)   — shadow_fill - whale_price on OK first-buys: what copying
                    actually costs at our latency, the number every
                    retrospective instrument failed to measure.
  4. Edge         — with --gamma-cache (resolutions JSON from
                    backfill_resolutions_gamma.py): per-token outcome ->
                    shadow edge = outcome - shadow_fill on OK first-buys,
                    token-clustered bootstrap P(edge>0). Pre-registered bar:
                    P >= 0.95 on >= 30 resolved markets, pooled edge >= +0.02
                    net of the --fee haircut. UNRESOLVED tokens are counted,
                    never guessed.

READ-ONLY. Pure stdlib. --self-test runs offline on synthetic records.
INVOCATION (VPS):
    /opt/polymarket-ai-v2/venv/bin/python /opt/mirror3/scripts/analyze_shadow.py \
      --log /opt/pa2-shared/mirror3_shadow.jsonl \
      --gamma-cache /tmp/copyable_cache/gamma_resolutions.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from typing import Optional


def load_records(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue  # torn tail line from a live writer is expected
    return out


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def token_outcomes(gamma_path: str) -> dict[str, int]:
    """token_id -> 1 (won) / 0 (lost), from the gamma resolutions cache
    (resolution YES means token[0]/yes_token won; wording-independent)."""
    with open(gamma_path) as f:
        gamma = json.load(f)
    out: dict[str, int] = {}
    for rec in gamma.values():
        res = rec.get("resolution")
        yes_t, no_t = rec.get("yes_token_id"), rec.get("no_token_id")
        if res not in ("YES", "NO") or not yes_t or not no_t:
            continue
        out[str(yes_t)] = 1 if res == "YES" else 0
        out[str(no_t)] = 0 if res == "YES" else 1
    return out


def cluster_bootstrap_p(edges_by_token: dict[str, list[float]],
                        n_boot: int = 2000, seed: int = 7) -> float:
    """P(pooled mean edge > 0), resampling TOKENS (the cluster unit)."""
    toks = list(edges_by_token)
    if not toks:
        return float("nan")
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(toks) for _ in toks]
        vals = [e for t in sample for e in edges_by_token[t]]
        means.append(sum(vals) / len(vals))
    return sum(1 for m in means if m > 0) / len(means)


def analyze(records: list[dict], outcomes: Optional[dict[str, int]],
            fee: float, econ_floor: float, p_min: float,
            min_markets: int) -> dict:
    firsts = [r for r in records if r.get("first_buy")]
    verd = Counter(r.get("verdict") for r in firsts)
    lags = [float(r["detect_lag_s"]) for r in records
            if isinstance(r.get("detect_lag_s"), (int, float))]
    ok = [r for r in firsts if r.get("verdict") == "OK"
          and isinstance(r.get("shadow_fill"), (int, float))]
    taxes = [float(r["shadow_fill"]) - float(r["whale_price"]) for r in ok
             if isinstance(r.get("whale_price"), (int, float))]

    by_trader: dict[str, Counter] = defaultdict(Counter)
    for r in firsts:
        by_trader[str(r.get("trader"))][str(r.get("verdict"))] += 1

    res = {
        "records": len(records),
        "first_buys": len(firsts),
        "verdicts": dict(verd),
        "ok_rate": (verd.get("OK", 0) / len(firsts)) if firsts else float("nan"),
        "lag_p50": pct(lags, 0.50),
        "lag_p95": pct(lags, 0.95),
        "tax_mean": (sum(taxes) / len(taxes)) if taxes else float("nan"),
        "tax_p50": pct(taxes, 0.50),
        "by_trader": {t: dict(c) for t, c in sorted(by_trader.items())},
    }

    if outcomes is not None:
        edges_by_token: dict[str, list[float]] = defaultdict(list)
        unresolved = 0
        for r in ok:
            o = outcomes.get(str(r.get("token_id")))
            if o is None:
                unresolved += 1
                continue
            fill = float(r["shadow_fill"])
            edges_by_token[str(r["token_id"])].append(o - fill - fee * fill)
        vals = [e for es in edges_by_token.values() for e in es]
        n_mkts = len(edges_by_token)
        p = cluster_bootstrap_p(edges_by_token)
        edge = (sum(vals) / len(vals)) if vals else float("nan")
        if n_mkts < min_markets:
            verdict = f"UNDERPOWERED ({n_mkts}/{min_markets} resolved mkts — keep collecting)"
        elif p >= p_min and edge >= econ_floor:
            verdict = "SURVIVES (pre-registered bars met)"
        elif p >= p_min:
            verdict = f"POSITIVE-BUT-BELOW-FLOOR (edge {edge:+.4f} < {econ_floor:+.02f})"
        else:
            verdict = f"NOT DEMONSTRATED (P={p:.3f} < {p_min})"
        res.update({"resolved_mkts": n_mkts, "unresolved_ok_fills": unresolved,
                    "shadow_edge": edge, "shadow_edge_p": p,
                    "edge_verdict": verdict})
    return res


def report(res: dict, econ_floor: float) -> None:
    print("=" * 78)
    print("  SHADOW WATCHER READOUT — pre-registered analysis (MB_STATE §0)")
    print(f"  records={res['records']:,}  first-buys={res['first_buys']:,}")
    print(f"  gates on first-buys: {res['verdicts']}")
    print(f"  OK-rate={res['ok_rate']:.1%}  (pre-registered bar: >=50%)")
    print(f"  detect lag: p50={res['lag_p50']:.2f}s  p95={res['lag_p95']:.2f}s"
          f"  (REST baseline ~10s)")
    print(f"  copy tax on OK fills (ask - whale): mean {res['tax_mean']:+.4f}"
          f"  median {res['tax_p50']:+.4f}")
    if "shadow_edge" in res:
        print(f"  shadow edge (net of fee): {res['shadow_edge']:+.4f}"
              f"  P(>0)={res['shadow_edge_p']:.3f}"
              f"  on {res['resolved_mkts']} resolved mkts"
              f"  ({res['unresolved_ok_fills']} OK fills unresolved — counted, not guessed)")
        print(f"  VERDICT vs +{econ_floor:.02f} floor: {res['edge_verdict']}")
    else:
        print("  (no --gamma-cache: operability/latency/tax only — edge needs outcomes)")
    print("  per trader (first-buys):")
    for t, c in res["by_trader"].items():
        print(f"    {t[:14]}  {c}")
    print("=" * 78)


def _self_test() -> int:
    print("SELF-TEST — shadow analysis (synthetic, offline)\n")
    rng = random.Random(1)
    recs, outcomes = [], {}
    for i in range(200):
        tok = str(1000 + i)
        whale = 0.50
        fill = whale + 0.01
        won = rng.random() < 0.55  # true edge above fill ≈ +0.04 gross
        outcomes[tok] = 1 if won else 0
        recs.append({"trader": "0xgoat", "token_id": tok, "side": "BUY",
                     "whale_price": whale, "verdict": "OK", "shadow_fill": fill,
                     "detect_lag_s": 3.0 + rng.random(), "first_buy": True})
    recs.append({"trader": "0xgoat", "token_id": "1", "side": "BUY",
                 "whale_price": 0.5, "verdict": "PRICE_RAN_AWAY",
                 "shadow_fill": None, "detect_lag_s": 3.5, "first_buy": True})
    res = analyze(recs, outcomes, fee=0.0, econ_floor=0.02, p_min=0.95,
                  min_markets=30)
    ok = True
    ok &= res["first_buys"] == 201 and res["verdicts"]["OK"] == 200
    print(f"  [counts] first_buys/verdicts : {ok}")
    ok2 = abs(res["tax_mean"] - 0.01) < 1e-9
    print(f"  [tax] mean == +0.01 : {ok2}"); ok &= ok2
    ok3 = 2.9 < res["lag_p50"] < 4.1
    print(f"  [lag] p50 in range : {ok3}"); ok &= ok3
    ok4 = res["resolved_mkts"] == 200 and res["shadow_edge"] > 0
    print(f"  [edge] resolved+positive : {ok4}"); ok &= ok4
    # underpowered path
    res2 = analyze(recs[:10], outcomes, fee=0.0, econ_floor=0.02, p_min=0.95,
                   min_markets=30)
    ok5 = "UNDERPOWERED" in res2["edge_verdict"]
    print(f"  [underpowered] refuses verdict on 10 mkts : {ok5}"); ok &= ok5
    # unresolved tokens counted, never guessed
    res3 = analyze(recs, {}, fee=0.0, econ_floor=0.02, p_min=0.95, min_markets=30)
    ok6 = res3["resolved_mkts"] == 0 and res3["unresolved_ok_fills"] == 200
    print(f"  [unresolved] counted not guessed : {ok6}"); ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Shadow-log pre-registered readout")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--gamma-cache", default="", dest="gamma_cache",
                    help="gamma_resolutions.json for outcome labels (optional)")
    ap.add_argument("--fee", type=float, default=0.02)
    ap.add_argument("--econ-floor", type=float, default=0.02, dest="econ_floor")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    recs = load_records(args.log)
    outc = token_outcomes(args.gamma_cache) if args.gamma_cache and \
        os.path.exists(args.gamma_cache) else None
    report(analyze(recs, outc, args.fee, args.econ_floor, args.p_min,
                   args.min_markets), args.econ_floor)
    sys.exit(0)
