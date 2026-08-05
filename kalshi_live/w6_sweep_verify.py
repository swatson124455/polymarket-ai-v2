#!/usr/bin/env python3
"""W6 LIVE VERIFICATION (restart checklist item) — prove the widened measurement path
works end-to-end on the running bot, from its own telemetry. Read-only.

Checks, from the newest plans-YYYYMMDD.jsonl rows after --since:
  scored     scored_markets grows toward the active-program universe (coverage)
  fresh      score_age_p50_m / p90_m fall as the sweeper walks (freshness); the 07-30
             baseline this fixes: cache median age ~3.6 days, 9% under 30 min
  consuming  pcap_age_p50_m present and bounded (ALLOC merge consuming sweeper rows)
Prints each metric's first and latest post-restart values; exit 0 when the latest row has
scored_markets >= --min-scored AND score_age_p50_m <= --max-p50-min, else exit 1.
Run:  sudo ./venv/bin/python w6_sweep_verify.py --since <restart-iso> [--max-p50-min 180]
"""
import argparse
import datetime as dt
import glob
import json
import os
import subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--min-scored", type=int, default=1000)
    ap.add_argument("--max-p50-min", type=float, default=180.0)
    a = ap.parse_args()
    since = dt.datetime.fromisoformat(a.since.replace("Z", "+00:00"))
    paths = sorted(glob.glob("/opt/pa2-maker-kalshi-live/plans-*.jsonl"),
                   key=os.path.getmtime)[-2:]
    rows = []
    for p in paths:
        out = subprocess.run(["tail", "-n", "1500", p], capture_output=True,
                             text=True, timeout=15).stdout
        for line in out.splitlines():
            try:
                r = json.loads(line)
                if dt.datetime.fromisoformat(r["ts"]) >= since:
                    rows.append(r)
            except Exception:
                continue
    if not rows:
        print("no post-restart plan rows yet")
        raise SystemExit(1)
    first, last = rows[0], rows[-1]
    for k in ("scored_markets", "score_age_p50_m", "score_age_p90_m",
              "pcap_age_p50_m", "footprint", "quoted_markets"):
        print(f"{k:18s} first={first.get(k)}  latest={last.get(k)}")
    ok = ((last.get("scored_markets") or 0) >= a.min_scored
          and (last.get("score_age_p50_m") or 1e9) <= a.max_p50_min)
    print("W6 VERIFY:", "PASS" if ok else
          f"NOT YET (need scored>={a.min_scored} and p50<={a.max_p50_min}m; "
          f"the sweeper's full pass over ~4.3k programs takes ~72 min at 1 read/s)")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
