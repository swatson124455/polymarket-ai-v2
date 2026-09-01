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
