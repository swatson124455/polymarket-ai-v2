#!/usr/bin/env python3
"""DIAGNOSTIC: does the Polymarket price CONVERGE toward the sharp line?

The sharp-anchor thesis says Pinnacle leads and Polymarket follows. If that is
true, a PM-vs-sharp gap observed HOURS before a match should shrink by match
start — and it should shrink because the PM price moved toward the sharp line
(not because the sharp line drifted to meet PM). Buying the gap then earns
closing-line value (CLV) mechanically, which is a far lower-variance validation
than settled outcomes (line movement has no coin-flip noise).

For every match with >=2 pre-start PM-priced snapshots this script measures:

  g0 = sharp_yes - pm_yes at the FIRST priced snapshot (the entry gap)
  g1 = the same at the LAST pre-start priced snapshot (the closing gap)

and decomposes the sign-adjusted closure  sign(g0)*(g0 - g1)  into
  pm_moved   = sign(g0)*(q1 - q0)   -> PM price moved toward the sharp line
                                       (this term IS price-CLV for the gap side)
  sharp_moved= sign(g0)*(s0 - s1)   -> the sharp line came down to meet PM

This is NOT a backtest (no outcomes, no P&L). Placeholder prices (~0.50 empty
book) are skipped; a match whose priced snapshots disagree on condition_id is
dropped as ambiguous (correct-or-absent).

    python -m esports_v2.scripts.pm_convergence --snapshots <jsonl>
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, Iterable, List, Optional

from esports_v2.model.closing_line import (
    _coerce_odds,
    _coerce_price,
    iter_snapshots_jsonl,
    parse_ts,
)
from esports_v2.model.sharp_eval import no_vig_prob_a
from esports_v2.model.team_match import same_team

MIN_GAP_DEFAULT = 0.01
PLACEHOLDER_TOL = 0.0051      # same empty-book signature as edge_distribution
GAP_BUCKETS = ((0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 1.0))


def _orient(yes_outcome: Optional[str], home: str, away: str) -> Optional[bool]:
    """True if the PM YES outcome is the home/'A' side, False if away, else None
    (ambiguous/unmatched -> never a guess; same rule as edge_distribution)."""
    if not yes_outcome:
        return None
    is_home = same_team(yes_outcome, home, None)
    is_away = same_team(yes_outcome, away, None)
    if is_home == is_away:
        return None
    return bool(is_home)


def convergence_rows(
    snapshots: Iterable[dict], *, method: str = "simple"
) -> Dict[str, List[dict]]:
    """Reduce snapshots to one convergence row per qualifying match.

    Returns ``{"rows": [...], "dropped": [...]}`` where each row carries the
    entry/closing gaps and the closure decomposition, and ``dropped`` counts
    why matches fell out (fewer than 2 priced pre-start snaps, ambiguous
    condition_id, unresolvable orientation, unparseable starts).
    """
    grouped: Dict[str, List[dict]] = {}
    for snap in snapshots:
        if not isinstance(snap, dict) or not snap.get("match_key"):
            continue
        cap = parse_ts(snap.get("captured_at"))
        oa = _coerce_odds(snap.get("odds_a"))
        ob = _coerce_odds(snap.get("odds_b"))
        if cap is None or oa is None or ob is None:
            continue
        grouped.setdefault(str(snap["match_key"]), []).append(
            {"cap_dt": cap, "odds_a": oa, "odds_b": ob, "raw": snap}
        )

    rows: List[dict] = []
    dropped = {"no_start": 0, "lt2_priced": 0, "ambiguous_market": 0,
               "orientation": 0, "placeholder_snaps": 0}
    for key, obs in grouped.items():
        # Latest-seen starts: same schedule-drift rule as the closing reducer.
        latest = max(obs, key=lambda o: o["cap_dt"])
        starts_dt = parse_ts(latest["raw"].get("starts"))
        if starts_dt is None:
            dropped["no_start"] += 1
            continue
        pre = sorted((o for o in obs if o["cap_dt"] <= starts_dt),
                     key=lambda o: o["cap_dt"])
        priced = []
        for o in pre:
            q = _coerce_price(o["raw"].get("market_price"))
            if q is None:
                continue
            if abs(q - 0.5) < PLACEHOLDER_TOL:
                dropped["placeholder_snaps"] += 1
                continue
            priced.append((o, q))
        if len(priced) < 2:
            dropped["lt2_priced"] += 1
            continue
        cids = {str(o["raw"].get("condition_id") or "") for o, _ in priced}
        if len(cids) != 1:
            dropped["ambiguous_market"] += 1
            continue
        craw = priced[-1][0]["raw"]
        yes_is_home = _orient(craw.get("yes_outcome"),
                              str(craw.get("home") or "").strip(),
                              str(craw.get("away") or "").strip())
        if yes_is_home is None:
            dropped["orientation"] += 1
            continue

        def sharp_yes(o) -> Optional[float]:
            pa = no_vig_prob_a(o["odds_a"], o["odds_b"], method)
            if pa is None:
                return None
            return pa if yes_is_home else (1.0 - pa)

        (o0, q0), (o1, q1) = priced[0], priced[-1]
        s0, s1 = sharp_yes(o0), sharp_yes(o1)
        if s0 is None or s1 is None:
            dropped["orientation"] += 1
            continue
        g0, g1 = s0 - q0, s1 - q1
        sign = 1.0 if g0 >= 0 else -1.0
        rows.append({
            "match_key": key,
            "home": str(craw.get("home") or "").strip(),
            "away": str(craw.get("away") or "").strip(),
            "starts": str(latest["raw"].get("starts") or "")[:16],
            "n_priced": len(priced),
            "hours_span": (priced[-1][0]["cap_dt"] - priced[0][0]["cap_dt"])
                          .total_seconds() / 3600.0,
            "hours_to_start": (starts_dt - priced[0][0]["cap_dt"])
                              .total_seconds() / 3600.0,
            "s0": s0, "q0": q0, "s1": s1, "q1": q1,
            "g0": g0, "g1": g1, "abs_g0": abs(g0), "abs_g1": abs(g1),
            "converged": abs(g1) < abs(g0),
            "closure": sign * (g0 - g1),          # >0 = gap moved toward zero
            "pm_moved": sign * (q1 - q0),         # >0 = PM toward sharp (=CLV)
            "sharp_moved": sign * (s0 - s1),      # >0 = sharp toward PM
        })
    return {"rows": rows, "dropped": dropped}


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def report(result: Dict, *, min_gap: float = MIN_GAP_DEFAULT,
           method: str = "simple") -> str:
    rows, dropped = result["rows"], result["dropped"]
    out = [f"PM-vs-sharp convergence (DIAGNOSTIC — no outcomes, no P&L; "
           f"de-vig={method})"]
    out.append(f"  matches with >=2 priced pre-start snapshots: {len(rows)}")
    out.append("  dropped: " + "  ".join(f"{k}={v}" for k, v in dropped.items()))
    if not rows:
        out.append("  (nothing to measure yet)")
        return "\n".join(out)
    med_span = sorted(r["hours_span"] for r in rows)[len(rows) // 2]
    med_n = sorted(r["n_priced"] for r in rows)[len(rows) // 2]
    out.append(f"  median priced-snapshot span: {med_span:.1f}h  "
               f"median priced snaps/match: {med_n}")
    uni = [r for r in rows if r["abs_g0"] >= min_gap]
    out.append(f"  [entry gap |g0| >= {min_gap:.2f}]  n={len(uni)}")
    if uni:
        conv = sum(1 for r in uni if r["converged"])
        out.append(f"    converged (|g1|<|g0|): {conv}/{len(uni)} "
                   f"({conv / len(uni):.0%})")
        out.append(f"    mean |gap| entry->close: {_mean([r['abs_g0'] for r in uni]):.3f}"
                   f" -> {_mean([r['abs_g1'] for r in uni]):.3f}")
        pm_m = _mean([r["pm_moved"] for r in uni])
        sh_m = _mean([r["sharp_moved"] for r in uni])
        out.append(f"    closure decomposition (mean, + = toward the other): "
                   f"PM moved {pm_m:+.3f}  sharp moved {sh_m:+.3f}")
        out.append(f"    mean price-CLV of buying the gap side at entry: {pm_m:+.3f}"
                   f"  ({'PM follows sharp' if pm_m > 0 else 'PM does NOT follow sharp'})")
        out.append("  By entry-gap size:")
        for lo, hi in GAP_BUCKETS:
            b = [r for r in uni if lo <= r["abs_g0"] < hi]
            if not b:
                continue
            c = sum(1 for r in b if r["converged"])
            out.append(f"    |g0| [{lo:.2f},{hi:.2f})  n={len(b):3d}  "
                       f"mean|g0|={_mean([r['abs_g0'] for r in b]):.3f}  "
                       f"mean|g1|={_mean([r['abs_g1'] for r in b]):.3f}  "
                       f"conv={c}/{len(b)}  "
                       f"pm_clv={_mean([r['pm_moved'] for r in b]):+.3f}")
    out.append("  Top 10 by entry gap (g0 -> g1; watch for oddities):")
    for r in sorted(rows, key=lambda x: -x["abs_g0"])[:10]:
        out.append(f"    {r['starts']}  {r['home']} vs {r['away']}  "
                   f"g0={r['g0']:+.3f} -> g1={r['g1']:+.3f}  "
                   f"(pm {r['q0']:.3f}->{r['q1']:.3f}, sharp {r['s0']:.3f}->{r['s1']:.3f}, "
                   f"{r['hours_span']:.0f}h)")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="PM-vs-sharp convergence diagnostic")
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--min-gap", type=float, default=MIN_GAP_DEFAULT)
    ap.add_argument("--de-vig", choices=("simple", "shin"), default="simple")
    args = ap.parse_args()
    if args.de_vig == "shin":
        from esports_v2.model.clv import shin_available
        if not shin_available():
            print("!! --de-vig shin requested but the 'shin' package is NOT "
                  "installed — refusing to mislabel simple as shin. "
                  "`pip install shin` or use --de-vig simple.")
            return 2
    result = convergence_rows(iter_snapshots_jsonl(args.snapshots),
                              method=args.de_vig)
    print(report(result, min_gap=args.min_gap, method=args.de_vig))
    return 0


if __name__ == "__main__":
    sys.exit(main())
