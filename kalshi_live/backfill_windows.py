#!/usr/bin/env python3
"""Backfill Time Period start/end onto concentration samples captured before the
sampler recorded them. Windows are a property of the PROGRAM (stable per ticker),
so a single live lookup is a faithful backfill, not an approximation."""
import json, sys, time, urllib.request

def get(p):
    r = urllib.request.Request("https://api.elections.kalshi.com/trade-api/v2" + p,
                               headers={"User-Agent": "backfill/1.0"})
    return json.loads(urllib.request.urlopen(r, timeout=25).read())

progs, cur = [], ""
for _ in range(8):
    d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
    progs += d.get("incentive_programs") or []
    cur = d.get("next_cursor") or ""
    if not cur:
        break
    time.sleep(0.35)
win = {p["market_ticker"]: (p["start_date"], p["end_date"]) for p in progs}
print(f"program windows known: {len(win)}")

path = sys.argv[1] if len(sys.argv) > 1 else "concentration_samples.jsonl"
out, filled, missing = [], 0, 0
for line in open(path):
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue
    for r in d.get("rows", []):
        if not r.get("start"):
            w = win.get(r["t"])
            if w:
                r["start"], r["end"] = w
                filled += 1
            else:
                missing += 1
    out.append(d)
with open(path, "w") as fh:
    for d in out:
        fh.write(json.dumps(d, separators=(",", ":")) + "\n")
print(f"filled {filled} rows, {missing} still missing a window")
