#!/usr/bin/env python3
"""BOOK SHAPE PROBE -- NEW FILE, READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

kalshi_field_data_probe.py got an EMPTY orderbook object with ?depth=100. Before concluding
anything about microprice/depth-imbalance computability, verify whether that is a real empty
book or a bad request. Tries: no param, depth=10, depth=32, and a high-volume non-allowlist
market as a control. Also dumps the recorder's FROZEN book sample for the true field shape.
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "kalshi-book-shape-probe/1.0 (read-only measurement)"}


def get(path):
    time.sleep(0.35)
    req = urllib.request.Request(PUB + path, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    d = get("/markets?series_ticker=KXAAAGASD&status=open&limit=200")
    ms = d.get("markets") or []
    print(f"markets returned: {len(ms)}")
    if ms:
        print("market object keys:", sorted(ms[0].keys()))
        print("sample market:", json.dumps(ms[0])[:900])
    # pick the busiest by whatever volume field exists
    def vol(m):
        for k in ("volume_fp", "volume", "volume_24h_fp", "volume_24h"):
            v = m.get(k)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 0.0
    ms.sort(key=lambda m: -vol(m))
    cands = [m["ticker"] for m in ms[:3]]

    for t in cands:
        for q in ("", "?depth=10", "?depth=32"):
            try:
                ob = get(f"/markets/{t}/orderbook{q}")
            except Exception as e:
                print(f"  {t}{q}: ERROR {e!r}")
                continue
            print(f"\n  {t}{q}")
            print("    raw:", json.dumps(ob)[:800])

    print("\n--- FROZEN recorder sample (concentration_samples.jsonl), first row ---")
    p = os.path.join(HERE, "concentration_samples.jsonl")
    if os.path.exists(p):
        with open(p) as fh:
            line = fh.readline()
        obj = json.loads(line)
        print("keys:", sorted(obj.keys()))
        print(json.dumps(obj)[:1500])
    else:
        print("MISSING", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
