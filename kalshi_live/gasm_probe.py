#!/usr/bin/env python3
"""KXAAAGASM DILIGENCE PROBE — READ-ONLY, public API only, no keys, never trades.

Step 1: structure. Pull the series record, its events, and every market, and
dump raw so classification is done off ACTUAL tickers/titles/rules, not a
numeric-suffix heuristic (canon §M5 blocker 3).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

PUB = "https://api.elections.kalshi.com/trade-api/v2"
HERE = os.path.dirname(os.path.abspath(__file__))
SPACING_S = 0.35
_last = [0.0]


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "gasm-diligence/1.0 (read-only)"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            _last[0] = time.time()
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        _last[0] = time.time()
        return {"__http_error__": e.code, "__body__": e.read()[:400].decode("utf8", "replace")}


def paginate(path, key, cap=20):
    out, cur = [], ""
    for _ in range(cap):
        sep = "&" if "?" in path else "?"
        d = get(path + (f"{sep}cursor={cur}" if cur else ""))
        if "__http_error__" in d:
            return out, d
        rows = d.get(key) or []
        out += rows
        cur = d.get("next_cursor") or ""
        if not cur or not rows:
            break
    return out, None


def main():
    ser = sys.argv[1] if len(sys.argv) > 1 else "KXAAAGASM"
    res = {}
    res["series"] = get(f"/series/{ser}")
    ev, err = paginate(f"/events?series_ticker={ser}&status=open&limit=200", "events")
    res["events_open"] = ev
    res["events_err"] = err
    ev_all, err2 = paginate(f"/events?series_ticker={ser}&limit=200", "events")
    res["events_all"] = ev_all
    mk, err3 = paginate(f"/markets?series_ticker={ser}&limit=1000", "markets")
    res["markets"] = mk
    res["markets_err"] = err3
    out = os.path.join(HERE, f"gasm_raw_{ser}.json")
    json.dump(res, open(out, "w"), indent=1)
    print(f"series rec keys: {list(res['series'].keys())}")
    print(json.dumps(res["series"], indent=1)[:2500])
    print(f"\nevents open={len(ev)} all={len(ev_all)} markets={len(mk)}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
