#!/usr/bin/env python3
"""Did the CLEAN roster actually trade? — read-only data-api probe.

WHY (2026-07-12): the shadow watcher logged ZERO records in its first ~30h.
Two hypotheses: (a) the 16 human traders were genuinely quiet, (b) the
watcher is silently broken (roster/filter mismatch). This probe settles it
from the API side: per roster address, recent trade count and the age of
the newest trade. If traders show BUYs inside the watcher's uptime window
and the shadow log stayed empty, the watcher has a bug; if they were
quiet, the shadow is fine and the answer is patience.

STDLIB ONLY (urllib) — runs with the system python3, no venv, no repo
imports, no DB, no keys. Rate-limited politely (0.3s between addresses).

INVOCATION (VPS or anywhere with internet):
    python3 scripts/roster_activity_check.py \
        --roster /opt/pa2-shared/mb_copyable_data/chain_audit.json --hours 40
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

DATA_API = "https://data-api.polymarket.com/activity"


def fetch_activity(addr: str, limit: int, timeout_s: float) -> list[dict]:
    url = DATA_API + "?" + urllib.parse.urlencode(
        {"user": addr, "limit": limit, "type": "TRADE"})
    # the data-api 403s urllib's default UA (probe-confirmed 2026-07-12);
    # same UA the repo's client sends (polymarket_client.py:203)
    req = urllib.request.Request(url, headers={
        "User-Agent": "PolymarketAI/1.0 (https://github.com; data)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        blob = json.load(r)
    return blob if isinstance(blob, list) else []


def summarize(rows: list[dict], now: float, hours: float) -> dict:
    trades = [r for r in rows
              if str(r.get("type", "")).upper() == "TRADE"
              and r.get("timestamp") is not None]
    recent = [r for r in trades
              if now - float(r["timestamp"]) < hours * 3600]
    newest = max((float(r["timestamp"]) for r in trades), default=None)
    return {
        "recent": len(recent),
        "recent_buys": sum(1 for r in recent
                           if str(r.get("side", "")).upper() == "BUY"),
        "newest_age_h": (now - newest) / 3600 if newest else None,
        "fetched": len(trades),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Recent data-api activity for the CLEAN roster")
    ap.add_argument("--roster", required=True,
                    help="chain_audit.json with a top-level clean[] list")
    ap.add_argument("--hours", type=float, default=40.0)
    ap.add_argument("--limit", type=int, default=50,
                    help="activity rows fetched per address (newest-first)")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    with open(args.roster) as f:
        roster = [str(a).lower() for a in json.load(f).get("clean", [])]
    if not roster:
        print("no clean[] addresses in the roster json", file=sys.stderr)
        return 2

    now = time.time()
    total_recent = total_buys = errors = 0
    print(f"data-api activity, last {args.hours:.0f}h, {len(roster)} traders "
          f"(limit {args.limit}/addr — a full-limit row may undercount)")
    for a in roster:
        try:
            rows = fetch_activity(a, args.limit, args.timeout)
        except Exception as e:
            errors += 1
            print(f"  {a[:14]}…  ERROR {e!r}")
            time.sleep(0.3)
            continue
        s = summarize(rows, now, args.hours)
        total_recent += s["recent"]
        total_buys += s["recent_buys"]
        cap = " (AT LIMIT — undercount)" if s["fetched"] >= args.limit and \
            s["recent"] == s["fetched"] else ""
        age = (f"{s['newest_age_h']:.1f}h ago" if s["newest_age_h"] is not None
               else "none returned")
        print(f"  {a[:14]}…  last{args.hours:.0f}h: {s['recent']:>3} trades "
              f"({s['recent_buys']} BUY)  newest: {age}{cap}")
        time.sleep(0.3)

    print(f"\nTOTAL last {args.hours:.0f}h: {total_recent} trades "
          f"({total_buys} BUYs) across {len(roster)} traders; "
          f"{errors} fetch errors")
    print("READ: BUYs > 0 inside the watcher's uptime with an EMPTY shadow "
          "log = watcher bug. Zero BUYs = quiet roster, shadow is fine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
