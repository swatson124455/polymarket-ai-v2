#!/usr/bin/env python3
"""DIAGNOSTIC: distribution of sharp-vs-Polymarket gaps in captured snapshots.

Answers "would the edge rule ever fire?" EMPIRICALLY, before results exist:
for every distinct match whose closing snapshot carries a captured Polymarket
price, compute the no-vig sharp prob, orient it to the PM YES outcome, and
report the two-sided edge — then show how many matches clear each candidate
threshold at fee=0.02 (current default) and fee=0.

This is NOT a backtest (no outcomes, no P&L). It measures whether MISPRICINGS
EXIST at capture time, and flags placeholder prices (an empty book shows
~0.50 on Gamma — a "gap" against 0.50 is often unfillable, not free money).

    python -m esports_v2.scripts.edge_distribution --snapshots <jsonl>
"""
from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Optional

from esports_v2.model.closing_line import iter_snapshots_jsonl, reduce_to_closing_lines
from esports_v2.model.sharp_eval import no_vig_prob_a
from esports_v2.model.team_match import same_team

FEE_DEFAULT = 0.02
THRESHOLDS = (0.03, 0.05, 0.07, 0.10)


def yes_side_sharp_prob(cl) -> Optional[float]:
    """No-vig sharp prob of the PM YES outcome for one closing line, or None.

    Orientation is re-derived from the stored ``yes_outcome`` name against the
    odds row's home/away via the SAME conservative matcher used at capture
    (correct-or-absent: ambiguous/unmatched -> None, never a guess).
    """
    if cl.market_price is None or not cl.yes_outcome:
        return None
    pa = no_vig_prob_a(cl.odds_a, cl.odds_b, "simple")
    if pa is None:
        return None
    is_home = same_team(cl.yes_outcome, cl.home, None)
    is_away = same_team(cl.yes_outcome, cl.away, None)
    if is_home == is_away:  # both or neither -> ambiguous
        return None
    return pa if is_home else (1.0 - pa)


def edge_rows(closing: Dict) -> List[dict]:
    """One row per PM-priced match: sharp prob (YES side), PM price, gaps."""
    rows = []
    for cl in closing.values():
        p_sharp = yes_side_sharp_prob(cl)
        if p_sharp is None:
            continue
        q = float(cl.market_price)
        gap = p_sharp - q                     # >0: PM underprices YES
        rows.append({
            "match_key": cl.match_key, "home": cl.home, "away": cl.away,
            "starts": str(cl.starts)[:16], "sharp_yes": p_sharp, "pm_yes": q,
            "gap": gap, "abs_gap": abs(gap),
            "placeholder": abs(q - 0.5) < 0.0051,   # empty-book signature
        })
    return rows


def report(rows: List[dict], fee: float = FEE_DEFAULT) -> str:
    out = ["Sharp-vs-PM edge distribution (DIAGNOSTIC — no outcomes, no P&L)"]
    out.append(f"  PM-priced matches with orientable sharp line: {len(rows)}")
    if not rows:
        out.append("  (nothing to measure yet)")
        return "\n".join(out)
    ph = sum(1 for r in rows if r["placeholder"])
    out.append(f"  placeholder-price matches (PM ~0.50, likely empty book): {ph}")
    live = [r for r in rows if not r["placeholder"]]
    for label, universe in (("ALL", rows), ("ex-placeholder", live)):
        if not universe:
            continue
        gaps = sorted(r["abs_gap"] for r in universe)
        mid = gaps[len(gaps) // 2]
        out.append(f"  [{label}] n={len(universe)}  median |gap|={mid:.3f}  "
                   f"max |gap|={gaps[-1]:.3f}")
        for fee_v in (fee, 0.0):
            hits = {t: sum(1 for r in universe if r["abs_gap"] - fee_v >= t)
                    for t in THRESHOLDS}
            out.append(f"    fee={fee_v:.2f}: clears " +
                       "  ".join(f"edge>={t:.2f}: {n}" for t, n in hits.items()))
    out.append("  Top 10 by |gap| (eyeball for placeholder/oddity before excitement):")
    for r in sorted(rows, key=lambda x: -x["abs_gap"])[:10]:
        tag = " [PLACEHOLDER?]" if r["placeholder"] else ""
        out.append(f"    {r['starts']}  {r['home']} vs {r['away']}  "
                   f"sharp={r['sharp_yes']:.3f} pm={r['pm_yes']:.3f} "
                   f"gap={r['gap']:+.3f}{tag}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sharp-vs-PM gap distribution")
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--fee", type=float, default=FEE_DEFAULT)
    args = ap.parse_args()
    closing = reduce_to_closing_lines(iter_snapshots_jsonl(args.snapshots))
    rows = edge_rows(closing)
    print(f"closing lines: {len(closing)}")
    print(report(rows, fee=args.fee))
    return 0


if __name__ == "__main__":
    sys.exit(main())
