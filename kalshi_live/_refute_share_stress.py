#!/usr/bin/env python3
"""ADVERSARIAL SHARE-FRAGILITY STRESS — READ-ONLY, public /orderbook only.

Lens: the config's reward(size) is scored against a STATIC book. Competitors
chase the same LIP pool. Stress = scale EVERY resting size in the book by k
(all makers thicken proportionally), keep OUR order fixed, recompute qualifying
share with the SAME REC.side_share the live quoter uses.

reward is linear in share -> reward_stressed/reward_base = share_stressed/share_base.
Aggregate keep per series = sum(share_stress) / sum(share_base) over its near-money
strikes (equal-pool assumption across adjacent near-money strikes; stated as caveat).
"""
import json, os, sys
import kalshi_horizon_census as C

REC = C.REC
TARGET, DF, TICK = 1000.0, 0.5, 0.01
JOIN, HALF = 20.0, 7.5
CONFIG_SERIES = ["KXH200MS", "KXB200MON", "KXB200MS"]
# config per-market reward at base share (post M7=3x haircut), from optimizer:
BASE_REWARD = {"KXH200MS": 0.19, "KXB200MON": 0.21, "KXB200MS": 0.12}


def scale_levels(levels, k):
    return [(p, s * k) for (p, s) in levels]


def frac(yl, nl):
    """Two-sided avg qualifying share for our JOIN order, book = yl/nl as given."""
    if not yl or not nl:
        return 0.0
    by = max(p for p, _ in yl)
    bn = max(p for p, _ in nl)
    cy, cn = min(JOIN, HALF / by), min(JOIN, HALF / bn)
    ys = REC.side_share(yl, [(by, cy)], TARGET, DF, TICK)[0]
    ns = REC.side_share(nl, [(bn, cn)], TARGET, DF, TICK)[0]
    return (ys + ns) / 2.0


def frac_stressed(yl, nl, k):
    """Scale COMPETITOR depth by k (whole book), our order stays fixed size."""
    return frac(scale_levels(yl, k), scale_levels(nl, k))


def get_book(t):
    ob = C.get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
    yl = C.levels(ob.get("yes_dollars"))
    nl = C.levels(ob.get("no_dollars"))
    return yl, nl


def main():
    out = {}
    for s in CONFIG_SERIES:
        r = C.get(f"/markets?series_ticker={s}&status=open&limit=200")
        ms = r.get("markets") or []
        strikes = []
        for m in ms:
            t = m.get("ticker")
            try:
                yl, nl = get_book(t)
            except Exception:
                continue
            b = frac(yl, nl)
            if b <= 0:
                continue
            by = max(p for p, _ in yl)
            strikes.append((t, b, by, yl, nl))
        # near-money = strikes whose best-yes price is not in the tails; rank by base share
        strikes.sort(key=lambda x: -x[1])
        picked = strikes[:4]  # config says ~3-4 near-money strikes / market
        rows = []
        agg = {"base": 0.0, "k2": 0.0, "k3": 0.0}
        for t, b, by, yl, nl in picked:
            f2 = frac_stressed(yl, nl, 2.0)
            f3 = frac_stressed(yl, nl, 3.0)
            agg["base"] += b
            agg["k2"] += f2
            agg["k3"] += f3
            rows.append({"ticker": t, "best_yes": round(by, 3),
                         "base": round(b, 4), "k2": round(f2, 4), "k3": round(f3, 4),
                         "keep2_pct": round(100 * f2 / b, 1) if b else 0,
                         "keep3_pct": round(100 * f3 / b, 1) if b else 0})
        keep2 = agg["k2"] / agg["base"] if agg["base"] else 0
        keep3 = agg["k3"] / agg["base"] if agg["base"] else 0
        rew = BASE_REWARD.get(s, 0.0)
        out[s] = {"n_strikes_scored": len(picked), "rows": rows,
                  "keep2_frac": round(keep2, 4), "keep3_frac": round(keep3, 4),
                  "base_reward_usd_day": rew,
                  "reward_k2_usd_day": round(rew * keep2, 4),
                  "reward_k3_usd_day": round(rew * keep3, 4)}
        print(f"\n=== {s}  (base reward {rew:.2f}/day) ===")
        for rr in rows:
            print(f"  {rr['ticker']:28s} byes={rr['best_yes']:.2f} base={rr['base']:.4f} "
                  f"2x={rr['k2']:.4f}({rr['keep2_pct']:.0f}%) 3x={rr['k3']:.4f}({rr['keep3_pct']:.0f}%)")
        print(f"  SERIES keep: 2x={keep2*100:.0f}%  3x={keep3*100:.0f}%  ->  "
              f"reward 2x=${rew*keep2:.3f}/day  3x=${rew*keep3:.3f}/day")

    tot_base = sum(BASE_REWARD.values())
    tot2 = sum(out[s]["reward_k2_usd_day"] for s in out)
    tot3 = sum(out[s]["reward_k3_usd_day"] for s in out)
    print(f"\n==== BASKET REWARD ====")
    print(f"  base (static book): ${tot_base:.3f}/day")
    print(f"  2x competitor depth: ${tot2:.3f}/day")
    print(f"  3x competitor depth: ${tot3:.3f}/day")
    out["_basket"] = {"reward_base": round(tot_base, 4),
                      "reward_k2": round(tot2, 4), "reward_k3": round(tot3, 4)}
    json.dump(out, open("_refute_share_stress.json", "w"), indent=1)
    print("\nwrote _refute_share_stress.json")


if __name__ == "__main__":
    sys.exit(main())
