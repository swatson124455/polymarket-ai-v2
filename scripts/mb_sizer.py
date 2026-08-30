#!/usr/bin/env python3
"""MB SIZER - pre-registered position-sizing rule (operator "build the sizer"
2026-08-30; design amended by the same-day adversarial review - see
docs/MB_SIZER_DESIGN.md for the review findings D1-D3/E4-E5).

PURE MODULE, no I/O, no defaults for risk parameters. Sizes ONE first-buy
copy of ONE trader. Consumes canon primitives (band_tracker.e_value injected
by the caller) - never re-implements them (MEASUREMENT_CANON rule).

THE RULE (every constant is cited or operator-supplied):
  1. Evidence -> edge (D1/D2): the trader's anytime-valid LOWER confidence
     bound (LCB) on canonical per-market mean edge, obtained by INVERTING
     the grader's own betting e-process at the ruled bar 1/alpha = 20
     (cohort5_qualification.C1_E_REJECT - no new evidential constant).
     Duality: LCB = sup{m : e-process rejects H0 "mean <= m" at e_bar},
     computed by bisection on e_value_fn([y - m]); valid because each
     betting factor (1 + lam*(y-m)) is positive and strictly decreasing in
     m, so the mixture e-value is strictly decreasing in m. A trader that
     has not rejected the null has LCB <= 0 -> stake exactly $0. There is
     no shrinkage map to tune.
  2. Edge -> fraction (E4, price-aware): edge atoms are net-of-fee, so for
     a share costing fill+fee paying 1 or 0, exact binary Kelly is
         k_full = LCB / (1 - fill - fee)        (capped at 1.0)
     (derivation: k* = (p - c)/(1 - c) with c = fill + fee, p = c + LCB).
  3. Fraction -> dollars (D3, concurrency): Kelly assumes sequential bets;
     ours resolve concurrently. Bankroll is pre-divided by a MEASURED
     concurrency budget (caller supplies it from measurement; no default):
         raw_stake = kelly_mult * k_full * bankroll / concurrency
     kelly_mult and bankroll are OPERATOR decisions (no defaults).
  4. Rails (E5), strictly DOWNWARD only:
         stake = min(raw_stake, book_depth_usd, per_bet_cap)
     per_bet_cap defaults to the $300 canon per-bet ceiling (BotBankroll
     max_bet_usd for Mirror - established canon, not invented here).
     If the railed stake is below min_viable the answer is $0.00 - NEVER
     round a small stake up (the legacy dust-clamp defect, hygiene-verified
     2026-08: $9 intents clamped up to $25, nullifying every dampener).

Nothing here writes state or trades; output is a recommendation dict with
full provenance of every intermediate.
"""
from __future__ import annotations

E_BAR_RULED = 20.0     # = cohort5_qualification.C1_E_REJECT (ruled bar)
PER_BET_CAP_CANON = 300.0  # BotBankrollManager max_bet_usd (Mirror), canon


def lcb_edge(edges: list, e_value_fn, y_min: float,
             e_bar: float = E_BAR_RULED, tol: float = 1e-6):
    """Anytime-valid lower confidence bound on the mean edge by e-process
    inversion. Returns None when there is no data. May be negative (edge
    not demonstrated). Search domain is capped so shifted edges never
    violate the e-process's y >= y_min support bound (shifting DOWN by m
    moves edges toward the bound; m_max keeps min(edges)-m >= y_min)."""
    if not edges:
        return None
    if e_bar <= 1.0:
        raise ValueError("e_bar must exceed 1 (Ville)")
    m_max = min(min(edges) - y_min, 1.0)
    m_lo = -1.0  # shifting UP is always inside support; -1 floors the report

    def rejects(m: float) -> bool:
        return e_value_fn([y - m for y in edges]) >= e_bar

    if not rejects(m_lo):
        # even "mean <= -1" not rejected: no informative bound
        return None
    if m_max <= m_lo:
        return None
    if rejects(m_max):
        return m_max
    lo, hi = m_lo, m_max            # rejects(lo)=True, rejects(hi)=False
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if rejects(mid):
            lo = mid
        else:
            hi = mid
    return lo                        # conservative side of the bracket


def recommend_stake(edges: list, fill: float, fee_per_share: float, *,
                    bankroll: float, kelly_mult: float, concurrency: int,
                    book_depth_usd: float, min_viable: float,
                    e_value_fn, y_min: float,
                    per_bet_cap: float = PER_BET_CAP_CANON,
                    e_bar: float = E_BAR_RULED) -> dict:
    """Recommend a USD stake for one first-buy copy. All risk parameters
    are keyword-required with NO defaults (bankroll, kelly_mult,
    concurrency, book_depth_usd, min_viable) so no constant can hide here.
    Returns a dict with the stake and every intermediate + reasons."""
    if not (0.0 < fill < 1.0):
        raise ValueError(f"fill out of (0,1): {fill}")
    if fee_per_share < 0.0:
        raise ValueError("negative fee")
    if bankroll <= 0.0:
        raise ValueError("bankroll must be > 0")
    if not (0.0 < kelly_mult <= 1.0):
        raise ValueError("kelly_mult must be in (0, 1]")
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1 (measured, not guessed)")
    if book_depth_usd < 0.0:
        raise ValueError("negative book depth")
    if min_viable < 0.0:
        raise ValueError("negative min_viable")

    lcb = lcb_edge(edges, e_value_fn, y_min, e_bar=e_bar)
    return recommend_stake_from_lcb(
        lcb, fill, fee_per_share, bankroll=bankroll, kelly_mult=kelly_mult,
        concurrency=concurrency, book_depth_usd=book_depth_usd,
        min_viable=min_viable, per_bet_cap=per_bet_cap, e_bar=e_bar)


def recommend_stake_from_lcb(lcb, fill: float, fee_per_share: float, *,
                             bankroll: float, kelly_mult: float,
                             concurrency: int, book_depth_usd: float,
                             min_viable: float,
                             per_bet_cap: float = PER_BET_CAP_CANON,
                             e_bar: float = E_BAR_RULED) -> dict:
    """Same rule, LCB supplied by a caller that already computed it with
    lcb_edge (e.g. the funnel) - ONE implementation, two entry points."""
    if not (0.0 < fill < 1.0):
        raise ValueError(f"fill out of (0,1): {fill}")
    if fee_per_share < 0.0:
        raise ValueError("negative fee")
    if bankroll <= 0.0:
        raise ValueError("bankroll must be > 0")
    if not (0.0 < kelly_mult <= 1.0):
        raise ValueError("kelly_mult must be in (0, 1]")
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1 (measured, not guessed)")
    if book_depth_usd < 0.0:
        raise ValueError("negative book depth")
    if min_viable < 0.0:
        raise ValueError("negative min_viable")
    out = {"stake": 0.0, "lcb": lcb, "k_full": 0.0, "raw_stake": 0.0,
           "caps_applied": [], "reason": ""}
    if lcb is None:
        out["reason"] = "no informative LCB (insufficient data)"
        return out
    if lcb <= 0.0:
        out["reason"] = (f"edge not demonstrated (LCB={lcb:+.4f} <= 0 at "
                         f"e>={e_bar:.0f}) -> $0")
        return out
    denom = 1.0 - fill - fee_per_share
    if denom <= 0.0:
        out["reason"] = "fill+fee >= 1: no upside to size"
        return out
    k_full = min(1.0, lcb / denom)
    out["k_full"] = k_full
    raw = kelly_mult * k_full * bankroll / float(concurrency)
    out["raw_stake"] = raw
    stake = raw
    if stake > book_depth_usd:
        stake = book_depth_usd
        out["caps_applied"].append("book_depth")
    if stake > per_bet_cap:
        stake = per_bet_cap
        out["caps_applied"].append("per_bet_cap")
    if stake < min_viable:
        # NEVER clamp up (legacy dust-clamp defect): below-minimum -> $0
        out["stake"] = 0.0
        out["caps_applied"].append("below_min_viable_to_zero")
        out["reason"] = (f"railed stake ${stake:.2f} < min_viable "
                         f"${min_viable:.2f} -> $0 (never clamp up)")
        return out
    out["stake"] = stake
    out["reason"] = (f"LCB {lcb:+.4f} @ fill {fill:.3f} -> kelly "
                     f"{k_full:.4f} x mult {kelly_mult} x bankroll "
                     f"${bankroll:.0f} / conc {concurrency}")
    return out
