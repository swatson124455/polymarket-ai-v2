#!/usr/bin/env python3
"""MB MEASUREMENT CANON - the single authoritative definition of "edge".

Operator-ordered 2026-08-25 ("create measuring stick with canon data").
This module is the ONE place the lane's estimand lives. Every future
instrument imports these functions; re-implementation is a defect
(docs/MEASUREMENT_CANON.md, NAMING LAW).

The canonical estimand is the operator-ratified frozen estimand of
docs/BAND_PREREGISTRATION.md ("Estimand (canonical, frozen)"):

    per-market mean edge of OK first-buys, edge atom = outcome - fill - fee,
    markets keyed by token_id, ordered by the market's first detect_ts,
    pooled = mean of per-market means (each market weighs equally).

Fee precedence (exactly the ratified band_tracker chain, in force for all
post-2026-08-19 registrations per analyze_shadow's fee rule):
    1. token in fee_rate_map  -> fee = rate * fill * (1 - fill)   [venue formula]
    2. fee_map says zero-fee  -> fee = 0.0                        [measured exemption]
    3. otherwise              -> fee = 0.02 * fill                [flat 2%, DISCLOSED]

Label precedence: DB outcome wins, supplement fills holes - but a CONFLICT
(both present, different outcome) is never silently resolved: conflicted
tokens are EXCLUDED from the merged map and returned for loud reporting
(2026-08-25 canon; the old silent DB-wins merge is retired for new code).

Pure functions only - no I/O, no network, no side effects. Offline-testable.
Changes to this file require an operator-signed amendment with an epoch
stamp; running pre-registered tests NEVER retroactively change scoring.
"""
from __future__ import annotations

CANON_EPOCH = "2026-08-25"
FLAT_FEE_FRAC = 0.02


def canon_fee(token_id: str, fill: float, fee_rate_map: dict,
              fee_map: dict) -> tuple[float, str]:
    """(fee, source) for one fill. source is one of
    'venue_formula' | 'zero_fee_exempt' | 'flat_2pct_fallback'."""
    tok = str(token_id)
    rate = fee_rate_map.get(tok) if fee_rate_map else None
    if rate is not None:
        return float(rate) * fill * (1.0 - fill), "venue_formula"
    if fee_map and fee_map.get(tok) == 0:
        return 0.0, "zero_fee_exempt"
    return FLAT_FEE_FRAC * fill, "flat_2pct_fallback"


def edge_atom(outcome: float, fill: float, fee: float) -> float:
    """The canonical atom: what one copied first-buy realizes per share."""
    return outcome - fill - fee


def per_market_edges(records: list[dict], outcomes: dict,
                     fee_rate_map: dict, fee_map: dict,
                     epoch: float = 0.0,
                     lo: float | None = None,
                     hi: float | None = None) -> list[tuple[float, str, float]]:
    """[(first_detect_ts, token_id, per-market mean edge)] over resolved
    markets, canonical filters: forward window (detect_ts >= epoch),
    first_buy, verdict OK, optional fill band [lo, hi). Sorted by the
    market's first detect_ts (the pre-registered ordering)."""
    per_tok: dict[str, list[float]] = {}
    first_ts: dict[str, float] = {}
    for r in records:
        if float(r.get("detect_ts") or 0) < epoch:
            continue
        if not (r.get("first_buy") and r.get("verdict") == "OK"):
            continue
        f = r.get("shadow_fill")
        if not isinstance(f, (int, float)):
            continue
        if lo is not None and not (lo <= f < hi):
            continue
        tok = str(r.get("token_id"))
        o = outcomes.get(tok)
        if o is None:
            continue
        fee, _src = canon_fee(tok, f, fee_rate_map, fee_map)
        per_tok.setdefault(tok, []).append(edge_atom(o, f, fee))
        ts = float(r.get("detect_ts") or 0)
        first_ts[tok] = min(first_ts.get(tok, ts), ts)
    return sorted((first_ts[t], t, sum(v) / len(v))
                  for t, v in per_tok.items())


def pooled_edge(market_edges: list[tuple[float, str, float]]) -> float | None:
    """THE canonical pooled edge: mean of per-market means. None when empty -
    a caller printing a number for an empty set is the lane's documented
    worst failure mode; None forces the caller to say so."""
    if not market_edges:
        return None
    return sum(e for _, _, e in market_edges) / len(market_edges)


# ── AMENDMENT 2026-09-06 (operator hardcode, verbatim: "we also need to
# base off roi as well and net winnings hard code"; "score per market can
# be multiple wagers if they ladder that is flawed") ─────────────────────
# THE BASIS for trader measurement is ROI *and* NET WINNINGS, LADDER-AWARE:
#   * a WAGER = one buy (tx-merged fills), INCLUDING ladder adds — the
#     first-buy-only per-market estimand above remains as an INPUT/
#     diagnostic but is superseded as a gating basis;
#   * ROI per wager = (outcome − fill − fee) / fill — dollars returned
#     per dollar staked (per-share edge is NOT ROI: an $0.08 share paying
#     $1 returns +11.5x/dollar, which edge scores as +0.92/share);
#   * NET WINNINGS = ROI × stake, at the ruled $100 reference per wager
#     for hypothetical bases, sizer stake for money.
ROI_Y_MIN = -1.10
# RAW-ATOM sanity floor: worst possible wager ROI = -(1 + fee/fill);
# venue fee = rate*fill*(1-fill) with rate <= 0.07 => fee/fill <= 0.07
# => ROI >= -1.07; flat-2% fallback => ROI >= -1.02. A raw atom below
# -1.10 is data corruption — wager_rois raises loudly (never used as an
# e-process bound; that conflation TRUNCATED LCBs at min_roi+1.10 in the
# 2026-09-06 first ROI run — the exactly-+0.080 rows).
LAMBDAS = (0.05, 0.1, 0.2, 0.4, 0.6, 0.8)
# = band_tracker.LAMBDAS verbatim (pinned equal by test so the mixture
# grids can never drift apart).
MIX_POSITIVITY_FLOOR = -1.0 / max(LAMBDAS) + 1e-4
# The mixture's TRUE support bound: every wealth factor 1 + lam*y must
# stay positive, so y > -1/max(LAMBDAS) = -1.25. This (not the raw-atom
# floor) is what limits how far the LCB bisection may shift atoms down;
# the e-process stays a valid supermartingale under H0 for any shift
# keeping all observed factors positive.


def wager_rois(records: list[dict], outcomes: dict, fee_rate_map: dict,
               fee_map: dict, epoch: float = 0.0
               ) -> list[tuple[float, str, float]]:
    """[(detect_ts, token_id, roi)] over resolved WAGERS in the forward
    window — ladder-aware: EVERY OK buy record is one wager (repeats and
    adds included; first_buy is NOT required). Ordered by detect_ts."""
    out = []
    for r in records:
        ts = float(r.get("detect_ts") or 0)
        if ts < epoch or r.get("verdict") != "OK":
            continue
        f = r.get("shadow_fill")
        if not isinstance(f, (int, float)) or not (0.0 < f):
            continue
        tok = str(r.get("token_id"))
        o = outcomes.get(tok)
        if o is None:
            continue
        fee, _src = canon_fee(tok, f, fee_rate_map, fee_map)
        roi = (o - f - fee) / f
        if roi < ROI_Y_MIN:
            raise ValueError(
                f"wager ROI {roi:.4f} below the physical floor "
                f"{ROI_Y_MIN} (token {tok}) — data corruption, refusing")
        out.append((ts, tok, roi))
    out.sort()
    return out


def roi_e_value(ys: list, m: float) -> float | None:
    """E-process for H0: true mean ROI <= m, with the lambda-subgrid
    valid at shift m (operator "ceiling review go", 2026-09-06). A bet
    lambda is admissible at m iff its wealth factor stays positive for
    ANY physically possible atom: 1 + lam*(ROI_Y_MIN - m) > 0, i.e.
    lam < 1/(m - ROI_Y_MIN). Selection uses the PHYSICAL floor, never
    the observed minimum — observed-min selection would be choosing bets
    with hindsight and void the anytime validity. None when no bet is
    admissible (m beyond 1/min(LAMBDAS) - 1.10 ~= 18.9: untestable)."""
    valid = [lam for lam in LAMBDAS if 1.0 + lam * (ROI_Y_MIN - m) > 0.0]
    if not valid:
        return None
    wealth = [1.0] * len(valid)
    for y in ys:
        if y < ROI_Y_MIN - 1e-9:
            raise ValueError(f"atom {y} below physical ROI floor")
        for j, lam in enumerate(valid):
            wealth[j] *= (1.0 + lam * (y - m))
    return sum(wealth) / len(valid)


def roi_lcb(rois: list, e_bar: float = 20.0, tol: float = 1e-6):
    """Anytime-valid LCB on mean ROI by inverting roi_e_value: LCB =
    sup{m : e(m) >= e_bar}. Validity needs only the test AT the true
    mean (level 1/e_bar by Ville) — no multiplicity across m. The
    subgrid changes at boundaries m_k = 1/lam_k - |ROI_Y_MIN|; WITHIN an
    interval every factor is strictly decreasing in m, so e is monotone
    there and bisection is sound; across a boundary the mixture loses
    its largest bet and e can jump, so the search walks intervals from
    the bottom and bisects inside the highest interval whose lower edge
    still rejects. None when even m=-1 is not rejected."""
    if not rois:
        return None
    if e_bar <= 1.0:
        raise ValueError("e_bar must exceed 1 (Ville)")

    def rejects(m: float) -> bool:
        ev = roi_e_value(rois, m)
        return ev is not None and ev >= e_bar

    if not rejects(-1.0):
        return None
    # subgrid-change boundaries above -1, ascending; top = untestable edge
    bounds = sorted(1.0 / lam - abs(ROI_Y_MIN) for lam in LAMBDAS)
    edges = [-1.0] + [b for b in bounds if b > -1.0]
    lo = -1.0
    hi = None
    for i, a in enumerate(edges):
        if not rejects(a + (tol if a > -1.0 else 0.0)):
            hi = a
            break
        lo = a
        hi = edges[i + 1] if i + 1 < len(edges) else edges[-1]
    if hi is None or hi <= lo:
        return lo
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if rejects(mid):
            lo = mid
        else:
            hi = mid
    return lo


def market_position_rois(records: list[dict], outcomes: dict,
                         fee_rate_map: dict, fee_map: dict,
                         epoch: float = 0.0
                         ) -> list[tuple[float, str, float, int]]:
    """[(first_ts, token_id, position_roi, n_wagers)] — ONE atom per
    MARKET (correlated-atom fix, operator "fix go" 2026-09-06):
    same-market ladder wagers share one outcome, so multiplying them as
    independent e-process bets inflated evidence (measured null false-
    pass 37%/65%/74% at 5/20/44 wagers per market vs the 5% guarantee).
    position_roi = the equal-stake MEAN of the market's wager ROIs —
    every ladder fill still prices the position (the ladder ruling
    honored), but each market resolution contributes exactly one
    e-process atom (independence restored; measured 1.6% at one atom).
    wager_rois above remains canon for MONEY bookkeeping (dollars are
    real per wager); THIS is canon for EVIDENCE (e-process / LCB)."""
    seq = wager_rois(records, outcomes, fee_rate_map, fee_map, epoch=epoch)
    per_tok: dict[str, list[float]] = {}
    first_ts: dict[str, float] = {}
    for ts, tok, roi in seq:
        per_tok.setdefault(tok, []).append(roi)
        first_ts[tok] = min(first_ts.get(tok, ts), ts)
    return sorted((first_ts[t], t, sum(v) / len(v), len(v))
                  for t, v in per_tok.items())


def mixture_e_value(ys: list, y_min: float = MIX_POSITIVITY_FLOOR) -> float:
    """Uniform-mixture betting e-process for H0: mean <= 0 — the same
    mixture as band_tracker.e_value (grid pinned equal by test),
    generalized to the wealth-positivity support bound so shifted ROI
    atoms (LCB bisection) are accepted all the way to the mathematical
    limit; raw-atom sanity lives in wager_rois, not here."""
    assert all(y >= y_min - 1e-9 for y in ys), "atom below support bound"
    wealth = [1.0] * len(LAMBDAS)
    for y in ys:
        for j, lam in enumerate(LAMBDAS):
            wealth[j] *= (1.0 + lam * y)
    return sum(wealth) / len(wealth)


def merge_labels(db: dict, supplement: dict) -> tuple[dict, dict]:
    """(merged, conflicts). DB wins, supplement fills holes; a token present
    in BOTH with DIFFERENT outcomes goes to `conflicts` {token: (db, supp)}
    and is EXCLUDED from merged - conflicts must be reported loudly, never
    silently resolved."""
    merged: dict = {}
    conflicts: dict = {}
    for tok, o in (supplement or {}).items():
        merged[str(tok)] = o
    for tok, o in (db or {}).items():
        tok = str(tok)
        if tok in merged and merged[tok] != o:
            conflicts[tok] = (o, merged[tok])
            del merged[tok]
            continue
        merged[tok] = o
    return merged, conflicts
