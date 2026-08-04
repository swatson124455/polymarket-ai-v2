#!/usr/bin/env python3
"""W10 PHASE 4 — public trade history per due event: movement (H4) + competition proxy (H3).

Phase 3 established payment is NOT presence-time-proportional (an event paid $12.94 on
2.4 min of presence, 36x the time-proportional bound), consistent with LIP score-SHARE
semantics: pool x (our score / all makers' scores). That makes competition the prime
suspect for zero-paying despite real presence. Without historical book depth, the public
trades tape is the best available competition evidence:

  other_fill_share  contracts traded on the venue during our presence window that were
                    NOT our fills. Our fills are known exactly from the frozen orders.
                    A high share proves other resting liquidity existed at the touch
                    (competition PROXY — flow share is not score share; labeled as such).
  px_range/px_std   movement of trade prices over the market's traded life (H4 direct).

Read-only (public endpoint, no auth). Writes only under --outdir.
"""
import argparse
import collections
import datetime as dt
import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_presence_calibrate import _iso  # noqa: E402


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def trade_fields(tr):
    """Venue shape defense: prefer *_dollars / *_fp, fall back to plain cents/ints."""
    px = tr.get("yes_price_dollars")
    px = _f(px) if px is not None else _f(tr.get("yes_price")) / 100.0
    ct = tr.get("count_fp")
    ct = _f(ct) if ct is not None else _f(tr.get("count"))
    return px, ct, tr.get("created_time")


def fetch_trades(public_get, ticker, max_pages=30):
    rows, cursor = [], ""
    for _ in range(max_pages):
        path = f"/trade-api/v2/markets/trades?ticker={ticker}&limit=1000"
        if cursor:
            path += f"&cursor={cursor}"
        d = public_get(path)
        page = d.get("trades") or []
        rows += page
        cursor = d.get("cursor") or d.get("next_cursor") or ""
        if not page or not cursor:
            break
        time.sleep(0.2)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/w10")
    ap.add_argument("--snapshot", required=True)
    a = ap.parse_args()
    import maker_kalshi_quoter as q

    raw = json.load(open(a.snapshot))
    table = json.load(open(os.path.join(a.outdir, "w10_event_table.json")))
    events = {}
    events.update({k: dict(v, zero=True) for k, v in table["zero_payers"].items()})
    events.update({k: dict(v, zero=False) for k, v in table["paid_events"].items()})

    our_fills = collections.defaultdict(float)   # ticker -> contracts we filled
    for o in raw["orders"]:
        t = o.get("ticker")
        if t:
            our_fills[t] += _f(o.get("fill_count_fp"))

    out = {}
    for ev, e in sorted(events.items()):
        pxs, tot_ct, our_ct = [], 0.0, 0.0
        for t in e["tickers"]:
            trs = fetch_trades(q.public_get, t)
            for tr in trs:
                px, ct, _ts = trade_fields(tr)
                if ct <= 0:
                    continue
                pxs.append(px)
                tot_ct += ct
            our_ct += our_fills.get(t, 0.0)
            time.sleep(0.15)
        row = {"zero": e["zero"], "credit_usd": e["credit_usd"],
               "covered_h": e["covered_h"],
               "n_trades": len(pxs), "venue_ct": round(tot_ct, 2),
               "our_fill_ct": round(our_ct, 2),
               "other_fill_share": (round(1.0 - min(our_ct / tot_ct, 1.0), 4)
                                     if tot_ct > 0 else None),
               "px_range": round(max(pxs) - min(pxs), 4) if pxs else None,
               "px_std": round(st.pstdev(pxs), 4) if len(pxs) > 1 else 0.0}
        out[ev] = row
        print(f"{ev:32s} zero={int(e['zero'])} credit={e['credit_usd']:6.2f} "
              f"trades={len(pxs):5d} venue_ct={tot_ct:9.1f} ours={our_ct:7.1f} "
              f"othershare={row['other_fill_share']} range={row['px_range']} "
              f"std={row['px_std']}")

    path = os.path.join(a.outdir, "w10_phase4_trades.json")
    json.dump({"schema": 1,
               "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
               "note": "trades fetched over each market's full tape; competition proxy "
                       "is fill-flow share, not LIP score share (labeled proxy).",
               "events": out}, open(path, "w"), indent=1, sort_keys=True)

    for name in ("zero", "paid"):
        pop = [r for r in out.values() if r["zero"] == (name == "zero")]
        rng = [r["px_range"] for r in pop if r["px_range"] is not None]
        osh = [r["other_fill_share"] for r in pop if r["other_fill_share"] is not None]
        print(f"\n{name}: px_range median={st.median(rng):.3f} (n={len(rng)}) | "
              f"other_fill_share median={st.median(osh):.3f} (n={len(osh)})")
    print("wrote", path)


if __name__ == "__main__":
    main()
