#!/usr/bin/env python3
"""SIGNAL INVENTORY probe #3 — is the public history RETROACTIVE on SETTLED markets?

READ-ONLY, unauthenticated. New file; edits nothing.

Decisive question: probe #2 showed a public tape and 1-minute bid/ask candlesticks on
LIVE markets. If they also work on markets that have already SETTLED, then the entire
07-20..22 loss set (the -$40.9 of exit-at-0.00 positions in
kalshi_live/kalshi_transactions_2026-07-23.csv) can be replayed minute-by-minute and any
proposed detector can be BACKTESTED with positive and negative controls, for free.

Also pins min_ts/max_ts semantics on /markets/trades, which probe #2 could not separate
(it passed limit=5, so every variant returned the same 5 newest rows).
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOST = "https://api.elections.kalshi.com"
ROOT = "/trade-api/v2"
SPACING = 0.35
_last = [0.0]

# From kalshi_live/kalshi_transactions_2026-07-23.csv — the worst exit-at-0.00 positions.
LOSERS = [
    ("KXTEMPCHIH-26JUL2212-T69.99", "2026-07-22T15:23:21Z", "2026-07-22T17:30:54Z", -7.23),
    ("KXTEMPLAXH-26JUL2212-T71.99", "2026-07-22T15:14:26Z", "2026-07-22T17:30:54Z", -7.20),
    ("KXTEMPCHIH-26JUL2123-T70.99", "2026-07-22T02:33:06Z", "2026-07-22T04:31:04Z", -4.55),
    ("KXTEMPCHIH-26JUL2207-T59.99", "2026-07-22T10:04:08Z", "2026-07-22T12:30:54Z", -2.80),
    ("KXTEMPCHIH-26JUL2211-T66.99", "2026-07-22T14:05:23Z", "2026-07-22T16:30:54Z", -2.40),
]
# Gas contracts traded in the same window that did NOT blow up — the negative control.
CONTROLS = [
    "KXAAAGASD-26JUL22-4.055",
    "KXAAAGASD-26JUL21-4.020",
    "KXAAAGASD-26JUL23-4.100",
]


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACING:
        time.sleep(SPACING - dt)
    _last[0] = time.time()
    req = urllib.request.Request(HOST + path,
                                 headers={"User-Agent": "kalshi-signal-probe/1.0",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:250].decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)[:250]


def ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def main():
    print("=" * 78)
    print("A. CANDLESTICKS ON SETTLED MARKETS (the 07-22 losers)")
    print("=" * 78)
    for tk, t_open, t_close, pnl in LOSERS:
        s = tk.split("-")[0]
        a, b = ts(t_open) - 5400, ts(t_close) + 600
        st, d = get(f"{ROOT}/series/{s}/markets/{tk}/candlesticks"
                    f"?start_ts={a}&end_ts={b}&period_interval=1")
        cs = d.get("candlesticks", []) if isinstance(d, dict) else []
        print(f"  {st}  {tk:<30} pnl={pnl:>6}  candles={len(cs)}")
        if isinstance(d, str):
            print(f"       -> {d}")
        if cs:
            def bb(c):
                yb = c.get("yes_bid", {}).get("close_dollars")
                ya = c.get("yes_ask", {}).get("close_dollars")
                return f"{yb}/{ya}"
            print(f"       first ts={cs[0]['end_period_ts']} bid/ask={bb(cs[0])} "
                  f"oi={cs[0].get('open_interest_fp')}")
            print(f"       last  ts={cs[-1]['end_period_ts']} bid/ask={bb(cs[-1])} "
                  f"oi={cs[-1].get('open_interest_fp')}")
            vol = sum(float(c.get("volume_fp", 0)) for c in cs)
            print(f"       total volume over window = {vol}")

    print()
    print("=" * 78)
    print("B. MARKET OBJECT ON SETTLED MARKETS (result / expiration_value)")
    print("=" * 78)
    for tk, _, _, _ in LOSERS[:3]:
        st, d = get(f"{ROOT}/markets/{tk}")
        if isinstance(d, dict) and "market" in d:
            m = d["market"]
            print(f"  {st} {tk:<30} status={m.get('status')} result={m.get('result')} "
                  f"exp_val={m.get('expiration_value')} vol={m.get('volume_fp')} "
                  f"oi={m.get('open_interest_fp')}")
        else:
            print(f"  {st} {tk} -> {str(d)[:160]}")

    print()
    print("=" * 78)
    print("C. TAPE ON SETTLED MARKETS — how far back does /markets/trades reach?")
    print("=" * 78)
    for tk, _, _, _ in LOSERS[:3]:
        st, d = get(f"{ROOT}/markets/trades?ticker={tk}&limit=1000")
        rows = d.get("trades", []) if isinstance(d, dict) else []
        print(f"  {st} {tk:<30} n={len(rows)}")
        if rows:
            t = sorted(x["created_time"] for x in rows)
            print(f"       {t[0]} .. {t[-1]}")

    print()
    print("=" * 78)
    print("D. min_ts / max_ts SEMANTICS on /markets/trades (limit=1000 this time)")
    print("=" * 78)
    tk = LOSERS[0][0]
    now = int(time.time())
    tests = [
        ("no filter", f"ticker={tk}&limit=1000"),
        ("min_ts=07-22T00Z", f"ticker={tk}&limit=1000&min_ts={ts('2026-07-22T00:00:00Z')}"),
        ("window 07-22 15-18Z", f"ticker={tk}&limit=1000"
                                f"&min_ts={ts('2026-07-22T15:00:00Z')}"
                                f"&max_ts={ts('2026-07-22T18:00:00Z')}"),
        ("venue 24h ago 1h window", f"limit=1000&min_ts={now-90000}&max_ts={now-86400}"),
        ("venue 7d ago 1h window", f"limit=1000&min_ts={now-608400}&max_ts={now-604800}"),
    ]
    for label, q in tests:
        st, d = get(f"{ROOT}/markets/trades?{q}")
        rows = d.get("trades", []) if isinstance(d, dict) else []
        t = sorted(x["created_time"] for x in rows) if rows else []
        print(f"  {st}  {label:<26} n={len(rows):<5} "
              f"{t[0] if t else '-'} .. {t[-1] if t else '-'}")
        if isinstance(d, str):
            print(f"       -> {d[:180]}")

    print()
    print("=" * 78)
    print("E. HOW FAR BACK DO CANDLESTICKS GO? (a long-lived gas contract)")
    print("=" * 78)
    for tk in CONTROLS:
        s = tk.split("-")[0]
        st, d = get(f"{ROOT}/series/{s}/markets/{tk}/candlesticks"
                    f"?start_ts={now-30*86400}&end_ts={now}&period_interval=60")
        cs = d.get("candlesticks", []) if isinstance(d, dict) else []
        print(f"  {st} {tk:<28} hourly candles over 30d = {len(cs)}")
        if cs:
            print(f"       first {datetime.fromtimestamp(cs[0]['end_period_ts'], timezone.utc)}"
                  f"  last {datetime.fromtimestamp(cs[-1]['end_period_ts'], timezone.utc)}")
        if isinstance(d, str):
            print(f"       -> {d[:180]}")

    print()
    print("=" * 78)
    print("F. MAX CANDLESTICK WINDOW at interval=1 (rate/range limits)")
    print("=" * 78)
    tk = CONTROLS[2]
    s = tk.split("-")[0]
    for hours in [6, 24, 48, 168, 720]:
        st, d = get(f"{ROOT}/series/{s}/markets/{tk}/candlesticks"
                    f"?start_ts={now-hours*3600}&end_ts={now}&period_interval=1")
        cs = d.get("candlesticks", []) if isinstance(d, dict) else []
        print(f"  {st}  window={hours:>4}h  n={len(cs)}  "
              f"{str(d)[:120] if isinstance(d, str) else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
