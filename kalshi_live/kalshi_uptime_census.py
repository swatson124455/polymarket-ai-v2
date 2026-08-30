#!/usr/bin/env python3
"""S1 qualifying-uptime census (operator-approved 2026-08-30, strategy review).

Under the official LIP rules (R3 canon) a snapshot pays ONLY when both sides hold
Target depth, so a market's earnability is dominated by the MEASURED fraction of time
its book qualifies — not by its pool or its price shape (census 08-26: 33/41 watched
tickers at 0% uptime; the 90-100% books were the payers we skipped). This script turns
the D4 book tape into that measurement.

Reads d4_books-<today>.jsonl + the newest archived day (gz), joins each ticker's
per-program Target from kalshi_program_map.json (fallback 1000), and writes
kalshi_uptime_census.json: {ticker: {"uptime": 0..1, "snaps": n, "target": t, "ts": unix}}.
Atomic write (tmp+rename). READ-ONLY against the venue (touches only local tape).
Consumed by select_footprint's UPTIME_RANK multiplier (default OFF).
"""
import datetime as dt
import glob
import gzip
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "kalshi_uptime_census.json")
ARCHIVE = "/opt/pa2-maker-backups/d4"


def _targets():
    try:
        pmap = json.load(open(os.path.join(HERE, "kalshi_program_map.json")))
    except Exception:
        return {}
    out = {}
    for v in pmap.values():
        t = v.get("market_ticker")
        # program map has no target field; per-ticker targets come from the live feed via
        # the quoter — census uses the canonical default 1000 unless a caller-side map is
        # added later. Kept as a seam: {ticker: target} merged over the default below.
        if t:
            out.setdefault(t, 1000.0)
    return out


def run(days=2):
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    files = [os.path.join(HERE, f"d4_books-{today}.jsonl")]
    arch = sorted(glob.glob(os.path.join(ARCHIVE, "d4_books-*.jsonl.gz")))
    files = arch[-(days - 1):] + files if days > 1 else files
    tgt = _targets()
    stats = {}
    for f in files:
        try:
            fh = gzip.open(f, "rt") if f.endswith(".gz") else open(f)
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                t = r.get("ticker")
                if not t:
                    continue
                bd = sum(s for _, s in (r.get("bid_depth") or []))
                ad = sum(s for _, s in (r.get("ask_depth") or []))
                a = stats.setdefault(t, [0, 0])
                a[0] += 1
                if bd >= tgt.get(t, 1000.0) and ad >= tgt.get(t, 1000.0):
                    a[1] += 1
    now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
    out = {t: {"uptime": (q / n if n else 0.0), "snaps": n,
               "target": tgt.get(t, 1000.0), "ts": now_ts}
           for t, (n, q) in stats.items()}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, OUT)
    ranked = sorted(out.items(), key=lambda kv: -kv[1]["uptime"])[:10]
    print(f"census: {len(out)} tickers -> {OUT}")
    for t, v in ranked:
        print(f"  {v['uptime']:6.1%} {t} (n={v['snaps']})")
    return out


if __name__ == "__main__":
    run()
