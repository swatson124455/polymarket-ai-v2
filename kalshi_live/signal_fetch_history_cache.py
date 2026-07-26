#!/usr/bin/env python3
"""SIGNAL INVENTORY — build a local cache of PUBLIC market metadata + 1-min candlesticks
for every contract we actually filled in, so the analyses are reproducible offline.

READ-ONLY, unauthenticated, >=0.35s spacing. New file; edits nothing.

Writes ONE new file:  <out>  (default kalshi_live/signal_history_cache.json)
  { ticker: {"market": {...}, "candles": [ ... 1-minute ... ]} }

Candle fields (verified live 2026-07-23): end_period_ts, open_interest_fp, volume_fp,
price{open,high,low,close,mean,previous}_dollars, yes_bid{open,high,low,close}_dollars,
yes_ask{open,high,low,close}_dollars.  These are BEST bid/ask only — no depth.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

HOST = "https://api.elections.kalshi.com"
ROOT = "/trade-api/v2"
SPACING = 0.35
_last = [0.0]
HERE = os.path.dirname(os.path.abspath(__file__))


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACING:
        time.sleep(SPACING - dt)
    _last[0] = time.time()
    req = urllib.request.Request(HOST + path,
                                 headers={"User-Agent": "kalshi-signal-probe/1.0",
                                          "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + attempt * 3)
                continue
            return e.code, e.read()[:200].decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return -1, str(e)[:200]
    return 429, "rate limited"


def main():
    fills_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 \
        else os.path.join(HERE, "signal_history_cache.json")
    fills = json.load(open(fills_path))
    tickers = sorted({f["ticker"] for f in fills})
    print(f"contracts to cache: {len(tickers)}")

    cache = {}
    if os.path.exists(out_path):
        cache = json.load(open(out_path))
        print(f"resuming from {out_path}: {len(cache)} already cached")

    for i, tk in enumerate(tickers):
        if tk in cache and cache[tk].get("candles"):
            continue
        st, d = get(f"{ROOT}/markets/{tk}")
        mkt = d.get("market") if isinstance(d, dict) else None
        if not mkt:
            print(f"  {i:>3} {tk:<32} market {st} {str(d)[:80]}")
            cache[tk] = {"market": None, "candles": []}
            continue
        # candlestick window: the contract's own life, clipped to the 1440-min API cap
        try:
            o = int(datetime.fromisoformat(
                mkt["open_time"].replace("Z", "+00:00")).timestamp())
            c = int(datetime.fromisoformat(
                mkt["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:  # noqa: BLE001
            o, c = int(time.time()) - 86400, int(time.time())
        if c - o > 1400 * 60:
            o = c - 1400 * 60
        st2, d2 = get(f"{ROOT}/series/{tk.split('-')[0]}/markets/{tk}/candlesticks"
                      f"?start_ts={o - 120}&end_ts={c + 120}&period_interval=1")
        cs = d2.get("candlesticks", []) if isinstance(d2, dict) else []
        cache[tk] = {"market": mkt, "candles": cs}
        print(f"  {i:>3} {tk:<32} mkt={st} candles={len(cs):<5} "
              f"life={(c - o) // 60}m status={mkt.get('status')} result={mkt.get('result')}")
        if i % 10 == 0:
            json.dump(cache, open(out_path, "w"))

    json.dump(cache, open(out_path, "w"))
    print(f"wrote {out_path}  contracts={len(cache)}  "
          f"candles={sum(len(v['candles']) for v in cache.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
