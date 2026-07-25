#!/usr/bin/env python3
"""PRESENCE CALIBRATION — measure time-in-book and what it costs, and emit the table the quoter
navigates by.

WHY THIS EXISTS
LIP takes a snapshot of the book every second and SUMS the scores, so reward is proportional to
size x SECONDS RESTING. Every estimate the bot makes today is instantaneous — it computes a share
and multiplies by a whole day's pool, i.e. it assumes we are present 100% of the window. Measured
from the venue's own order history (980 orders / 91 markets, 2026-07-20..07-25), the real figure is
a MEDIAN 5.7% presence (mean 11.9%) across all 91 markets. That is not a small correction.

WHAT IT MEASURES (all read-only, all from /portfolio/orders which carries created_time and
last_update_time per order):
  presence  = union of our resting intervals in a market / that market's own open->close life.
              UNION, not sum: two orders resting simultaneously is one second of presence, not two.
  cost/hour = realized fill cost attributable to an hour of resting, so "be present longer" can be
              judged against what presence COSTS rather than assumed free.

THE TWO MULTIPLIERS ARE DELIBERATELY SEPARATE — they are not the same kind of number:
  window_fraction_remaining  STRUCTURAL. Known exactly at quote time from the program's own clock.
                             Entering a window 80% through caps our score at ~20% no matter how
                             well we execute. Nothing to calibrate, nothing to second-guess.
  presence_factor            EMPIRICAL. How much of the time we actually stay resting while we
                             intend to. This one partly encodes OUR OWN DEFECTS (42% of orders
                             historically died inside a single 120s cycle).
Keeping them apart matters: multiplying by a defect-contaminated presence_factor makes the bot
avoid markets because WE execute badly there, which is a death spiral — it never re-enters, so the
factor never improves. So the table records both, and the gate is required to log what it would
have decided at presence_factor=1.0. That exposes how much of any skip is the market versus us.

FAIL-OPEN everywhere: a missing or corrupt table yields {} -> the caller treats every family as
uncalibrated and must fall back to its own default. A calibration file can never block trading.
"""
import collections
import datetime as dt
import json
import statistics as st

SCHEMA = 1
# Presence depends far more on how LONG a market lives than on which series it is: measured, short
# markets (~16h life) ran 24-82% presence while 700h+ markets ran 1-2%. So bucket by life, and keep
# family only as a secondary key.
LIFE_BUCKETS = ((0, 6, "0-6h"), (6, 24, "6-24h"), (24, 96, "1-4d"),
                (96, 336, "4-14d"), (336, 1e9, "14d+"))
FAMILY_RULES = (("KXAAAGAS", "gas"), ("KXTEMP", "temp"))


def family_of(ticker):
    t = ticker or ""
    for prefix, fam in FAMILY_RULES:
        if t.startswith(prefix):
            return fam
    return t.split("-")[0]


def life_bucket(life_hours):
    for lo, hi, name in LIFE_BUCKETS:
        if lo <= life_hours < hi:
            return name
    return "14d+"


def _iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def union_seconds(intervals):
    """Seconds during which AT LEAST ONE order was resting. Overlapping orders do not double-count —
    presence is wall-clock coverage, not order-hours."""
    ivs = sorted((a, b) for a, b in intervals if b >= a)
    if not ivs:
        return 0.0
    merged = [list(ivs[0])]
    for a, b in ivs[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return sum((b - a).total_seconds() for a, b in merged)


def measure(orders, market_lives):
    """orders: raw /portfolio/orders rows. market_lives: {ticker: open->close seconds}.
    Returns the calibration dict. Markets with no known life are EXCLUDED from presence (we cannot
    form the fraction) but still counted for cost — dropping them silently from both would bias
    cost toward the markets we happened to resolve."""
    per = collections.defaultdict(lambda: {"iv": [], "fill_cost": 0.0, "fees": 0.0,
                                           "orders": 0, "filled": 0, "short_lived": 0})
    for o in orders:
        try:
            t = o["ticker"]
            a, b = _iso(o["created_time"]), _iso(o["last_update_time"])
            if (b - a).total_seconds() < 0:
                continue
        except Exception:
            continue
        d = per[t]
        d["iv"].append((a, b))
        d["orders"] += 1
        if (b - a).total_seconds() < 120:
            d["short_lived"] += 1
        try:
            if float(o.get("fill_count_fp") or 0) > 0:
                d["filled"] += 1
            d["fill_cost"] += float(o.get("maker_fill_cost_dollars") or 0) \
                + float(o.get("taker_fill_cost_dollars") or 0)
            d["fees"] += float(o.get("maker_fees_dollars") or 0) \
                + float(o.get("taker_fees_dollars") or 0)
        except Exception:
            pass

    markets, buckets = {}, collections.defaultdict(
        lambda: {"presence": [], "hours": 0.0, "fees": 0.0, "orders": 0, "short_lived": 0,
                 "filled": 0, "markets": 0})
    for t, d in per.items():
        cov = union_seconds(d["iv"])
        life = market_lives.get(t)
        row = {"covered_h": round(cov / 3600.0, 4), "orders": d["orders"],
               "short_lived": d["short_lived"], "filled": d["filled"],
               "fees_usd": round(d["fees"], 6)}
        key = None
        if life and life > 0:
            row["life_h"] = round(life / 3600.0, 3)
            row["presence"] = round(cov / life, 6)
            key = (family_of(t), life_bucket(life / 3600.0))
            row["bucket"] = key[1]
        markets[t] = row
        if key:
            b = buckets[key]
            b["presence"].append(row["presence"])
            b["markets"] += 1
        # cost/orders accrue to the family regardless of whether life resolved
        fb = buckets[(family_of(t), row.get("bucket") or "unknown")]
        fb["hours"] += cov / 3600.0
        fb["fees"] += d["fees"]
        fb["orders"] += d["orders"]
        fb["short_lived"] += d["short_lived"]
        fb["filled"] += d["filled"]

    fams = {}
    for (fam, bucket), b in buckets.items():
        if not b["presence"]:
            continue
        fams[f"{fam}|{bucket}"] = {
            "family": fam, "bucket": bucket,
            "presence_median": round(st.median(b["presence"]), 6),
            "presence_mean": round(st.mean(b["presence"]), 6),
            "n_markets": b["markets"],
            "resting_hours": round(b["hours"], 3),
            "fees_usd": round(b["fees"], 6),
            "fees_per_resting_hour": (round(b["fees"] / b["hours"], 6) if b["hours"] > 0 else None),
            "orders": b["orders"],
            "short_lived_orders": b["short_lived"],
            "short_lived_pct": (round(100.0 * b["short_lived"] / b["orders"], 2)
                                if b["orders"] else None),
            "filled_orders": b["filled"],
        }
    allp = [r["presence"] for r in markets.values() if "presence" in r]
    return {"schema": SCHEMA, "n_orders": len(orders), "n_markets": len(markets),
            "n_markets_with_life": len(allp),
            "presence_median_all": round(st.median(allp), 6) if allp else None,
            "presence_mean_all": round(st.mean(allp), 6) if allp else None,
            "families": fams, "markets": markets}


def load_table(path):
    """Fail-OPEN: any error -> {}. A bad calibration file must never block trading."""
    try:
        with open(path) as fh:
            data = json.load(fh)
        if data.get("schema") != SCHEMA:
            return {}
        fams = data.get("families")
        return fams if isinstance(fams, dict) else {}
    except Exception:
        return {}


def write_table(result, out_path):
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)
    return out_path


def presence_for(table, ticker, life_hours, default=1.0):
    """Look up the measured presence for this market's family+life bucket.

    Default is 1.0 = 'assume perfect execution'. That is deliberate: an ABSENT calibration must not
    silently make the bot pessimistic. Only a MEASURED number may lower the estimate."""
    if not table or life_hours is None:
        return default
    fam, bucket = family_of(ticker), life_bucket(life_hours)
    row = table.get(f"{fam}|{bucket}") or table.get(f"*|{bucket}")
    if not row:
        return default
    v = row.get("presence_median")
    return float(v) if isinstance(v, (int, float)) and v > 0 else default


def main():
    """CLI: measure against the live account and write the table. Read-only against the venue."""
    import argparse
    import os
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="kalshi_presence_table.json")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import maker_kalshi_quoter as q
    c = q.KalshiOrderClient()
    orders = []
    for stt in ("canceled", "executed", "resting"):
        try:
            orders += (c.get_orders(status=stt).get("orders") or [])
        except Exception as e:
            print(f"WARN {stt}: {e!r}"[:160], file=sys.stderr)
    lives = {}
    for t in sorted({o.get("ticker") for o in orders if o.get("ticker")}):
        try:
            m = q.public_get(f"/trade-api/v2/markets/{t}").get("market") or {}
            if m.get("open_time") and m.get("close_time"):
                lives[t] = (_iso(m["close_time"]) - _iso(m["open_time"])).total_seconds()
        except Exception:
            pass
    res = measure(orders, lives)
    print(f"orders={res['n_orders']} markets={res['n_markets']} "
          f"with_life={res['n_markets_with_life']}")
    print(f"presence median={res['presence_median_all']} mean={res['presence_mean_all']}")
    for k, r in sorted(res["families"].items(), key=lambda kv: -kv[1]["resting_hours"]):
        print(f"  {k:22s} presence_med={r['presence_median']:.4f} n_mkts={r['n_markets']:3d} "
              f"rest_h={r['resting_hours']:8.2f} fees/h={r['fees_per_resting_hour']} "
              f"short_lived={r['short_lived_pct']}% filled={r['filled_orders']}/{r['orders']}")
    if not a.print_only:
        print("wrote", write_table(res, a.out))


if __name__ == "__main__":
    main()
