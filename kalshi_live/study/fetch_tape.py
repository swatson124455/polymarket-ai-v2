#!/usr/bin/env python3
"""Fetch the PUBLIC trade tape for every ticker in the frozen telemetry window.

Window = the telemetry window, 2026-07-26T00:59:58Z .. 04:35:01Z, plus a 2h tail so
adverse-selection can be measured AFTER each fill.

Output: tape_frozen.jsonl (one trade per line). Public data only, no auth, no orders.
"""
import json, urllib.request, time, sys, os
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
W_START = datetime(2026, 7, 26, 0, 59, 58, tzinfo=timezone.utc)
W_END = datetime(2026, 7, 26, 6, 40, 0, tzinfo=timezone.utc)   # window + 2h tail
MIN_TS = int(W_START.timestamp())
MAX_TS = int(W_END.timestamp())


def get(path, tries=5):
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "tape/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            time.sleep(1.0 + 1.5 * a)
    raise last


tickers = sorted({json.loads(l)["ticker"] for l in open("quotes_frozen.jsonl")})
print("TICKERS", len(tickers), file=sys.stderr)

done = set()
if os.path.exists("tape_progress.txt"):
    done = set(open("tape_progress.txt").read().split())
    print("RESUME, already done:", len(done), file=sys.stderr)

out = open("tape_frozen.jsonl", "a")
prog = open("tape_progress.txt", "a")
n_tr = 0
for i, t in enumerate(tickers):
    if t in done:
        continue
    cursor = ""
    got = 0
    while True:
        p = f"/markets/trades?ticker={t}&limit=1000&min_ts={MIN_TS}&max_ts={MAX_TS}"
        if cursor:
            p += f"&cursor={cursor}"
        try:
            d = get(p)
        except Exception as e:
            print("FAIL", t, str(e)[:80], file=sys.stderr)
            break
        tr = d.get("trades", [])
        for x in tr:
            out.write(json.dumps(x) + "\n")
        got += len(tr)
        cursor = d.get("cursor") or ""
        if not cursor or not tr:
            break
        time.sleep(0.12)
    n_tr += got
    prog.write(t + "\n")
    prog.flush()
    out.flush()
    if i % 40 == 0:
        print(f"  {i}/{len(tickers)} trades so far {n_tr}", file=sys.stderr)
    time.sleep(0.12)
print("TOTAL_TRADES", n_tr, file=sys.stderr)
