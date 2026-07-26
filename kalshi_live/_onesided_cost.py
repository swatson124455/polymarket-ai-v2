#!/usr/bin/env python3
"""READ-ONLY. One-sided reward cost/benefit. Reuses scorecard qualifying_share."""
import sys, json, datetime as dt
from collections import defaultdict
import kalshi_attribution_ledger as L
from kalshi_market_scorecard import qualifying_share, _f

INV_TOL = 1.0          # live KALSHI_INV_TOLERANCE
JOIN_SIZE = 20
MAX_MKT_CAP = 15.0
PER_SIDE = MAX_MKT_CAP / 2.0
MINP, MAXP = 0.04, 0.96

def capped_join(best):
    if best <= 0: return 0
    return max(1, min(JOIN_SIZE, int(PER_SIDE / best)))

def main():
    now = dt.datetime.now(dt.timezone.utc)
    orders = L.get(L.P + "/portfolio/orders?status=resting").get("orders") or []
    ours = defaultdict(lambda: {"yes": None, "no": None})
    for o in orders:
        t = o.get("ticker"); s = o.get("outcome_side")
        px = _f(o.get("yes_price_dollars") if s == "yes" else o.get("no_price_dollars"))
        sz = _f(o.get("remaining_count_fp"))
        cur = ours[t][s]
        ours[t][s] = (px, sz + (cur[1] if cur else 0.0)) if px > 0 else cur
    positions = {p.get("ticker"): p for p in (L.get(L.P + "/portfolio/positions").get("market_positions") or [])}
    progs = L.get(L.P + "/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
    prog = {}
    for p in progs:
        t = p.get("market_ticker")
        if not t: continue
        try:
            st = dt.datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
            en = dt.datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
            days = max((en - st).total_seconds() / 86400, 1/24)
        except Exception:
            days = 1.0
        prog[t] = {"usd_day": (_f(p.get("period_reward"))/10000)/days,
                   "target": _f(p.get("target_size_fp")) or 1000.0,
                   "df": (_f(p.get("discount_factor_bps"))/10000) or 0.5}

    rows = []
    for t in sorted(ours):
        oy, on = ours[t]["yes"], ours[t]["no"]
        onesided = (oy is None) ^ (on is None)
        if not onesided: continue
        pos = _f((positions.get(t) or {}).get("position_fp"))
        flat = abs(pos) < INV_TOL
        pr = prog.get(t)
        ob = L.get(L.P + f"/markets/{t}/orderbook").get("orderbook_fp", {}) or {}
        yb = [(_f(p), _f(s)) for p, s in (ob.get("yes_dollars") or [])]
        nb = [(_f(p), _f(s)) for p, s in (ob.get("no_dollars") or [])]
        tgt = pr["target"] if pr else 1000.0
        df = pr["df"] if pr else 0.5
        usd_day = pr["usd_day"] if pr else 0.0
        # current shares
        ys, yq = qualifying_share(yb, oy[0] if oy else None, oy[1] if oy else 0.0, tgt, df)
        ns, nq = qualifying_share(nb, on[0] if on else None, on[1] if on else 0.0, tgt, df)
        book2s_cur = yq and nq
        cur_snap = (ys + ns)/2 if book2s_cur else 0.0
        # counterfactual: add the missing side as a JOIN quote at its best bid
        best_y = max((p for p, _ in yb), default=None)
        best_n = max((p for p, _ in nb), default=None)
        miss = "yes" if oy is None else "no"
        cf_note = ""
        if miss == "yes":
            if best_y is None: cf_note = "no_best_y"
            bp = best_y
        else:
            if best_n is None: cf_note = "no_best_n"
            bp = best_n
        cf_snap = cur_snap; cf_miss_share = 0.0; add_sz = 0; add_px = bp
        if bp is not None and MINP < bp <= MAXP and not cf_note:
            add_sz = capped_join(bp)
            if miss == "yes":
                yb2 = yb + [(bp, add_sz)]
                ys2, yq2 = qualifying_share(yb2, bp, add_sz, tgt, df)
                book2s_cf = yq2 and nq
                cf_snap = (ys2 + ns)/2 if book2s_cf else 0.0
                cf_miss_share = ys2
            else:
                nb2 = nb + [(bp, add_sz)]
                ns2, nq2 = qualifying_share(nb2, bp, add_sz, tgt, df)
                book2s_cf = yq and nq2
                cf_snap = (ys + ns2)/2 if book2s_cf else 0.0
                cf_miss_share = ns2
        elif bp is not None and not (MINP < bp <= MAXP):
            cf_note = f"missing_side_price_bound({bp})"
        rows.append({
            "ticker": t, "rest": ("Y" if oy else "-")+("Y" if on else "-"),
            "pos": pos, "flat": flat, "usd_day": round(usd_day,4), "tgt": tgt, "df": df,
            "miss": miss, "add_px": add_px, "add_sz": add_sz,
            "cur_snap": round(cur_snap,5), "cf_snap": round(cf_snap,5),
            "cf_miss_share": round(cf_miss_share,5),
            "d_reward_raw": round(usd_day*(cf_snap-cur_snap),4),
            "d_reward_m7": round(usd_day*(cf_snap-cur_snap)/3.0,4),
            "book2s_cur": book2s_cur, "note": cf_note,
        })
    out = {"instant": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "onesided": rows}
    print(json.dumps(out, indent=1))

if __name__ == "__main__":
    main()
