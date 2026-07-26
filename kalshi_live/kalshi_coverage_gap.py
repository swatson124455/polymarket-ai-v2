#!/usr/bin/env python3
"""COVERAGE GAP — how many hours a day can our allowlist actually earn? (read-only, offline)

Consumes the cached universe written by kalshi_program_fetch.py (118,731 programs,
2026-07-23T15:24Z). NEVER hits the network, NEVER trades, NEVER writes a frozen dataset.

DEFINITIONS (canon KALSHI_LIP_RULE_CANON.md §T / R1):
  * a PROGRAM is per CONTRACT (market_ticker), with one Time Period [start_date, end_date]
    and one period_reward that is the TOTAL for that window (R1). $/day = reward / days.
  * a SERIES is LIVE at time t iff >=1 of its programs covers t. That is the coarsest
    possible earn-gate: it says a pool EXISTS, not that we can win any of it.
  * QUOTABLE at t is stricter and matches select_footprint (maker_kalshi_quoter.py:294-312):
    incentive_type == liquidity, target_size_fp and discount_factor_bps non-null, and t is
    before the LATE-LIFE cutoff  end - min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN,
    LATE_LIFE_FRAC * life_min)).

WHAT THIS CANNOT SEE (state it with every number):
  * fill rate, queue position, adverse selection -- reward-side only.
  * the R3 two-sided book test. A live program whose BOOK misses Target Size on either
    side pays NOBODY. Coverage here is an UPPER BOUND on earnable time; §M6 measured only
    8/39 in-allowlist contracts two-sided at 03:14Z, so the real earnable fraction of a
    "live" hour is materially lower. Do not read duty cycle as revenue.
  * our normalised share (competing qualifying size).
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOW = ["KXTEMPDCH", "KXTEMPAUSH", "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH",
         "KXAAAGASD", "KXAAAGASW"]
# mirrors maker_kalshi_quoter.py defaults (read, not imported -- that file is owned by
# another concurrent workflow and must not be touched)
WIND_DOWN_MIN = 45
LATE_LIFE_FRAC = 0.30
MAX_ENTRY_CUTOFF_MIN = 1440


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_universe(path=None):
    if path is None:
        meta = json.load(open(os.path.join(HERE, "program_universe_latest.json")))
        path = os.path.join(HERE, meta["file"])
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows, path


def merge(intervals):
    """union of [a,b) intervals -> sorted disjoint list"""
    out = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def clip(intervals, lo, hi):
    out = []
    for a, b in intervals:
        a2, b2 = max(a, lo), min(b, hi)
        if b2 > a2:
            out.append((a2, b2))
    return out


def covered_secs(intervals):
    return sum((b - a).total_seconds() for a, b in intervals)


def hour_coverage(intervals, lo, hi):
    """fraction of each UTC hour-of-day slot inside [lo,hi) that is covered."""
    cov = [0.0] * 24
    tot = [0.0] * 24
    t = lo
    while t < hi:
        nxt = min(t + timedelta(hours=1), hi)
        h = t.hour
        tot[h] += (nxt - t).total_seconds()
        for a, b in intervals:
            s, e = max(a, t), min(b, nxt)
            if e > s:
                cov[h] += (e - s).total_seconds()
        t = nxt
    return [(cov[h] / tot[h]) if tot[h] else 0.0 for h in range(24)], tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--universe", default=None)
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    rows, path = load_universe(a.universe)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hi = now
    lo = now - timedelta(days=a.days)

    print(f"universe file : {os.path.basename(path)}  ({len(rows):,} programs)")
    print(f"window        : {lo:%Y-%m-%dT%H:%MZ} -> {hi:%Y-%m-%dT%H:%MZ}  ({a.days} days)")
    print(f"allowlist     : {','.join(ALLOW)}")

    # ---- per-series intervals -------------------------------------------------
    live_iv = defaultdict(list)      # program exists
    quot_iv = defaultdict(list)      # program exists AND passes the footprint gates
    prog_n = defaultdict(int)
    pool_rate = defaultdict(list)    # (start, end, usd_day) for accessible-pool integration
    bad = defaultdict(int)
    want = set(ALLOW)
    for p in rows:
        t = p.get("market_ticker") or ""
        s = t.split("-")[0]
        if s not in want:
            continue
        try:
            st, en = parse_iso(p["start_date"]), parse_iso(p["end_date"])
        except Exception:
            bad["date_parse"] += 1
            continue
        if en <= lo or st >= hi:
            continue
        prog_n[s] += 1
        live_iv[s].append((st, en))
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            bad["not_liquidity"] += 1
            continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            bad["null_fields"] += 1
            continue
        life_min = max((en - st).total_seconds() / 60.0, 1.0)
        cutoff = min(MAX_ENTRY_CUTOFF_MIN, max(WIND_DOWN_MIN, LATE_LIFE_FRAC * life_min))
        q_end = en - timedelta(minutes=cutoff)
        if q_end > st:
            quot_iv[s].append((st, q_end))
        days = max((en - st).total_seconds() / 86400, 1 / 24)
        pool_rate[s].append((st, en, ((p.get("period_reward") or 0) / 10000) / days))

    span = (hi - lo).total_seconds()
    print(f"\ndrops while building: {dict(bad) or 'none'}")

    # ---- per-series summary ---------------------------------------------------
    print("\n=== PER-SERIES PROGRAM CALENDAR (last %dd) ===" % a.days)
    hdr = ("series", "progs", "windows", "duty_live", "duty_quotable",
           "med_win_h", "n_gaps", "med_gap_h", "max_gap_h")
    print(f"{hdr[0]:<12}{hdr[1]:>7}{hdr[2]:>9}{hdr[3]:>11}{hdr[4]:>15}"
          f"{hdr[5]:>11}{hdr[6]:>8}{hdr[7]:>11}{hdr[8]:>11}")
    per_series = {}
    for s in ALLOW:
        mi = clip(merge(live_iv[s]), lo, hi)
        mq = clip(merge(quot_iv[s]), lo, hi)
        duty = covered_secs(mi) / span
        dutyq = covered_secs(mq) / span
        wins = [(b - a2).total_seconds() / 3600 for a2, b in mi]
        gaps = []
        for i in range(len(mi) - 1):
            gaps.append((mi[i + 1][0] - mi[i][1]).total_seconds() / 3600)
        if mi and mi[0][0] > lo:
            gaps.append((mi[0][0] - lo).total_seconds() / 3600)
        if mi and mi[-1][1] < hi:
            gaps.append((hi - mi[-1][1]).total_seconds() / 3600)
        med = lambda v: sorted(v)[len(v) // 2] if v else 0.0
        print(f"{s:<12}{prog_n[s]:>7}{len(mi):>9}{duty:>10.1%}{dutyq:>15.1%}"
              f"{med(wins):>11.2f}{len(gaps):>8}{med(gaps):>11.2f}"
              f"{(max(gaps) if gaps else 0):>11.2f}")
        per_series[s] = {"merged": mi, "quotable": mq, "duty": duty, "duty_q": dutyq,
                         "windows": [(x.isoformat(), y.isoformat()) for x, y in mi],
                         "n_programs": prog_n[s]}

    # ---- window detail --------------------------------------------------------
    print("\n=== WINDOW DETAIL (merged union per series) ===")
    for s in ALLOW:
        mi = per_series[s]["merged"]
        print(f"\n{s}  ({len(mi)} merged windows)")
        if not mi:
            print("   (no programs overlapping the window)")
            continue
        prev_end = None
        for a2, b in mi:
            gap = f"  gap_before={(a2 - prev_end).total_seconds()/3600:6.2f}h" if prev_end else ""
            print(f"   {a2:%m-%d %H:%MZ} -> {b:%m-%d %H:%MZ}  "
                  f"dur={(b-a2).total_seconds()/3600:7.2f}h{gap}")
            prev_end = b

    # ---- union hour table -----------------------------------------------------
    print("\n=== 24-HOUR UNION COVERAGE (fraction of that UTC hour, %dd sample) ===" % a.days)
    hc = {s: hour_coverage(per_series[s]["merged"], lo, hi)[0] for s in ALLOW}
    hcq = {s: hour_coverage(per_series[s]["quotable"], lo, hi)[0] for s in ALLOW}
    all_iv = merge([iv for s in ALLOW for iv in per_series[s]["merged"]])
    all_q = merge([iv for s in ALLOW for iv in per_series[s]["quotable"]])
    uh, _ = hour_coverage(all_iv, lo, hi)
    uhq, _ = hour_coverage(all_q, lo, hi)
    print("hour " + "".join(f"{s.replace('KXTEMP','T').replace('KXAAAGAS','G'):>7}"
                            for s in ALLOW) + "   n_series_live  union_live union_quot")
    dark, dark_q = [], []
    for h in range(24):
        n_live = sum(1 for s in ALLOW if hc[s][h] > 0)
        print(f"{h:02d}Z  " + "".join(f"{hc[s][h]*100:6.0f}%" for s in ALLOW) +
              f"{n_live:>14}{uh[h]*100:>11.0f}%{uhq[h]*100:>10.0f}%")
        if uh[h] == 0:
            dark.append(h)
        if uhq[h] == 0:
            dark_q.append(h)
    print(f"\nDARK HOURS (no series has ANY live program):      "
          f"{', '.join(f'{h:02d}Z' for h in dark) if dark else 'none'}")
    print(f"DARK HOURS (nothing passes the footprint gates):  "
          f"{', '.join(f'{h:02d}Z' for h in dark_q) if dark_q else 'none'}")
    print(f"union duty cycle  live={covered_secs(clip(all_iv, lo, hi))/span:.1%}   "
          f"quotable={covered_secs(clip(all_q, lo, hi))/span:.1%}")

    # ---- accessible pool by hour (reward-side only) ---------------------------
    print("\n=== ACCESSIBLE POOL RATE BY UTC HOUR ($/day of pool with a live program) ===")
    print("   (sum over live programs of period_reward normalised to $/day, R1. This is the")
    print("    POOL SIZE we could compete for, NOT capture -- share depends on competing size.)")
    pool_h = [0.0] * 24
    cnt_h = [0.0] * 24
    tot_h = [0.0] * 24
    t = lo
    while t < hi:
        nxt = t + timedelta(hours=1)
        h = t.hour
        tot_h[h] += 1
        for s in ALLOW:
            for st, en, ud in pool_rate[s]:
                if st < nxt and en > t:
                    pool_h[h] += ud
                    cnt_h[h] += 1
        t = nxt
    print("hour   mean_live_contracts   mean_pool_$/day_accessible")
    for h in range(24):
        n = tot_h[h] or 1
        print(f"{h:02d}Z {cnt_h[h]/n:>18.1f} {pool_h[h]/n:>27.2f}")

    if a.json_out:
        out = {"generated": datetime.now(timezone.utc).isoformat(),
               "universe": os.path.basename(path), "lo": lo.isoformat(), "hi": hi.isoformat(),
               "series": {s: {k: v for k, v in per_series[s].items() if k != "merged"
                              and k != "quotable"} for s in ALLOW},
               "union_hour_live": uh, "union_hour_quotable": uhq,
               "dark_hours_live": dark, "dark_hours_quotable": dark_q,
               "hour_pool_usd_day": [pool_h[h] / (tot_h[h] or 1) for h in range(24)],
               "hour_mean_contracts": [cnt_h[h] / (tot_h[h] or 1) for h in range(24)]}
        json.dump(out, open(a.json_out, "w"), indent=2)
        print(f"\nwrote {a.json_out}")


if __name__ == "__main__":
    sys.exit(main() or 0)
