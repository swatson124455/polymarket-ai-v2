#!/usr/bin/env python3
"""KALSHI DRIFT DETECTION — public-data fetcher / cache builder.

NEW FILE. Edits nothing. READ-ONLY, UNAUTHENTICATED public API, >=0.35s spacing.

Builds a reproducible offline cache for every contract we actually traded, so the
detector study in kalshi_drift_detect.py can be re-run without touching the network.

Cache layout (kalshi_drift_cache.json):
  { ticker: {
      "market":  {...}          # /markets/{t}          (status,result,expiration_value,strike,times)
      "candles": [ ... ],       # 1-minute, whole contract life, yes_bid/yes_ask/price OHLC + volume
      "trades":  [ ... ],       # public tape, paginated to contract open, microsecond created_time
    } }

VERIFIED FIELD NAMES (live 2026-07-23):
  trades:  trade_id, ticker, created_time, count_fp, yes_price_dollars, no_price_dollars,
           taker_side ('yes'|'no'), taker_book_side, is_block_trade
  candles: end_period_ts, volume_fp, open_interest_fp,
           price{open,high,low,close,mean,previous}_dollars,
           yes_bid{open,high,low,close}_dollars, yes_ask{open,high,low,close}_dollars
  NOTE: candles carry NO SIZE on the book -> no retroactive depth/queue imbalance. Stated,
        not worked around.

API LIMITS (measured, do not re-probe):
  trades limit ceiling = 1000 (>1000 -> 400 'lte' tag)
  candlesticks period_interval in {1,60,1440} only; range cap at interval=1 is between
  2880 and 10080 minutes -> we clip every request to 1440 minutes and page if needed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HOST = "https://api.elections.kalshi.com"
ROOT = "/trade-api/v2"
SPACING = 0.35
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE = os.path.join(HERE, "kalshi_drift_cache.json")

_last = [0.0]


def get(path):
    """Rate-limited GET. Returns (status, parsed_or_text)."""
    dt = time.time() - _last[0]
    if dt < SPACING:
        time.sleep(SPACING - dt)
    _last[0] = time.time()
    req = urllib.request.Request(
        HOST + path,
        headers={"User-Agent": "kalshi-drift-study/1.0", "Accept": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + attempt * 3)
                continue
            return e.code, e.read()[:300].decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            if attempt < 3:
                time.sleep(1 + attempt)
                continue
            return -1, str(e)[:300]
    return 429, "rate limited"


def ts(iso):
    """ISO8601 (with Z or offset, optional microseconds) -> epoch seconds float."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def fetch_market(tk):
    st, d = get(f"{ROOT}/markets/{tk}")
    if st != 200 or not isinstance(d, dict):
        return None
    return d.get("market")


def fetch_candles(tk, t0, t1):
    """1-minute candles over [t0,t1], paged in <=1400-minute chunks (API range cap)."""
    series = tk.split("-")[0]
    out, cur = [], int(t0)
    end = int(t1)
    while cur < end:
        chunk = min(end, cur + 1400 * 60)
        q = urllib.parse.urlencode(
            {"start_ts": cur, "end_ts": chunk, "period_interval": 1}
        )
        st, d = get(f"{ROOT}/series/{series}/markets/{tk}/candlesticks?{q}")
        if st == 200 and isinstance(d, dict):
            out.extend(d.get("candlesticks") or [])
        else:
            print(f"    candles {st} {str(d)[:120]}")
        cur = chunk
    # de-dup on end_period_ts, sort
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: c.get("end_period_ts", 0)):
        k = c.get("end_period_ts")
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq


def fetch_trades(tk, stop_before_ts=None, max_pages=12):
    """Public tape, newest-first, paginated backwards via cursor until exhausted
    or until we have reached stop_before_ts (contract open)."""
    out, cursor, pages = [], None, 0
    while pages < max_pages:
        q = {"ticker": tk, "limit": 1000}
        if cursor:
            q["cursor"] = cursor
        st, d = get(f"{ROOT}/markets/trades?{urllib.parse.urlencode(q)}")
        if st != 200 or not isinstance(d, dict):
            print(f"    trades {st} {str(d)[:120]}")
            break
        batch = d.get("trades") or []
        out.extend(batch)
        pages += 1
        cursor = d.get("cursor")
        if not cursor or not batch:
            break
        oldest = min(ts(t["created_time"]) for t in batch)
        if stop_before_ts and oldest <= stop_before_ts:
            break
    seen, uniq = set(), []
    for t in sorted(out, key=lambda t: t["created_time"]):
        if t["trade_id"] in seen:
            continue
        seen.add(t["trade_id"])
        uniq.append(t)
    return uniq


def main():
    tickers_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CACHE
    tickers = sorted(set(json.load(open(tickers_path))))

    cache = {}
    if os.path.exists(out_path):
        cache = json.load(open(out_path))
        print(f"resume: {len(cache)} cached")

    for i, tk in enumerate(tickers):
        if tk in cache and cache[tk].get("candles") is not None \
                and cache[tk].get("trades") is not None:
            continue
        print(f"[{i+1}/{len(tickers)}] {tk}")
        mkt = fetch_market(tk)
        if not mkt:
            cache[tk] = {"market": None, "candles": [], "trades": []}
            continue
        try:
            o, c = ts(mkt["open_time"]), ts(mkt["close_time"])
        except Exception:  # noqa: BLE001
            now = time.time()
            o, c = now - 86400, now
        # pad both ends by 15 min so pre-entry context exists
        candles = fetch_candles(tk, o - 900, min(c + 900, time.time()))
        trades = fetch_trades(tk, stop_before_ts=o - 900)
        cache[tk] = {"market": mkt, "candles": candles, "trades": trades}
        print(f"    candles={len(candles)} trades={len(trades)} "
              f"status={mkt.get('status')} result={mkt.get('result')}")
        if (i + 1) % 10 == 0:
            json.dump(cache, open(out_path, "w"))

    json.dump(cache, open(out_path, "w"))
    print(f"\nwrote {out_path}: {len(cache)} contracts")
    nc = sum(1 for v in cache.values() if v.get("candles"))
    nt = sum(1 for v in cache.values() if v.get("trades"))
    print(f"  with candles: {nc}   with trades: {nt}")


if __name__ == "__main__":
    main()
