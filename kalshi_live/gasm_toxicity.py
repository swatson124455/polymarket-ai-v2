#!/usr/bin/env python3
"""SETTLEMENT-TOXICITY study — READ-ONLY, public API, no keys, never trades.

The FIGHTMENTION shape is: looks fine while open, guts you at settlement.
Measured signature, per FINALIZED contract, from hourly candlesticks:

  TERMINAL GAP   |last traded close - settlement(0/1)| . A maker resting at the
                 close and carried into resolution eats this per contract.
  ENDGAME VOL%   share of lifetime volume in the final 6h. Concentrated late
                 flow against a resting book IS adverse selection.
  OI GROWTH      open interest change over the final 6h. RISING OI at the end
                 means new positions are being OPENED into resolution -- someone
                 is taking, not closing.
  T-24h ERROR    |close 24h before expiry - settlement|. How much of the answer
                 was still unpriced a day out.

Weighted by final-window volume, because a big gap on a contract nobody trades
costs nothing and a small gap on the whole event's volume costs everything.

WHAT THIS DOES NOT COVER: no trade-direction tape and no queue position, so this
cannot compute realised maker markout. It measures the ENVIRONMENT (how violent
settlement is and how late the information arrives), not our fill P&L.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PUB = "https://api.elections.kalshi.com/trade-api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
SPACING_S = 0.35
_last = [0.0]


def get(p):
    w = SPACING_S - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    r = urllib.request.Request(PUB + p, headers={"User-Agent": "gasm-diligence/1.0 (read-only)"})
    try:
        d = json.loads(urllib.request.urlopen(r, timeout=25).read())
        _last[0] = time.time()
        return d
    except urllib.error.HTTPError as e:
        _last[0] = time.time()
        return {"__err__": e.code}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def study(series, event, hours_back=72, endgame_h=6):
    mk, cur = [], ""
    for _ in range(6):
        d = get(f"/markets?event_ticker={event}&limit=1000" + (f"&cursor={cur}" if cur else ""))
        mk += d.get("markets") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    mk = [m for m in mk if m.get("status") == "finalized" and m.get("result") in ("yes", "no")]
    print(f"\n=== {event}  finalized contracts n={len(mk)}")
    rows = []
    for m in mk:
        t = m["ticker"]
        exp = ts(m["close_time"])
        d = get(f"/series/{series}/markets/{t}/candlesticks"
                f"?start_ts={exp - hours_back * 3600}&end_ts={exp}&period_interval=60")
        if "__err__" in d:
            continue
        cs = [c for c in (d.get("candlesticks") or []) if f((c.get("price") or {}).get("close_dollars")) is not None]
        if not cs:
            continue
        settle = 1.0 if m["result"] == "yes" else 0.0
        last = f(cs[-1]["price"]["close_dollars"])
        endg = [c for c in cs if c["end_period_ts"] > exp - endgame_h * 3600]
        vol_all = sum(f(c.get("volume_fp")) or 0.0 for c in cs)
        vol_end = sum(f(c.get("volume_fp")) or 0.0 for c in endg)
        oi0 = f((endg[0] if endg else cs[-1]).get("open_interest_fp")) or 0.0
        oi1 = f(cs[-1].get("open_interest_fp")) or 0.0
        c24 = min(cs, key=lambda c: abs(c["end_period_ts"] - (exp - 24 * 3600)))
        rows.append({
            "t": t, "strike": m.get("floor_strike"), "result": m["result"],
            "last": last, "gap": abs(last - settle),
            "err24": abs((f(c24["price"]["close_dollars"]) or 0.0) - settle),
            "vol_all": vol_all, "vol_end": vol_end,
            "endvol_pct": 100.0 * vol_end / vol_all if vol_all else 0.0,
            "oi_growth": oi1 - oi0,
            "lifetime_vol": f(m.get("volume_fp")) or 0.0,
        })
    if not rows:
        print("  no candle data")
        return []
    rows.sort(key=lambda r: -(r["strike"] or 0))
    print(f"{'strike':>7} {'res':>4} {'last':>6} {'GAP':>6} {'err@T-24h':>10} {'vol72h':>10} "
          f"{'vol_last6h':>11} {'end%':>6} {'dOI_6h':>10}")
    for r in rows:
        print(f"{str(r['strike']):>7} {r['result']:>4} {r['last']:6.2f} {r['gap']:6.2f} {r['err24']:10.2f} "
              f"{r['vol_all']:10.0f} {r['vol_end']:11.0f} {r['endvol_pct']:6.1f} {r['oi_growth']:+10.0f}")
    vw = sum(r["gap"] * r["vol_end"] for r in rows)
    ve = sum(r["vol_end"] for r in rows)
    vw24 = sum(r["err24"] * r["vol_all"] for r in rows)
    va = sum(r["vol_all"] for r in rows)
    live = [r for r in rows if 0.05 < r["last"] < 0.95]
    print(f"  contracts still UNRESOLVED at the last trade (0.05<p<0.95): {len(live)}/{len(rows)}"
          f"  -> {[ (r['strike'], round(r['last'],2), r['result']) for r in live ]}")
    print(f"  VOL-WEIGHTED TERMINAL GAP (last-6h volume) = {vw/ve if ve else 0:.4f} $/contract")
    print(f"  VOL-WEIGHTED T-24h ERROR (72h volume)      = {vw24/va if va else 0:.4f} $/contract")
    print(f"  final-6h volume = {ve:.0f} of {va:.0f} 72h volume = {100*ve/va if va else 0:.1f}%")
    print(f"  total dOI over final 6h = {sum(r['oi_growth'] for r in rows):+.0f} contracts")
    return rows


if __name__ == "__main__":
    out = {}
    for ser, ev in [a.split(":") for a in sys.argv[1:]]:
        out[ev] = study(ser, ev)
    json.dump(out, open(os.path.join(HERE, "gasm_toxicity.json"), "w"), indent=1)
