#!/usr/bin/env python3
"""Kalshi recorder READOUT — turns the arm's jsonl output into the decision tables.

Usage:
    python3 maker_kalshi_readout.py --data-dir <dir with samples-*.jsonl(.gz) + census-*.jsonl(.gz)>

Pull data from the VPS first (read-only), into a KALSHI-DEDICATED local dir —
the Polymarket arms use the same samples-*/census-* basenames, and both
readouts glob them; a shared dir silently interleaves venues:
    scp -i <key> 'ubuntu@18.201.216.0:/opt/pa2-maker-kalshi/*.jsonl*' <kalshi-only-dir>/

Sections:
  A. Data quality — ticks/hour, sampled counts, gaps.
  B. Census timeline — total scheduled $ by hour, World-Cup vs ex-WC split
     (the Jul-19 cliff read), biggest series movers first-vs-last.
  C. Per-series capture table — est $/day under JOIN(100ct) and ACTIVATE,
     with mean activate capital (return-on-capital denominator).
  D. Competition arrival — markets first seen VOID: how long until someone
     else supplies two-sided target liquidity (void->false WITHOUT our
     hypothetical orders), and share decay on markets we could activate.
  E. Share stability — first-third vs last-third JOIN share per series
     (is our hypothetical share eroding as competitors notice the pools?).

All $ figures are MODEL ESTIMATES from the arm's snapshot replay — label them
that way in any report. Nothing here is a payment record.
"""
import argparse
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

WC_PREFIXES = ("KXWC", "KXWORLDCUP")


def read_jsonl(pattern):
    rows = []
    for path in sorted(glob.glob(pattern)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn final line of a live file
    return rows


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def series_of(t):
    return t.split("-")[0] if t else "?"


def is_wc(series):
    return any(series.startswith(p) for p in WC_PREFIXES)


# ---------- pure aggregation (unit-tested) ----------

def data_quality(samples):
    if not samples:
        return {"n": 0}
    by_hour = defaultdict(set)
    tickers = set()
    for r in samples:
        ts = parse_ts(r["ts"])
        by_hour[ts.strftime("%m-%d %H")].add(r["ts"][:16])  # distinct tick-minutes
        tickers.add(r["t"])
    hours = sorted(by_hour)
    return {"n": len(samples), "n_markets": len(tickers),
            "first": samples[0]["ts"], "last": samples[-1]["ts"],
            "ticks_per_hour": {h: len(v) for h, v in sorted(by_hour.items())},
            "hours_covered": len(hours)}


def census_timeline(census):
    out = []
    for r in census:
        wc = sum(v for k, v in r.get("top_series_usd", []) if is_wc(k))
        out.append({"ts": r["ts"], "total": r["total_scheduled_usd"],
                    "n": r["n_programs"], "wc_topN": wc,
                    "ex_wc_topN_floor": r["total_scheduled_usd"] - wc})
    return out


def per_series_table(samples):
    """Two money views per series:
    - rate ($/d): share x usd_day summed over sampled markets. Valid ONLY as an
      instantaneous rate — short program windows make rate-sums exceed actually
      scheduled money, so it is NOT daily earnings.
    - realizable ($): share x total program reward (rw), summed per market —
      the whole-program capture if quoted throughout. rw exists in rows from
      the 2026-07-18 era on; older rows leave it 0 (flagged in output)."""
    agg = defaultdict(lambda: {"n": 0, "jn": 0, "js": 0.0, "an": 0, "as_": 0.0,
                               "cap": 0.0, "capn": 0, "void": 0,
                               "usd_day": defaultdict(float),
                               "rw": defaultdict(float)})
    for r in samples:
        s = series_of(r["t"])
        a = agg[s]
        a["n"] += 1
        if r.get("void"):
            a["void"] += 1
        if r.get("join") is not None:
            a["jn"] += 1
            a["js"] += r["join"]
        if r.get("act") is not None:
            a["an"] += 1
            a["as_"] += r["act"]
            if r.get("act_cap") is not None:
                a["cap"] += r["act_cap"]
                a["capn"] += 1
        # latest per market within the series
        a["usd_day"][r["t"]] = r.get("usd_day", 0.0)
        a["rw"][r["t"]] = r.get("rw", 0.0) or 0.0
    table = []
    for s, a in agg.items():
        pool_rate = sum(a["usd_day"].values())
        pool_rw = sum(a["rw"].values())
        join_share = (a["js"] / a["jn"] / 2.0) if a["jn"] else 0.0
        act_share = (a["as_"] / a["an"] / 2.0) if a["an"] else 0.0
        table.append({"series": s, "n_markets": len(a["usd_day"]), "samples": a["n"],
                      "pool_usd_day": pool_rate,
                      "pool_reward_usd": pool_rw,
                      "join_usd_day": join_share * pool_rate,
                      "act_usd_day": act_share * pool_rate,
                      "join_realizable_usd": join_share * pool_rw,
                      "act_realizable_usd": act_share * pool_rw,
                      "void_rate": a["void"] / a["n"] if a["n"] else 0.0,
                      "cap_avg": a["cap"] / a["capn"] if a["capn"] else 0.0})
    table.sort(key=lambda r: -(r["act_realizable_usd"] or r["act_usd_day"]))
    return table


def competition_arrival(samples):
    """Markets whose FIRST observation was void: time until first non-void
    observation (someone else filled the book to target), else still-void."""
    by_market = defaultdict(list)
    for r in samples:
        by_market[r["t"]].append((parse_ts(r["ts"]), bool(r.get("void"))))
    flipped, still_void, hours_to_flip = 0, 0, []
    for t, obs in by_market.items():
        obs.sort()
        if not obs[0][1]:
            continue  # wasn't void at first sight
        flip = next((ts for ts, v in obs if not v), None)
        if flip is None:
            still_void += 1
        else:
            flipped += 1
            hours_to_flip.append((flip - obs[0][0]).total_seconds() / 3600)
    hours_to_flip.sort()
    med = hours_to_flip[len(hours_to_flip) // 2] if hours_to_flip else None
    return {"first_seen_void": flipped + still_void, "flipped_to_contested": flipped,
            "still_void": still_void, "median_hours_to_flip": med}


def share_stability(samples):
    """First-third vs last-third mean JOIN share per series (erosion check)."""
    by_series = defaultdict(list)
    for r in samples:
        if r.get("join") is not None:
            by_series[series_of(r["t"])].append((r["ts"], r["join"]))
    out = {}
    for s, rows in by_series.items():
        if len(rows) < 6:
            continue
        rows.sort()
        third = len(rows) // 3
        early = sum(v for _, v in rows[:third]) / third
        late = sum(v for _, v in rows[-third:]) / third
        out[s] = {"early": early / 2.0, "late": late / 2.0, "n": len(rows)}
    return out


# ---------- rendering ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()
    samples = read_jsonl(os.path.join(args.data_dir, "samples-*.jsonl*"))
    census = read_jsonl(os.path.join(args.data_dir, "census-*.jsonl*"))
    samples.sort(key=lambda r: r["ts"])

    q = data_quality(samples)
    print("=== A. DATA QUALITY ===")
    if not q["n"]:
        print("no samples found — wrong --data-dir?")
        return 1
    print(f"samples={q['n']}  markets={q['n_markets']}  hours={q['hours_covered']}  "
          f"window={q['first']} .. {q['last']}")
    low = {h: c for h, c in q["ticks_per_hour"].items() if c < 10}
    print(f"low-coverage hours (<10 ticks): {low if low else 'none'}")

    print("\n=== B. CENSUS TIMELINE (WC split = top-40-series floor) ===")
    for row in census_timeline(census):
        print(f"{row['ts'][:16]}  total=${row['total']:>10,.0f}  n={row['n']}  "
              f"WC(top40)=${row['wc_topN']:>9,.0f}  exWC>=${row['ex_wc_topN_floor']:>10,.0f}")

    print("\n=== C. PER-SERIES CAPTURE (MODEL ESTIMATES) ===")
    print("rate$/d = instantaneous rate while programs run (short windows inflate");
    print("rate-sums past scheduled money — do NOT read as daily earnings).")
    print("realizable$ = share x total program reward (whole-program capture; rw-era rows only).")
    print(f"{'series':26} {'mkts':>4} {'poolrw$':>8} {'joinRz$':>8} {'actRz$':>8} {'rate$/d':>8} {'void%':>6} {'capavg$':>8}")
    tbl = per_series_table(samples)
    for r in tbl[:25]:
        print(f"{r['series']:26} {r['n_markets']:>4} {r['pool_reward_usd']:>8.0f} "
              f"{r['join_realizable_usd']:>8.2f} {r['act_realizable_usd']:>8.2f} "
              f"{r['act_usd_day']:>8.2f} "
              f"{r['void_rate']*100:>6.1f} {r['cap_avg']:>8.0f}")
    tjr = sum(r["join_realizable_usd"] for r in tbl)
    tar = sum(r["act_realizable_usd"] for r in tbl)
    tc = sum(r["cap_avg"] * r["n_markets"] for r in tbl)
    n_norw = sum(1 for s in samples if not s.get("rw"))
    print(f"{'TOTAL (footprint)':26} {'':>4} {'':>8} {tjr:>8.2f} {tar:>8.2f} {'':>8} {'':>6} {tc:>8.0f}")
    if n_norw:
        print(f"WARNING: {n_norw}/{len(samples)} rows predate the rw-era — realizable$ is a floor")

    print("\n=== D. COMPETITION ARRIVAL (void markets getting contested) ===")
    print(competition_arrival(samples))

    print("\n=== E. JOIN-SHARE STABILITY (early vs late thirds; erosion if late<early) ===")
    for s, v in sorted(share_stability(samples).items(),
                       key=lambda kv: kv[1]["early"] - kv[1]["late"], reverse=True)[:15]:
        drift = (v["late"] - v["early"])
        print(f"{s:26} early={v['early']:.3f} late={v['late']:.3f} drift={drift:+.3f} n={v['n']}")

    print("\nNOTE: all $ figures are snapshot-replay MODEL ESTIMATES (assumes our "
          "orders present at every snapshot; competition response NOT priced in "
          "beyond what the book already shows). Not payment records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
