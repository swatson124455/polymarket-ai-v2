#!/usr/bin/env python3
"""MB BAYES HEAD START — build 3 of the 2026-09-06 mandate (operator
"build": empirical prior from the population study, adversarially
reviewed BEFORE deploy — this module is built to the REVIEW GATE and is
NOT wired into any cron or gate until that review + operator sign-off).

WHAT IT IS: an empirical-Bayes estimate of each trader's per-market mean
edge, shrunk toward the measured population, available from the trader's
FIRST resolved market — a head start for ranking and (post-review)
allocation while the anytime-valid machinery accrues. IT CHANGES NO
GATE: the ruled PASS remains e>=20 + the $100/wk floor (operator
rulings 2026-09-06); the LCB remains the money-column authority. Bayes
numbers are HYPOTHESIS-labeled (model-based; valid exactly insofar as
the normal-normal model and the fitted prior hold — unlike the LCB,
which is anytime-valid without a model).

MODEL (deliberately the simplest defensible one):
  trader true mean edge  theta_w ~ N(mu, tau^2)      (population prior)
  observed per-market edges of w: mean m_w over n_w markets, within-
  wallet variance s2_w  ->  m_w | theta_w ~ N(theta_w, s2_w / n_w)
  posterior: theta_w | data ~ N(post_mean, post_var) with
      w = tau^2 / (tau^2 + s2_w / n_w)
      post_mean = w * m_w + (1 - w) * mu
      post_var  = w * s2_w / n_w   [= 1/(1/tau^2 + n_w/s2_w)]

PRIOR FIT (method of moments over wallets with n >= min_n):
  mu    = mean of m_w            (unweighted — every wallet one vote,
                                  mirroring the canon per-market pooling)
  tau^2 = var(m_w) - mean(s2_w / n_w)   (between-wallet variance minus
          expected sampling noise; clipped at >= 0 and the clip DISCLOSED
          — tau^2 = 0 means the population is indistinguishable from
          noise and every posterior collapses to mu).

Edges are the CANON estimand (mb_backtest.synth_records +
mb_canon.per_market_edges — imported, never re-implemented). The prior
may legitimately be fit on the FULL window (a prior is prior knowledge);
the out-of-sample LCB judge is untouched by this module.

    PYTHONPATH=<mb_readout> python scripts/mb_bayes.py fit --rows ... \
        --haircut 0.0100 --out prior.json
    ... self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mb_backtest as mbt  # noqa: E402  (synth_records, loaders — build 1)
import mb_canon as mc  # noqa: E402  (canonical estimand)
import shadow_readout as sr  # noqa: E402


# ── pure core ────────────────────────────────────────────────────────────────
def wallet_moments(records: list[dict], outcomes: dict, frm: dict,
                   fee_map: dict) -> dict | None:
    """(n, mean, within-variance) of one wallet's per-market canon edges.
    None when < 2 resolved markets (variance undefined at n < 2)."""
    seq = mc.per_market_edges(records, outcomes, frm or {}, fee_map or {})
    edges = [e for _, _, e in seq]
    n = len(edges)
    if n < 2:
        return None
    mean = sum(edges) / n
    var = sum((e - mean) ** 2 for e in edges) / (n - 1)
    return {"n": n, "mean": mean, "var": var}


def fit_prior(moments: list[dict], min_n: int) -> dict:
    """Method-of-moments empirical prior over trader true mean edge.
    moments: [{n, mean, var}] per wallet. Wallets with n < min_n are
    excluded from the FIT (their posteriors still shrink toward it).
    Returns {mu, tau2, tau2_clipped, n_wallets, min_n}."""
    fit = [m for m in moments if m["n"] >= min_n]
    if len(fit) < 2:
        raise ValueError(f"only {len(fit)} wallets with n >= {min_n} — "
                         f"cannot fit a population prior")
    means = [m["mean"] for m in fit]
    mu = sum(means) / len(means)
    var_means = sum((x - mu) ** 2 for x in means) / (len(means) - 1)
    noise = sum(m["var"] / m["n"] for m in fit) / len(fit)
    tau2_raw = var_means - noise
    clipped = tau2_raw < 0.0
    return {"mu": mu, "tau2": max(tau2_raw, 0.0), "tau2_clipped": clipped,
            "tau2_raw": tau2_raw, "between_var": var_means,
            "avg_noise": noise, "n_wallets": len(fit), "min_n": min_n}


def posterior(prior: dict, m: dict) -> dict:
    """Normal-normal posterior for one wallet's {n, mean, var}.
    HYPOTHESIS-labeled by every consumer (model-based estimate)."""
    tau2 = prior["tau2"]
    se2 = m["var"] / m["n"] if m["n"] > 0 else float("inf")
    if tau2 == 0.0 or se2 == float("inf"):
        return {"post_mean": prior["mu"], "post_var": tau2,
                "shrink_w": 0.0}
    if se2 == 0.0:
        # zero within-wallet variance (e.g. all edges identical): the
        # normal model degenerates; report the data mean, no shrinkage,
        # posterior variance 0 — flagged for the review, not hidden
        return {"post_mean": m["mean"], "post_var": 0.0, "shrink_w": 1.0,
                "degenerate_zero_var": True}
    w = tau2 / (tau2 + se2)
    return {"post_mean": w * m["mean"] + (1.0 - w) * prior["mu"],
            "post_var": w * se2, "shrink_w": w}


# ── I/O shell ────────────────────────────────────────────────────────────────
def cmd_fit(args) -> int:
    rows = mbt._load_jsonl(args.rows)
    assert rows, "EMPTY rows file - ABORT"
    rows.sort(key=lambda r: float(r.get("t") or 0))
    by_w: dict[str, list[dict]] = {}
    for r in rows:
        by_w.setdefault(str(r.get("w")), []).append(r)
    frm = json.load(open(args.fee_rate_map)) \
        if os.path.exists(args.fee_rate_map) else {}
    fee_map = json.load(open(args.fee_map)) \
        if os.path.exists(args.fee_map) else {}
    per_wallet: dict[str, list[dict]] = {}
    for w, rws in by_w.items():
        recs, _g = mbt.synth_records(rws, w, args.haircut)
        if recs:
            per_wallet[w] = recs
    all_tokens = sorted({str(r["token_id"]) for recs in per_wallet.values()
                         for r in recs})
    outcomes = sr.supplement_outcomes(args.resolutions, all_tokens)
    if os.environ.get("DATABASE_URL"):
        import asyncio
        db_out = asyncio.run(sr.fresh_outcomes(all_tokens))
        outcomes = sr.merge_outcomes(db_out, outcomes)
    moments = {}
    for w, recs in per_wallet.items():
        m = wallet_moments(recs, outcomes, frm, fee_map)
        if m is not None:
            moments[w] = m
    prior = fit_prior(list(moments.values()), args.min_n)
    print(f"[bayes] prior fit over {prior['n_wallets']} wallets "
          f"(n >= {args.min_n}; {len(moments)} wallets had >= 2 resolved): "
          f"mu={prior['mu']:+.4f} tau={prior['tau2'] ** 0.5:.4f} "
          f"(tau2_raw={prior['tau2_raw']:+.6f}"
          f"{', CLIPPED to 0' if prior['tau2_clipped'] else ''}; "
          f"between-wallet var {prior['between_var']:.6f}, avg sampling "
          f"noise {prior['avg_noise']:.6f})")
    post = {w: {**m, **posterior(prior, m)} for w, m in moments.items()}
    with open(args.out, "w") as f:
        json.dump({"prior": prior, "haircut": args.haircut,
                   "posteriors": post, "label": "HYPOTHESIS"}, f)
    top = sorted(post.items(), key=lambda kv: -kv[1]["post_mean"])[:args.top]
    print(f"[bayes] top posteriors (HYPOTHESIS — model-based; the LCB "
          f"column and e>=20 gate are unchanged by this):")
    for w, p in top:
        print(f"  {w[:12]}..  post_mean={p['post_mean']:+.4f} "
              f"shrink_w={p['shrink_w']:.2f} n={p['n']} raw={p['mean']:+.4f}")
    print(f"[bayes] wrote prior + {len(post)} posteriors -> {args.out}")
    print("[bayes] NOT DEPLOYED to any cron/gate — adversarial review + "
          "operator sign-off required first (mandate order)")
    return 0


def _self_test() -> int:
    print("SELF-TEST — mb_bayes (offline)\n")
    ok = True
    # [moments] canon edges via the shared estimand; n<2 -> None
    recs = [{"trader": "w", "token_id": "a", "detect_ts": 1.0,
             "first_buy": True, "verdict": "OK", "shadow_fill": 0.4},
            {"trader": "w", "token_id": "b", "detect_ts": 2.0,
             "first_buy": True, "verdict": "OK", "shadow_fill": 0.4}]
    m = wallet_moments(recs, {"a": 1, "b": 0}, {}, {})
    # edges (flat 2% fallback fee 0.008): 1-0.408=0.592, -0.408
    ok1 = (m is not None and m["n"] == 2
           and abs(m["mean"] - 0.092) < 1e-12
           and abs(m["var"] - 0.5) < 1e-9
           and wallet_moments(recs[:1], {"a": 1}, {}, {}) is None)
    print(f"  [moments] canon edges, exact mean/var, n<2 -> None : {ok1}")
    ok &= ok1
    # [fit] known population: recover mu; tau2 = between - noise
    moms = [{"n": 100, "mean": 0.10, "var": 1.0},
            {"n": 100, "mean": -0.10, "var": 1.0},
            {"n": 100, "mean": 0.10, "var": 1.0},
            {"n": 100, "mean": -0.10, "var": 1.0}]
    pr = fit_prior(moms, 10)
    between = sum((x - 0.0) ** 2 for x in (0.1, -0.1, 0.1, -0.1)) / 3
    ok2 = (abs(pr["mu"]) < 1e-12
           and abs(pr["tau2"] - (between - 0.01)) < 1e-12
           and pr["tau2_clipped"] is False)
    print(f"  [fit] mu exact; tau2 = between-var - sampling noise : {ok2}")
    ok &= ok2
    # [fit] noise > spread -> clipped to 0, disclosed
    pr0 = fit_prior([{"n": 2, "mean": 0.01, "var": 1.0},
                     {"n": 2, "mean": -0.01, "var": 1.0}], 2)
    ok3 = pr0["tau2"] == 0.0 and pr0["tau2_clipped"] is True
    try:
        fit_prior([{"n": 100, "mean": 0.1, "var": 1.0}], 10)
        ok3 = False
    except ValueError:
        pass
    print(f"  [fit] indistinct population clips to 0 + flagged; "
          f"<2 wallets raises : {ok3}")
    ok &= ok3
    # [posterior] shrinkage direction + limits
    p = posterior({"mu": 0.0, "tau2": 0.01}, {"n": 4, "mean": 0.2,
                                              "var": 0.04})
    ok4 = (0.0 < p["post_mean"] < 0.2 and 0.0 < p["shrink_w"] < 1.0)
    p_hi = posterior({"mu": 0.0, "tau2": 0.01}, {"n": 10000, "mean": 0.2,
                                                 "var": 0.04})
    p_lo = posterior({"mu": 0.0, "tau2": 0.0}, {"n": 4, "mean": 0.2,
                                                "var": 0.04})
    ok4 = (ok4 and abs(p_hi["post_mean"] - 0.2) < 1e-3
           and p_lo["post_mean"] == 0.0 and p_lo["shrink_w"] == 0.0)
    print(f"  [post] shrinks toward mu; n->inf recovers data; tau2=0 "
          f"collapses to mu : {ok4}")
    ok &= ok4
    p_deg = posterior({"mu": 0.0, "tau2": 0.01}, {"n": 5, "mean": 0.3,
                                                  "var": 0.0})
    ok5 = p_deg.get("degenerate_zero_var") is True
    print(f"  [post] zero within-variance flagged degenerate : {ok5}")
    ok &= ok5
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    ok6 = all(f"def {n}(" not in src for n in
              ("per_market_edges", "e_value", "lcb_edge", "canon_fee",
               "synth_records"))
    print(f"  [canon] no re-implementation of shared primitives : {ok6}")
    ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="empirical-Bayes head start (build 3 — review gate)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fit", help="fit prior + posteriors from sweep rows")
    p.add_argument("--rows", required=True)
    p.add_argument("--haircut", type=float, required=True)
    p.add_argument("--min-n", type=int, default=30,
                   help="wallets entering the PRIOR FIT (posteriors "
                        "computed for all; default = the grader's N_BAR)")
    p.add_argument("--resolutions", default=os.path.join(
        mbt.CACHE_DIR, "gamma_resolutions.json"))
    p.add_argument("--fee-rate-map", dest="fee_rate_map",
                   default=os.path.join(mbt.CACHE_DIR, "fee_rate_map.json"))
    p.add_argument("--fee-map", dest="fee_map",
                   default=os.path.join(mbt.CACHE_DIR, "fee_map.json"))
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--out", required=True)
    sub.add_parser("self-test")
    args = ap.parse_args()
    if args.cmd == "self-test":
        raise SystemExit(_self_test())
    raise SystemExit(cmd_fit(args))
