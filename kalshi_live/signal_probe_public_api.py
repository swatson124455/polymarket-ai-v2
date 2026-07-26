#!/usr/bin/env python3
"""SIGNAL INVENTORY probe #1 — what PUBLIC (unauthenticated) API surface exists?

READ-ONLY. No keys. >=0.35s spacing. New file; edits nothing.

Purpose: decide whether a public TRADE TAPE / candlestick / history surface exists.
A tape unlocks OFI/VPIN-style methods. Without one we are limited to book snapshots
plus our own fills.

Usage:  python kalshi_live/signal_probe_public_api.py
"""
import json
import sys
import time
import urllib.error
import urllib.request

HOSTS = [
    "https://api.elections.kalshi.com",
    "https://external-api.kalshi.com",
]
ROOT = "/trade-api/v2"
SPACING = 0.35
TIMEOUT = 20

_last = [0.0]


def get(host, path):
    dt = time.time() - _last[0]
    if dt < SPACING:
        time.sleep(SPACING - dt)
    _last[0] = time.time()
    url = host + path
    req = urllib.request.Request(url, headers={"User-Agent": "kalshi-signal-probe/1.0",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode()[:400]


def brief(body, n=260):
    try:
        s = body.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        s = repr(body)
    s = " ".join(s.split())
    return s[:n]


def main():
    host = HOSTS[0]
    # --- 1. find a live ticker to probe against -------------------------------
    st, body = get(host, ROOT + "/markets?limit=200&status=open")
    print(f"[bootstrap] GET {ROOT}/markets?limit=200&status=open -> {st}")
    tickers, series_t, gas_t, temp_t = [], None, None, None
    if st == 200:
        d = json.loads(body)
        mkts = d.get("markets", [])
        print(f"           markets returned: {len(mkts)}  cursor={bool(d.get('cursor'))}")
        if mkts:
            print(f"           market object keys: {sorted(mkts[0].keys())}")
        for m in mkts:
            t = m.get("ticker", "")
            tickers.append(t)
            if t.startswith("KXAAAGASD") and not gas_t:
                gas_t = t
            if t.startswith("KXTEMP") and not temp_t:
                temp_t = t
    probe_t = gas_t or temp_t or (tickers[0] if tickers else "KXAAAGASD-26JUL23-4.100")
    series_t = probe_t.split("-")[0]
    print(f"[bootstrap] probe ticker = {probe_t}   series = {series_t}")
    print(f"[bootstrap] gas ticker = {gas_t}   temp ticker = {temp_t}")

    now = int(time.time())
    day_ago = now - 86400

    paths = [
        # --- tape / trades ---
        f"{ROOT}/markets/trades?ticker={probe_t}&limit=100",
        f"{ROOT}/markets/trades?limit=100",
        f"{ROOT}/markets/{probe_t}/trades?limit=100",
        f"{ROOT}/trades?ticker={probe_t}&limit=100",
        f"{ROOT}/exchange/trades?limit=10",
        # --- candlesticks / OHLC ---
        f"{ROOT}/series/{series_t}/markets/{probe_t}/candlesticks"
        f"?start_ts={day_ago}&end_ts={now}&period_interval=1",
        f"{ROOT}/series/{series_t}/markets/{probe_t}/candlesticks"
        f"?start_ts={day_ago}&end_ts={now}&period_interval=60",
        f"{ROOT}/markets/{probe_t}/candlesticks?start_ts={day_ago}&end_ts={now}&period_interval=1",
        f"{ROOT}/markets/candlesticks?ticker={probe_t}",
        f"{ROOT}/series/{series_t}/candlesticks?ticker={probe_t}",
        # --- history / stats ---
        f"{ROOT}/markets/{probe_t}/history",
        f"{ROOT}/markets/{probe_t}/stats",
        f"{ROOT}/markets/{probe_t}/price_history",
        f"{ROOT}/market_stats?ticker={probe_t}",
        f"{ROOT}/markets/{probe_t}/orderbook?depth=100",
        f"{ROOT}/markets/{probe_t}",
        # --- structural ---
        f"{ROOT}/series/{series_t}",
        f"{ROOT}/series?limit=10",
        f"{ROOT}/events?limit=5&with_nested_markets=false",
        f"{ROOT}/exchange/schedule",
        f"{ROOT}/exchange/announcements",
        f"{ROOT}/incentive_programs?status=active&limit=2",
        f"{ROOT}/milestones?limit=5",
        f"{ROOT}/multivariate_event_collections?limit=2",
        # --- misc plausible ---
        f"{ROOT}/markets/{probe_t}/order_book",
        f"{ROOT}/markets/{probe_t}/last_trade",
        f"{ROOT}/markets/{probe_t}/volume",
        f"{ROOT}/markets/{probe_t}/open_interest",
        f"{ROOT}/statistics?ticker={probe_t}",
        f"{ROOT}/markets/{probe_t}/ticker",
    ]

    print("\n=== PUBLIC (unauthenticated) PROBE — host " + host + " ===")
    results = []
    for p in paths:
        st, body = get(host, p)
        results.append((st, p, brief(body)))
        print(f"{st:>4}  {p}")
        if st == 200:
            print(f"      -> {brief(body, 300)}")

    # cross-host check on the two decisive ones
    print("\n=== CROSS-HOST CHECK (external-api.kalshi.com) ===")
    for p in [f"{ROOT}/markets/trades?ticker={probe_t}&limit=5",
              f"{ROOT}/series/{series_t}/markets/{probe_t}/candlesticks"
              f"?start_ts={day_ago}&end_ts={now}&period_interval=1"]:
        st, body = get(HOSTS[1], p)
        print(f"{st:>4}  {p}\n      -> {brief(body, 200)}")

    print("\n=== SUMMARY: 200s ===")
    for st, p, _ in results:
        if st == 200:
            print(f"  200  {p}")
    print("=== SUMMARY: non-200 ===")
    for st, p, b in results:
        if st != 200:
            print(f"  {st:>4} {p}   {b[:90]}")


if __name__ == "__main__":
    sys.exit(main())
