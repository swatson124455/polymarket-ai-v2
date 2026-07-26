#!/usr/bin/env python3
"""PROBE (read-only, no keys): what does /incentive_programs accept and return?

Answers three things before any calendar is built:
  1. which `status` values the endpoint accepts (active / scheduled / others),
  2. whether limit=10000 actually returns >1000 rows in one page (the limit=1000
     truncation defect is why every other script here under-counts),
  3. the raw field set on a program row, so the calendar code does not guess.

NEVER trades. NEVER writes to any frozen dataset.
"""
import json
import sys
import time
import urllib.error
import urllib.request

PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE_S = 0.35
_last = [0.0]


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACE_S:
        time.sleep(SPACE_S - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-coverage-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            _last[0] = time.time()
            return r.status, json.loads(body)
    except urllib.error.HTTPError as e:
        _last[0] = time.time()
        return e.code, e.read()[:300].decode("utf-8", "replace")


def main():
    for st in ["active", "scheduled", "finalized", "closed", "ended", "expired", "inactive", "all"]:
        code, d = get(f"/incentive_programs?status={st}&limit=10000")
        if isinstance(d, dict):
            rows = d.get("incentive_programs") or []
            print(f"status={st:<10} HTTP {code}  rows={len(rows):<6} next_cursor={'Y' if d.get('next_cursor') else 'n'}")
        else:
            print(f"status={st:<10} HTTP {code}  ERR {d[:160]}")

    code, d = get("/incentive_programs?status=scheduled&limit=1000")
    if isinstance(d, dict):
        print(f"\nlimit=1000 page size: {len(d.get('incentive_programs') or [])}")
    code, d = get("/incentive_programs?status=scheduled&limit=10000")
    if isinstance(d, dict):
        rows = d.get("incentive_programs") or []
        print(f"limit=10000 page size: {len(rows)}")
        if rows:
            print("\nsample row:")
            print(json.dumps(rows[0], indent=2)[:1200])


if __name__ == "__main__":
    sys.exit(main() or 0)
