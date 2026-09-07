#!/usr/bin/env python3
"""Stage 1 POPULATION STUDY over the zero-based firehose capture
(docs/ZERO_BASED_SIFTER.md, charter 2026-08-25; run 2026-09-02 when the
7-full-day minimum completed).

Charter law honored here:
- NO thresholds go in. Output is DISTRIBUTIONS: per-wallet behavioral
  metrics -> percentile tables across the wallet population, plus a coarse
  joint (cluster) structure. Verdicts are downstream and must name their
  percentile + sensitivity band.
- Metrics computed THIS pass (from firehose fields w/tok/s/p/z/t only):
  trades total, trades per capture-day and per active-day, active days,
  size distributions (shares z AND notional z*p USD, labeled separately),
  market breadth (distinct tokens), side mix (BUY share), inter-trade
  spacing (median seconds), burstiness (share of trades <60s after the
  wallet's previous trade; the 60s constant is a DESCRIPTIVE bucket edge
  disclosed here, not a threshold - the full spacing distribution is also
  reported so any other edge can be recomputed).
- NOT computed this pass (disclosed, not silently dropped): category mix
  (needs a token->market->category join outside the firehose) and
  per-wallet peak open-position concurrency (needs entry/exit pairing or
  resolutions; the firehose alone cannot say when a position closed).
  Both are named follow-up passes.

Streaming design (25M+ rows on a shared box): per wallet we keep counters,
an active-day bitmask, log-bucketed histograms for sizes and spacings
(median read from the histogram; worst-case relative error one bucket
width, ~12% at 10 buckets/decade, disclosed), and a set of 64-bit token
hashes for breadth (collision loss negligible at this scale).
"""
import argparse
import glob
import gzip
import json
import math
import os
import sys
from datetime import datetime, timezone

BUCKETS_PER_DECADE = 10.0
BURST_EDGE_S = 60.0  # descriptive bucket edge, disclosed in the report
PCTS = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def _bucket(v: float) -> int:
    if v <= 0:
        return -10 ** 9
    return int(math.floor(math.log10(v) * BUCKETS_PER_DECADE))


def _bucket_mid(b: int) -> float:
    return 10 ** ((b + 0.5) / BUCKETS_PER_DECADE)


def hist_add(h: dict, v: float) -> None:
    b = _bucket(v)
    h[b] = h.get(b, 0) + 1


def hist_median(h: dict):
    n = sum(h.values())
    if n == 0:
        return None
    k = (n - 1) // 2
    for b in sorted(h):
        k -= h[b]
        if k < 0:
            return _bucket_mid(b) if b > -10 ** 9 else 0.0
    return None


class W:
    __slots__ = ("n", "buy", "days", "z_sum", "usd_sum", "z_h", "sp_h",
                 "toks", "last_t", "burst")

    def __init__(self):
        self.n = 0
        self.buy = 0
        self.days = 0          # bitmask over capture days
        self.z_sum = 0.0
        self.usd_sum = 0.0
        self.z_h = {}
        self.sp_h = {}
        self.toks = set()
        self.last_t = None
        self.burst = 0


def scan(files, day0_epoch):
    wallets = {}
    total = bad = 0
    tmin = tmax = None
    for fp in files:
        opener = gzip.open if fp.endswith(".gz") else open
        with opener(fp, "rt", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                total += 1
                try:
                    r = json.loads(ln)
                    w, t = r["w"], float(r["t"])
                    z, p, s = float(r["z"]), float(r["p"]), r["s"]
                    tok = r["tok"]
                except (ValueError, KeyError, TypeError):
                    bad += 1
                    continue
                tmin = t if tmin is None else min(tmin, t)
                tmax = t if tmax is None else max(tmax, t)
                a = wallets.get(w)
                if a is None:
                    a = wallets[w] = W()
                a.n += 1
                a.buy += 1 if s == "BUY" else 0
                a.days |= 1 << max(0, min(63, int((t - day0_epoch) // 86400)))
                a.z_sum += z
                a.usd_sum += z * p
                hist_add(a.z_h, z)
                a.toks.add(hash(tok) & 0xFFFFFFFFFFFFFFFF)
                if a.last_t is not None and t >= a.last_t:
                    gap = t - a.last_t
                    hist_add(a.sp_h, max(gap, 0.001))
                    if gap < BURST_EDGE_S:
                        a.burst += 1
                a.last_t = t
    return wallets, total, bad, tmin, tmax


def pct_table(vals, label, fmt="{:.4g}"):
    vs = sorted(v for v in vals if v is not None)
    if not vs:
        return f"  {label:<26} (no data)"
    row = " ".join(
        f"p{p}={fmt.format(vs[min(len(vs) - 1, int(round(p / 100 * (len(vs) - 1))))])}"
        for p in PCTS)
    return f"  {label:<26} n={len(vs)} {row}"


def run(args):
    # comma-separated globs so the window can pin EXACT capture days
    # (a partial current-day gz exists alongside the 7 complete days)
    files = sorted(set(sum((glob.glob(g) for g in args.glob.split(",")), [])))
    assert files, f"no capture files match {args.glob} - ABORT"
    day0 = datetime(2026, 8, 26, tzinfo=timezone.utc).timestamp()
    wallets, total, bad, tmin, tmax = scan(files, day0)
    span_days = (tmax - tmin) / 86400.0
    out = []
    out.append(f"===== POPULATION STUDY (stage 1) {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} =====")
    out.append(f"capture: {len(files)} files | rows={total} unparseable={bad} "
               f"| span {datetime.fromtimestamp(tmin, timezone.utc):%m-%d %H:%M}Z"
               f" -> {datetime.fromtimestamp(tmax, timezone.utc):%m-%d %H:%M}Z"
               f" ({span_days:.2f} days) | wallets={len(wallets)}")
    out.append("law: distributions only; downstream cuts must name percentile "
               "+ sensitivity. Size medians from log-buckets "
               f"({BUCKETS_PER_DECADE:.0f}/decade, ~12% bucket error). "
               f"burst edge {BURST_EDGE_S:.0f}s is descriptive, not a "
               "threshold. NOT in this pass (follow-ups): category mix, "
               "open-position concurrency.")
    ws = list(wallets.values())
    out.append("\nPER-WALLET DISTRIBUTIONS (population = every wallet seen):")
    out.append(pct_table([a.n for a in ws], "trades_total"))
    out.append(pct_table([a.n / span_days for a in ws], "trades_per_capture_day"))
    out.append(pct_table([a.n / max(1, bin(a.days).count('1')) for a in ws],
                         "trades_per_ACTIVE_day"))
    out.append(pct_table([bin(a.days).count('1') for a in ws], "active_days"))
    out.append(pct_table([len(a.toks) for a in ws], "market_breadth_tokens"))
    out.append(pct_table([a.buy / a.n for a in ws], "buy_share", "{:.3f}"))
    out.append(pct_table([a.z_sum for a in ws], "total_size_shares"))
    out.append(pct_table([a.usd_sum for a in ws], "total_notional_usd"))
    out.append(pct_table([hist_median(a.z_h) for a in ws], "median_trade_shares"))
    out.append(pct_table([hist_median(a.sp_h) for a in ws if a.n >= 2],
                         "median_spacing_s"))
    out.append(pct_table([a.burst / (a.n - 1) for a in ws if a.n >= 2],
                         "burst_frac(<60s)", "{:.3f}"))
    out.append("\nJOINT STRUCTURE (wallet counts, trades_total x breadth):")
    edges = [1, 3, 10, 30, 100, 300, 1000, 10 ** 9]
    lab = ["1-2", "3-9", "10-29", "30-99", "100-299", "300-999", "1000+"]
    grid = {}
    for a in ws:
        i = next(k for k, e in enumerate(edges[1:]) if a.n < e)
        j = next(k for k, e in enumerate(edges[1:]) if len(a.toks) < e)
        grid[(i, j)] = grid.get((i, j), 0) + 1
    hdr = "  trades\\breadth " + " ".join(f"{s:>8}" for s in lab)
    out.append(hdr)
    for i, li in enumerate(lab):
        out.append(f"  {li:<14} " + " ".join(
            f"{grid.get((i, j), 0):>8}" for j in range(len(lab))))
    report = "\n".join(out)
    print(report)
    if args.out:
        tmp = args.out + ".tmp"
        with open(tmp, "w") as f:
            f.write(report + "\n")
        os.replace(tmp, args.out)
        # per-wallet metric dump for downstream stages (one json line each)
        tmp2 = args.out + ".wallets.jsonl.tmp"
        with open(tmp2, "w") as f:
            for wa, a in wallets.items():
                f.write(json.dumps({
                    "w": wa, "n": a.n, "buy": a.buy,
                    "active_days": bin(a.days).count('1'),
                    "breadth": len(a.toks),
                    "z_sum": round(a.z_sum, 4),
                    "usd_sum": round(a.usd_sum, 4),
                    "med_z": hist_median(a.z_h),
                    "med_gap_s": hist_median(a.sp_h),
                    "burst": a.burst}) + "\n")
        os.replace(tmp2, args.out + ".wallets.jsonl")
    return 0


def _self_test() -> int:
    print("SELF-TEST - population_study (offline)\n")
    ok = True
    h = {}
    for v in [1, 10, 10, 10, 100]:
        hist_add(h, v)
    m = hist_median(h)
    ok1 = m is not None and 8 <= m <= 13   # bucket-mid of the 10s bucket
    print(f"  [hist] median of [1,10,10,10,100] lands in the 10-bucket : {ok1}")
    ok &= ok1
    ok2 = hist_median({}) is None and _bucket(0) < -10 ** 8
    print(f"  [hist] empty -> None; zero/negative guarded : {ok2}")
    ok &= ok2
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        fp = os.path.join(d, "x.jsonl")
        day0 = datetime(2026, 8, 26, tzinfo=timezone.utc).timestamp()
        rows = [
            {"w": "0xa", "tok": "T1", "s": "BUY", "p": 0.5, "z": 10, "tx": "h1", "t": day0 + 10},
            {"w": "0xa", "tok": "T2", "s": "SELL", "p": 0.2, "z": 5, "tx": "h2", "t": day0 + 40},
            {"w": "0xa", "tok": "T1", "s": "BUY", "p": 0.5, "z": 10, "tx": "h3", "t": day0 + 90000},
            {"w": "0xb", "tok": "T1", "s": "BUY", "p": 0.1, "z": 1, "tx": "h4", "t": day0 + 20},
            "GARBAGE-LINE",
        ]
        with open(fp, "w") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        wallets, total, bad, tmin, tmax = scan([fp], day0)
        a = wallets["0xa"]
        ok3 = (total == 5 and bad == 1 and len(wallets) == 2
               and a.n == 3 and a.buy == 2
               and bin(a.days).count('1') == 2
               and len(a.toks) == 2
               and abs(a.usd_sum - (5 + 1 + 5)) < 1e-9
               and a.burst == 1)          # 30s gap bursts, 1-day gap doesn't
        print(f"  [scan] counts/sides/days/breadth/usd/burst exact : {ok3}")
        ok &= ok3
        ok4 = wallets["0xb"].last_t == day0 + 20 and wallets["0xb"].burst == 0
        print(f"  [scan] single-trade wallet: no spacing, no burst : {ok4}")
        ok &= ok4
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob",
                    default="/opt/pa2-shared/mb_copyable_data/firehose/"
                            "firehose_2026*.jsonl.gz")
    ap.add_argument("--out",
                    default="/opt/pa2-shared/mb_copyable_data/firehose/"
                            "population_study_stage1.txt")
    ap.add_argument("--self-test", action="store_true", dest="self_test")
    a = ap.parse_args()
    sys.exit(_self_test() if a.self_test else run(a))
