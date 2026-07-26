#!/usr/bin/env python3
"""FIELD-METHOD DATA PROBE -- NEW FILE, READ-ONLY, PUBLIC API ONLY, NO KEYS, NEVER TRADES.

Purpose: establish EXACTLY what raw inputs the standard adverse-selection detectors
(OFI, depth imbalance, microprice, VPIN, markout, sweep detection) would have on Kalshi,
BEFORE any of them is recommended. Verifies rather than assumes:

  P1  Does a PUBLIC trades tape exist?  /markets/trades  -> fields, taker_side, timestamp
      granularity (seconds? sub-second?), how far back one paginated pull reaches.
  P2  Orderbook shape: /markets/{t}/orderbook -> levels, both sides, size units.
  P3  Tape density: trades per contract per hour on our live series -- is a 2-min bucket
      even non-empty?
  P4  Candlestick endpoint granularity (fallback tape proxy if the raw tape is thin).

Run:  python kalshi_field_data_probe.py
"""
import json
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

PUB = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "kalshi-field-data-probe/1.0 (read-only measurement)"}
SPACING_S = 0.35
ALLOW = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH", "KXTEMPCHIH", "KXTEMPLAXH")


def get(path):
    time.sleep(SPACING_S)
    req = urllib.request.Request(PUB + path, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def pick_tickers(n=6):
    out = []
    for s in ALLOW:
        try:
            d = get(f"/markets?series_ticker={s}&status=open&limit=200")
        except Exception as e:
            print(f"  series {s}: {e!r}")
            continue
        ms = d.get("markets") or []
        ms.sort(key=lambda m: -(m.get("volume") or 0))
        for m in ms[:2]:
            out.append((s, m["ticker"], m.get("volume"), m.get("close_time")))
        if len(out) >= n:
            break
    return out[:n]


def main():
    print("=" * 78)
    print("P2  ORDERBOOK SHAPE")
    print("=" * 78)
    tk = pick_tickers()
    if not tk:
        print("no open markets on the allowlist series -- probing generic open markets")
        d = get("/markets?status=open&limit=20")
        tk = [("?", m["ticker"], m.get("volume"), m.get("close_time"))
              for m in (d.get("markets") or [])[:6]]
    for s, t, vol, ct in tk:
        print(f"\n  {t}  (series {s}, volume={vol}, close={ct})")
        try:
            ob = get(f"/markets/{t}/orderbook?depth=100")
        except Exception as e:
            print(f"    orderbook ERROR {e!r}")
            continue
        # NOTE: the key is `orderbook_fp`, NOT `orderbook`. Reading `orderbook` returns {}
        # and looks exactly like an empty book -- that false negative cost one probe cycle.
        book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
        print(f"    top-level keys: {sorted(book.keys())}")
        for side in book:
            lv = book.get(side)
            if isinstance(lv, list):
                print(f"    {side}: {len(lv)} levels; first 4 = {lv[:4]}")
            else:
                print(f"    {side}: {type(lv).__name__} {str(lv)[:200]}")

    print()
    print("=" * 78)
    print("P1/P3  PUBLIC TRADES TAPE")
    print("=" * 78)
    for s, t, vol, ct in tk:
        rows, cur = [], ""
        for _ in range(3):
            try:
                d = get(f"/markets/trades?ticker={t}&limit=1000" + (f"&cursor={cur}" if cur else ""))
            except Exception as e:
                print(f"  {t}: trades ERROR {e!r}")
                break
            tr = d.get("trades") or []
            rows += tr
            cur = d.get("cursor") or ""
            if not cur or not tr:
                break
        if not rows:
            print(f"  {t}: 0 trades")
            continue
        print(f"\n  {t}: {len(rows)} trades pulled")
        print(f"    fields: {sorted(rows[0].keys())}")
        print(f"    sample: {json.dumps(rows[0])[:400]}")
        ts = [r.get("created_time") for r in rows if r.get("created_time")]
        if ts:
            print(f"    time range: {min(ts)} .. {max(ts)}")
            # granularity: how many distinct sub-second values?
            frac = Counter(x.split(".")[1][:6] if "." in x else "NONE" for x in ts)
            print(f"    subsecond-part distinct values: {len(frac)}  (top: {frac.most_common(3)})")
            # collisions => multi-level sweeps share a timestamp
            c = Counter(ts)
            multi = sum(1 for v in c.values() if v > 1)
            print(f"    distinct timestamps {len(c)} of {len(ts)}; "
                  f"{multi} timestamps carry >1 trade (sweep signature)")
            # density per 2-min bucket over covered span
            buck = Counter(x[:15] for x in ts)  # yyyy-mm-ddThh:m -> 10-min-ish
            print(f"    trades per 10-min bucket: n={len(buck)} "
                  f"median={sorted(buck.values())[len(buck)//2]} max={max(buck.values())}")
        sides = Counter(r.get("taker_side") for r in rows)
        print(f"    taker_side distribution: {dict(sides)}")

    print()
    print("=" * 78)
    print("P4  CANDLESTICKS")
    print("=" * 78)
    for s, t, vol, ct in tk[:2]:
        now = int(time.time())
        for per in (1, 60):
            try:
                d = get(f"/series/{s}/markets/{t}/candlesticks"
                        f"?start_ts={now - 6 * 3600}&end_ts={now}&period_interval={per}")
            except Exception as e:
                print(f"  {t} period={per}: ERROR {e!r}")
                continue
            cs = d.get("candlesticks") or []
            print(f"  {t} period_interval={per}min -> {len(cs)} candles")
            if cs:
                print(f"    fields: {sorted(cs[-1].keys())}")
                print(f"    last: {json.dumps(cs[-1])[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
