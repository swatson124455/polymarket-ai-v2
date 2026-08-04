#!/usr/bin/env python3
"""W10 PHASE 3 — per-event mechanism features over the frozen phase-1/2 data.

For every DUE event (paid and zero-payer alike) this computes, from the frozen orders:
  paired_h        simultaneous bid+ask resting per strike (interval intersection), summed.
                  The 2026-07-26 reward-vs-fill audit measured PAIREDNESS as the earnings
                  lever, so unpaired presence earning $0 is a candidate mechanism.
  paired_frac     paired_h / covered_h.
  buy_n / sell_n  order mix (rules out exit-only stories or confirms them).
  px_extreme_h    presence-hours weighted by resting-quote extremity: hours spent quoting
                  at min(p, 1-p) < 0.05 — deep-in-the-money rests that reward scoring
                  functions typically discount toward zero.
  sib_window_h    presence intersected with PROGRAM windows where phase 2 retention holds
                  any SIBLING strike's program row (window template inferred from
                  siblings — labeled INFERRED; per-strike pool NOT assumed).
  sib_ub_usd      accrual upper bound at 100% share against sibling-derived pools over
                  those windows (INFERRED — sibling pool applied to our strike).

Output: per-event feature table + paid-vs-zero distribution comparison. Read-only over
frozen files; writes only into --outdir.
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_presence_calibrate import _iso, union_seconds  # noqa: E402


def merged(ivs):
    ivs = sorted(ivs)
    out = [list(ivs[0])]
    for s, e in ivs[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def intersect_seconds(a_ivs, b_ivs):
    tot = 0.0
    for a1, a2 in a_ivs:
        for b1, b2 in b_ivs:
            lo, hi = max(a1, b1), min(a2, b2)
            if hi > lo:
                tot += (hi - lo).total_seconds()
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/w10")
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()

    raw = json.load(open(a.snapshot))
    table = json.load(open(os.path.join(a.outdir, "w10_event_table.json")))
    progs = json.load(open(os.path.join(a.outdir, "w10_programs_closed.json")))["programs"]
    meta = raw["market_meta"]

    events = {}
    events.update({k: dict(v, zero=True) for k, v in table["zero_payers"].items()})
    events.update({k: dict(v, zero=False) for k, v in table["paid_events"].items()})

    # per-strike resting intervals split by book_side, plus price extremity
    per = collections.defaultdict(lambda: {"bid": [], "ask": [], "all": [],
                                           "extreme_s": 0.0, "buy": 0, "sell": 0})
    for o in raw["orders"]:
        t = o.get("ticker")
        if not t:
            continue
        try:
            iv = (_iso(o["created_time"]), _iso(o["last_update_time"]))
        except Exception:
            continue
        dur = (iv[1] - iv[0]).total_seconds()
        if dur < 0:
            continue
        d = per[t]
        d["all"].append(iv)
        side = "bid" if o.get("book_side") == "bid" else "ask"
        d[side].append(iv)
        d["buy" if o.get("action") == "buy" else "sell"] += 1
        try:
            px = float(o.get("yes_price_dollars") or 0)
            if min(px, 1.0 - px) < 0.05:
                d["extreme_s"] += dur
        except Exception:
            pass

    # sibling program windows per EVENT (template from ANY retained strike row)
    win_by_event = collections.defaultdict(list)
    for r in progs:
        t = r.get("market_ticker") or ""
        ev = None
        m = meta.get(t)
        if m:
            ev = m.get("event_ticker")
        if ev is None:
            for cand in events:
                if t == cand or t.startswith(cand + "-"):
                    ev = cand
                    break
        if ev is None:
            continue
        try:
            wa, wb = _iso(r["start_date"]), _iso(r["end_date"])
        except Exception:
            continue
        pool_day = float(r.get("period_reward") or 0) / 10000.0
        win_by_event[ev].append((wa, wb, pool_day))

    rows = {}
    for ev, e in events.items():
        f = {"zero": e["zero"], "credit_usd": e["credit_usd"],
             "covered_h": e["covered_h"], "paired_h": 0.0, "extreme_h": 0.0,
             "buy_n": 0, "sell_n": 0, "sib_window_h": 0.0, "sib_ub_usd": 0.0,
             "sib_windows_n": len(win_by_event.get(ev, []))}
        for t in e["tickers"]:
            d = per.get(t)
            if not d or not d["all"]:
                continue
            all_iv = merged(d["all"])
            if d["bid"] and d["ask"]:
                f["paired_h"] += intersect_seconds(merged(d["bid"]),
                                                   merged(d["ask"])) / 3600.0
            f["extreme_h"] += d["extreme_s"] / 3600.0
            f["buy_n"] += d["buy"]
            f["sell_n"] += d["sell"]
            # windows are shared per event-hour across sibling strikes; dedupe identical
            seen = set()
            for wa, wb, pool_day in win_by_event.get(ev, []):
                key = (wa, wb)
                ov = intersect_seconds(all_iv, [(wa, wb)])
                f["sib_window_h"] += ov / 3600.0
                wlen = (wb - wa).total_seconds()
                if wlen > 0 and key not in seen:
                    # ub at 100% share of ONE strike's pool for our overlap time
                    f["sib_ub_usd"] += pool_day * (wlen / 86400.0) * (ov / wlen)
                    seen.add(key)
        f["paired_frac"] = round(f["paired_h"] / f["covered_h"], 4) if f["covered_h"] else None
        f["extreme_frac"] = round(f["extreme_h"] / f["covered_h"], 4) if f["covered_h"] else None
        for k in ("paired_h", "extreme_h", "sib_window_h", "sib_ub_usd"):
            f[k] = round(f[k], 4)
        rows[ev] = f

    out_path = os.path.join(a.outdir, "w10_phase3_features.json")
    json.dump({"schema": 1, "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
               "events": rows}, open(out_path, "w"), indent=1, sort_keys=True)

    def show(pop, label):
        print(f"\n{label}:")
        print(f"  {'event':32s} {'credit':>7s} {'rest_h':>7s} {'paired':>7s} "
              f"{'extremeF':>8s} {'buy/sell':>9s} {'sibwin_h':>8s} {'sib_ub$':>8s}")
        for ev, f in sorted(pop, key=lambda kv: -kv[1]["covered_h"]):
            print(f"  {ev:32s} {f['credit_usd']:7.2f} {f['covered_h']:7.2f} "
                  f"{(f['paired_frac'] if f['paired_frac'] is not None else -1):7.3f} "
                  f"{(f['extreme_frac'] if f['extreme_frac'] is not None else -1):8.3f} "
                  f"{f['buy_n']:>4d}/{f['sell_n']:<4d} {f['sib_window_h']:8.3f} "
                  f"{f['sib_ub_usd']:8.4f}")

    zp = [(k, v) for k, v in rows.items() if v["zero"]]
    pd_ = [(k, v) for k, v in rows.items() if not v["zero"]]
    show(zp, f"ZERO-PAYERS ({len(zp)})")
    show(pd_, f"PAID ({len(pd_)})")

    import statistics as st
    for name, pop in (("zero", zp), ("paid", pd_)):
        pf = [v["paired_frac"] for _, v in pop if v["paired_frac"] is not None]
        ef = [v["extreme_frac"] for _, v in pop if v["extreme_frac"] is not None]
        print(f"\n{name}: paired_frac median={st.median(pf):.3f} mean={st.mean(pf):.3f} "
              f"n={len(pf)} | extreme_frac median={st.median(ef):.3f} n={len(ef)}")
    print("wrote", out_path)


if __name__ == "__main__":
    main()
