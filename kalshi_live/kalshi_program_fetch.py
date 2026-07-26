#!/usr/bin/env python3
"""FETCH + CACHE the full /incentive_programs universe (read-only, no keys, never trades).

WHY A SEPARATE FETCHER: the coverage analysis must run many times against a FIXED
snapshot, and the endpoint is ~5 pages of 10,000 rows. Fetch once, cache to disk,
analyse offline.

METHOD NOTE (measured 2026-07-23, probe output in kalshi_program_probe.py):
  * limit=1000 silently caps the page at 1000 rows. limit=10000 returns 10000. Known defect
    in the older scripts here; this file uses 10000.
  * The `status` query param is IGNORED for any value the server does not recognise:
    status=zzzgarbage, status='' and status=ACTIVE all return the SAME first page as no
    filter at all (10000 rows + cursor). Only `active` (2,329 rows, no cursor) and
    `closed` (1,392 rows, no cursor) came back distinct. `scheduled` returned a full
    10,000-row page WITH a cursor, i.e. indistinguishable from unfiltered on page 1 --
    this fetcher paginates BOTH `scheduled` and a deliberate garbage status to settle
    whether `scheduled` is honoured or ignored. Do not assume; the answer is printed.

Writes:  kalshi_live/program_universe_<UTCSTAMP>.jsonl   (one program row per line)
         kalshi_live/program_universe_latest.json        (pointer + fetch metadata)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
SPACE_S = 0.35
PAGE = 10000
MAX_PAGES = 40
_last = [0.0]


def get(path):
    dt = time.time() - _last[0]
    if dt < SPACE_S:
        time.sleep(SPACE_S - dt)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-coverage-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    _last[0] = time.time()
    return out


def paginate(status, label):
    rows, cur, pages = [], "", 0
    for _ in range(MAX_PAGES):
        q = f"/incentive_programs?limit={PAGE}"
        if status is not None:
            q += f"&status={status}"
        if cur:
            q += f"&cursor={cur}"
        d = get(q)
        got = d.get("incentive_programs") or []
        rows.extend(got)
        pages += 1
        cur = d.get("next_cursor") or ""
        print(f"  [{label}] page {pages}: +{len(got)} -> {len(rows)}", flush=True)
        if not cur or not got:
            break
    return rows, pages


def key(p):
    return (p.get("market_ticker"), p.get("start_date"), p.get("end_date"), p.get("id"))


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"fetch start {stamp}")

    print("paginating status=scheduled ...")
    sched, sp = paginate("scheduled", "scheduled")
    print("paginating status=zzzgarbage (control: unrecognised value) ...")
    ctrl, cp = paginate("zzzgarbage", "control")
    print("fetching status=active ...")
    active, _ = paginate("active", "active")

    ks, kc = {key(p) for p in sched}, {key(p) for p in ctrl}
    honoured = (ks != kc)
    print(f"\nstatus=scheduled distinct from unfiltered control? {honoured}  "
          f"(scheduled={len(ks)} unique, control={len(kc)} unique, "
          f"overlap={len(ks & kc)})")

    # union everything we have; dedupe by program identity
    merged = {}
    for src, rowset in (("scheduled", sched), ("control", ctrl), ("active", active)):
        for p in rowset:
            k = key(p)
            if k not in merged:
                q = dict(p)
                q["_src"] = src
                merged[k] = q
            elif src == "active":
                merged[k]["_active_now"] = True
    for p in active:
        merged[key(p)]["_active_now"] = True

    path = os.path.join(HERE, f"program_universe_{stamp}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for p in merged.values():
            fh.write(json.dumps(p, sort_keys=True) + "\n")
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "file": os.path.basename(path),
        "n_unique_programs": len(merged),
        "n_scheduled_rows": len(sched), "scheduled_pages": sp,
        "n_control_rows": len(ctrl), "control_pages": cp,
        "n_active_rows": len(active),
        "scheduled_filter_honoured": honoured,
        "page_limit": PAGE,
    }
    with open(os.path.join(HERE, "program_universe_latest.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    sys.exit(main() or 0)
