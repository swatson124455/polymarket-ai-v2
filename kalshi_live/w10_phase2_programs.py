#!/usr/bin/env python3
"""W10 PHASE 2 — harvest CLOSED incentive programs for every event the account traded,
then intersect program windows with our measured presence.

Discovery (probed 2026-08-04): /trade-api/v2/incentive_programs?status=closed returns
ended programs with start_date / end_date / period_reward / paid_out / market_ticker.
Ticker/series filter params are silently IGNORED by the endpoint, so this paginates the
whole closed listing newest-first and filters client-side, stopping once a page's newest
end_date is older than the account's first trade (2026-07-19 cutoff).

Per event this yields, for every hypothesis test downstream:
  * had_program        — was there ANY program overlapping our resting presence?
  * pool_usd_day       — period_reward/10000 (CANON: the daily per-market pool)
  * presence_in_window — our resting seconds inside program windows (union, no dbl-count)
  * ub_accrual_usd     — pool x (presence_in_window / window_len), summed over programs:
                         the accrual UPPER BOUND at 100% share. Below $1 -> the $1
                         minimum-credit floor explains $0.00 regardless of share (H1).
  * paid_out flags     — the venue's own program-level payout marker.

Read-only. Writes only under --outdir (default /tmp/w10).
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_presence_calibrate import _iso  # noqa: E402

CUTOFF = dt.datetime(2026, 7, 19, tzinfo=dt.timezone.utc)


def harvest_closed(public_get, want_prefixes):
    """Paginate status=closed newest-first; keep rows whose market_ticker matches any
    wanted event prefix; stop when a full page is older than CUTOFF."""
    kept, cursor, pages, newest_seen, oldest_seen = [], "", 0, None, None
    while pages < 400:
        pages += 1
        d = public_get("/trade-api/v2/incentive_programs?status=closed&limit=1000"
                       + (f"&cursor={cursor}" if cursor else ""))
        rows = d.get("incentive_programs") or []
        if not rows:
            break
        ends = [_iso(r["end_date"]) for r in rows if r.get("end_date")]
        if ends:
            newest_seen = max(filter(None, [newest_seen, max(ends)]))
            oldest_seen = min(filter(None, [oldest_seen, min(ends)]))
        for r in rows:
            t = r.get("market_ticker") or ""
            if any(t == p or t.startswith(p + "-") for p in want_prefixes):
                kept.append(r)
        if pages % 10 == 0:
            print(f"  page {pages}: kept={len(kept)} oldest_end={oldest_seen}",
                  file=sys.stderr)
        if ends and max(ends) < CUTOFF:
            break
        cursor = d.get("next_cursor") or d.get("cursor") or ""
        if not cursor:
            break
        time.sleep(0.25)
    return kept, {"pages": pages, "newest_end": str(newest_seen),
                  "oldest_end": str(oldest_seen), "kept": len(kept),
                  "hit_cutoff": bool(oldest_seen and oldest_seen < CUTOFF)}


def overlap_seconds(iv_a, windows):
    """Seconds of interval-list iv_a falling inside any of the windows (both lists of
    (start,end) datetimes). iv_a is assumed already-unioned per market."""
    total = 0.0
    for a, b in iv_a:
        for wa, wb in windows:
            lo, hi = max(a, wa), min(b, wb)
            if hi > lo:
                total += (hi - lo).total_seconds()
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/w10")
    ap.add_argument("--snapshot", required=True, help="phase-1 frozen snapshot json")
    a = ap.parse_args()
    import maker_kalshi_quoter as q

    with open(a.snapshot) as fh:
        raw = json.load(fh)
    with open(os.path.join(a.outdir, "w10_event_table.json")) as fh:
        table = json.load(fh)

    events = {}
    events.update(table["zero_payers"])
    events.update(table["paid_events"])
    want = set(events.keys())

    progs, stats = harvest_closed(q.public_get, want)
    read_ts = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(os.path.join(a.outdir, "w10_programs_closed.json"), "w") as fh:
        json.dump({"read_ts": read_ts, "stats": stats, "programs": progs}, fh, indent=1)
    print(f"harvest: {stats}")

    # union-ed resting intervals per MARKET from the frozen phase-1 orders
    per_mkt = {}
    for o in raw["orders"]:
        t = o.get("ticker")
        if not t:
            continue
        try:
            iv = (_iso(o["created_time"]), _iso(o["last_update_time"]))
        except Exception:
            continue
        if (iv[1] - iv[0]).total_seconds() < 0:
            continue
        per_mkt.setdefault(t, []).append(iv)

    def merged(ivs):
        ivs = sorted(ivs)
        out = [list(ivs[0])]
        for s, e in ivs[1:]:
            if s <= out[-1][1]:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([s, e])
        return [(s, e) for s, e in out]

    prog_by_mkt = {}
    for r in progs:
        prog_by_mkt.setdefault(r.get("market_ticker"), []).append(r)

    result = {}
    for ev, e in events.items():
        row = {"credit_usd": e["credit_usd"], "covered_h": e["covered_h"],
               "n_programs": 0, "had_program": False, "presence_in_window_h": 0.0,
               "ub_accrual_usd": 0.0, "pools_usd_day": [], "paid_out_flags": [],
               "windows": []}
        for t in e["tickers"]:
            ivs = merged(per_mkt.get(t, []))
            for r in prog_by_mkt.get(t, []):
                try:
                    wa, wb = _iso(r["start_date"]), _iso(r["end_date"])
                except Exception:
                    continue
                wlen = (wb - wa).total_seconds()
                if wlen <= 0:
                    continue
                ov = overlap_seconds(ivs, [(wa, wb)])
                pool_day = float(r.get("period_reward") or 0) / 10000.0
                pool_window = pool_day * (wlen / 86400.0)
                row["n_programs"] += 1
                row["pools_usd_day"].append(round(pool_day, 2))
                row["paid_out_flags"].append(bool(r.get("paid_out")))
                row["windows"].append({"t": t, "start": r["start_date"],
                                       "end": r["end_date"],
                                       "overlap_h": round(ov / 3600.0, 3)})
                if ov > 0:
                    row["had_program"] = True
                    row["presence_in_window_h"] += ov / 3600.0
                    row["ub_accrual_usd"] += pool_window * (ov / wlen)
        row["presence_in_window_h"] = round(row["presence_in_window_h"], 3)
        row["ub_accrual_usd"] = round(row["ub_accrual_usd"], 4)
        result[ev] = row

    out = {"schema": 1, "read_ts": read_ts, "harvest_stats": stats, "events": result}
    path = os.path.join(a.outdir, "w10_phase2_join.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)

    zp = [(ev, r) for ev, r in result.items() if r["credit_usd"] == 0]
    pd = [(ev, r) for ev, r in result.items() if r["credit_usd"] > 0]
    print(f"\nZERO-PAYERS ({len(zp)}):")
    for ev, r in sorted(zp, key=lambda kv: -kv[1]["covered_h"]):
        print(f"  {ev:32s} rest={r['covered_h']:7.2f}h inwin={r['presence_in_window_h']:7.2f}h "
              f"progs={r['n_programs']:2d} ub=${r['ub_accrual_usd']:8.4f} "
              f"paid_out={sum(r['paid_out_flags'])}/{len(r['paid_out_flags'])}")
    print(f"\nPAID ({len(pd)}):")
    for ev, r in sorted(pd, key=lambda kv: -kv[1]["credit_usd"]):
        print(f"  {ev:32s} credit=${r['credit_usd']:6.2f} rest={r['covered_h']:7.2f}h "
              f"inwin={r['presence_in_window_h']:7.2f}h ub=${r['ub_accrual_usd']:8.4f} "
              f"paid_out={sum(r['paid_out_flags'])}/{len(r['paid_out_flags'])}")
    print("wrote", path)


if __name__ == "__main__":
    main()
