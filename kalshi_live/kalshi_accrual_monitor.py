#!/usr/bin/env python3
"""KALSHI ACCRUAL MONITOR — read-only view of the venue's own per-user reward estimates.

Reads the estimates tape (kalshi_estimates_recorder output, ~5-min rows) + program map and
prints, per active program: accrued $, deltas, implied $/day rate, period countdown, and
$1-cliff status. NO orders, NO writes outside --json output path, NO API calls — tape only.

GAUGE CAVEATS (print with every output; Rule Twelve — the gauge's own blindness):
  * VENUE ESTIMATE, NOT BANKED — payout is an end-of-period share ratio; the value CAN
    DECREASE when rivals add depth (25 decreases measured on this tape 08-06..09-01,
    up to -12% in 5 min). A shown $ is not owed until the period concludes >= $1.00.
  * $1.00/PROGRAM-PERIOD FLOOR — sub-$1 at conclusion pays exactly $0 (canon 38/38).
  * FEED CADENCE — ~3 value-changes/program/day, batched, 1-2h lag pattern; sub-day
    rates are noisy. Concluded programs VANISH from the live feed (read tape history).
"""
import argparse
import glob
import gzip
import json
import os
from datetime import datetime, timezone

LIVE_DIR = os.environ.get("KALSHI_LIVE_DIR", "/opt/pa2-maker-kalshi-live")

CAVEATS = [
    "VENUE ESTIMATE, NOT BANKED - share ratio; can DECREASE on rival depth (25 drops measured 08-06..09-01)",
    "$1.00/program-period FLOOR - sub-$1 at conclusion pays $0 (canon 38/38 exact)",
    "~3 updates/program/day, batched, 1-2h lag - sub-day rates are noisy; concluded programs vanish from live feed",
]


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_series(hours):
    """program_id -> sorted [(ts_dt, dollars)] over the trailing window, from all tape files."""
    now = datetime.now(timezone.utc)
    series = {}
    for f in sorted(glob.glob(os.path.join(LIVE_DIR, "estimates-2026*.jsonl*"))):
        op = gzip.open if f.endswith(".gz") else open
        try:
            with op(f, "rt") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        ts = parse_ts(r["ts"])
                    except Exception:
                        continue
                    if (now - ts).total_seconds() > hours * 3600:
                        continue
                    for e in r.get("estimates", []):
                        pid, cc = e.get("program_id"), e.get("reward_centicents")
                        if pid is None or cc is None:
                            continue
                        series.setdefault(pid, []).append((ts, cc / 10000.0))
        except OSError:
            continue
    for pid in series:
        series[pid].sort(key=lambda x: x[0])
    return series, now


def delta_over(pts, now, hours):
    """value change over the trailing sub-window, or None if no earlier point exists."""
    cutoff = now.timestamp() - hours * 3600
    older = [v for t, v in pts if t.timestamp() <= cutoff]
    if not older:
        return None
    return pts[-1][1] - older[-1]


def build_rows(series, now, pmap):
    rows = []
    for pid, pts in series.items():
        last_ts, last_v = pts[-1]
        meta = pmap.get(pid) or {}
        ticker = meta.get("market_ticker") or pid[:8] + ".."
        pool = (meta.get("period_reward") or 0) / 10000.0
        end = None
        try:
            end = parse_ts(meta["end_date"]) if meta.get("end_date") else None
        except Exception:
            pass
        hours_left = (end - now).total_seconds() / 3600.0 if end else None
        ended = hours_left is not None and hours_left <= 0
        d24 = delta_over(pts, now, 24.0)
        rate = d24 if d24 is not None else None  # $/day from the 24h delta
        proj = None
        if not ended and rate is not None and hours_left is not None:
            proj = last_v + max(rate, 0.0) * (hours_left / 24.0)
        dropped = any(b < a for (_, a), (_, b) in zip(pts, pts[1:]))
        if ended:
            status = "CONCLUDED-PAYS" if last_v >= 1.0 else "CONCLUDED-$0"
        elif last_v >= 1.0:
            status = "ABOVE-FLOOR"
        elif proj is None:
            status = "NO-RATE-YET"
        elif proj >= 1.5:
            status = "ON-TRACK(est)"
        elif proj >= 1.0:
            status = "BORDERLINE(est)"
        else:
            status = "SUB-CLIFF(est)"
        rows.append({
            "ticker": ticker, "program_id": pid, "accrued": round(last_v, 4),
            "last_update": last_ts.isoformat(), "pool_usd_day": pool,
            "d1h": delta_over(pts, now, 1.0), "d6h": delta_over(pts, now, 6.0),
            "d24h": d24, "rate_usd_day": rate,
            "hours_left": round(hours_left, 1) if hours_left is not None else None,
            "period_end": end.isoformat() if end else None,
            "projection_est": round(proj, 4) if proj is not None else None,
            "status": status, "decreased_in_window": dropped,
            "n_points": len(pts),
        })
    rows.sort(key=lambda r: -r["accrued"])
    return rows


def latest_cash():
    files = sorted(glob.glob(os.path.join(LIVE_DIR, "cash-2026*.jsonl")))
    if not files:
        return None
    try:
        with open(files[-1], "rt") as fh:
            last = None
            for line in fh:
                if line.strip():
                    last = line
        r = json.loads(last)
        return {"ts": r.get("ts"), "cash": r.get("cash"),
                "n_resting": r.get("n_resting"), "n_positions": r.get("n_positions")}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0, help="tape window (default 72h)")
    ap.add_argument("--json", metavar="PATH", help="also write machine-readable snapshot")
    args = ap.parse_args()

    try:
        pmap = json.load(open(os.path.join(LIVE_DIR, "kalshi_program_map.json")))
    except Exception:
        pmap = {}
    series, now = load_series(args.hours)
    rows = build_rows(series, now, pmap)
    cash = latest_cash()

    active = [r for r in rows if not r["status"].startswith("CONCLUDED")]
    snap = {
        "generated": now.isoformat(), "window_h": args.hours,
        "caveats": CAVEATS, "cash": cash,
        "total_accrued_active": round(sum(r["accrued"] for r in active), 4),
        "n_active": len(active),
        "n_above_floor": sum(1 for r in active if r["accrued"] >= 1.0),
        "rows": rows,
    }
    if args.json:
        tmp = args.json + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snap, fh, indent=1)
        os.replace(tmp, args.json)

    print("KALSHI ACCRUAL MONITOR  %s  (window %.0fh)" % (now.strftime("%Y-%m-%dT%H:%M:%SZ"), args.hours))
    for c in CAVEATS:
        print("  ! " + c)
    if cash:
        print("account: $%.4f cash | %s resting | %s positions  (cash-feed %s)"
              % (cash["cash"], cash["n_resting"], cash["n_positions"], cash["ts"]))
    print("ACTIVE: %d programs | accrued total $%.4f | >=%d at/above $1.00 floor"
          % (len(active), snap["total_accrued_active"], snap["n_above_floor"]))
    hdr = "%-34s %9s %8s %8s %8s %9s %7s %10s  %s"
    print(hdr % ("ticker", "accrued$", "d1h", "d6h", "d24h", "rate$/d", "hrs", "proj$(est)", "status"))
    for r in rows:
        def fd(x):
            return ("%+.4f" % x) if x is not None else "-"
        print(hdr % (r["ticker"][:34], "%.4f" % r["accrued"], fd(r["d1h"]), fd(r["d6h"]),
                     fd(r["d24h"]), fd(r["rate_usd_day"]),
                     ("%.0f" % r["hours_left"]) if r["hours_left"] is not None else "-",
                     ("%.2f" % r["projection_est"]) if r["projection_est"] is not None else "-",
                     r["status"] + (" [DROPPED]" if r["decreased_in_window"] else "")))


if __name__ == "__main__":
    main()
