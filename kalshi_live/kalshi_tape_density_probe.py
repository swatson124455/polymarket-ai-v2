#!/usr/bin/env python3
"""TAPE DENSITY PROBE -- NEW FILE, READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

The question that decides whether ANY tape-based detector (VPIN, trade-sign OFI, sweep
detection) is viable here: how many trades actually land in a 2-MINUTE bucket on the
contracts we quote? A detector needs non-degenerate buckets. If the median 2-min bucket
holds 0 trades, VPIN-style volume bucketing has nothing to synchronise on.

Measures, per contract, over the FULL retrievable tape:
  - trades and contracts (count_fp) per 2-min bucket: fraction empty, median, p90
  - the volume-clock: how long does it take to accumulate a V-contract bucket?
  - sweep structure: trades sharing (created_time-to-the-second, taker_side) and how many
    DISTINCT PRICES each such group spans (a >1-price group = the book was walked)
  - is_block_trade share

Reports GAS (13h, the profitable slice) vs TEMP (~1h, the -31.95% slice) separately.
Run:  python kalshi_tape_density_probe.py
"""
import json
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

PUB = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "kalshi-tape-density-probe/1.0 (read-only measurement)"}
SPACING_S = 0.35
SERIES = ["KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH", "KXTEMPCHIH", "KXTEMPLAXH"]


def get(path):
    time.sleep(SPACING_S)
    req = urllib.request.Request(PUB + path, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pull_tape(t, pages=4):
    rows, cur = [], ""
    for _ in range(pages):
        try:
            d = get(f"/markets/trades?ticker={t}&limit=1000" + (f"&cursor={cur}" if cur else ""))
        except Exception as e:
            print(f"    tape ERROR {e!r}")
            break
        tr = d.get("trades") or []
        rows += tr
        cur = d.get("cursor") or ""
        if not cur or not tr:
            break
    return rows


def analyse(t, rows):
    if not rows:
        return None
    def ts(r):
        return datetime.fromisoformat(r["created_time"].replace("Z", "+00:00"))
    rows = sorted(rows, key=ts)
    t0, t1 = ts(rows[0]), ts(rows[-1])
    span_min = (t1 - t0).total_seconds() / 60.0
    if span_min <= 0:
        return None

    # 2-min buckets across the whole covered span (INCLUDING empty ones)
    nb = max(1, int(span_min // 2) + 1)
    cnt = [0] * nb
    vol = [0.0] * nb
    for r in rows:
        i = min(nb - 1, int((ts(r) - t0).total_seconds() // 120))
        cnt[i] += 1
        vol[i] += fnum(r.get("count_fp")) or 0.0
    empty = sum(1 for c in cnt if c == 0)

    # sweeps: group by (second-truncated ts, taker_side); count distinct prices in group
    grp = defaultdict(list)
    for r in rows:
        px = fnum(r.get("yes_price_dollars"))
        grp[(r["created_time"][:19], r.get("taker_side"))].append(px)
    multi_price = sum(1 for v in grp.values() if len({p for p in v if p is not None}) > 1)
    multi_trade = sum(1 for v in grp.values() if len(v) > 1)

    tot_ct = sum(vol)
    blocks = sum(1 for r in rows if r.get("is_block_trade"))
    return {
        "ticker": t, "n_trades": len(rows), "span_min": round(span_min, 1),
        "contracts": round(tot_ct, 1),
        "ct_per_min": round(tot_ct / span_min, 2),
        "buckets_2min": nb, "pct_empty_2min": round(100.0 * empty / nb, 1),
        "median_trades_2min": statistics.median(cnt),
        "p90_trades_2min": sorted(cnt)[int(0.9 * (nb - 1))],
        "median_ct_2min": round(statistics.median(vol), 1),
        "taker_groups": len(grp), "groups_multi_trade": multi_trade,
        "groups_multi_price_SWEEP": multi_price,
        "sweep_rate_pct": round(100.0 * multi_price / max(1, len(grp)), 1),
        "block_trades": blocks,
    }


def main():
    out = []
    for s in SERIES:
        try:
            d = get(f"/markets?series_ticker={s}&status=open&limit=200")
        except Exception as e:
            print(f"{s}: {e!r}")
            continue
        ms = d.get("markets") or []
        ms.sort(key=lambda m: -(fnum(m.get("volume_fp")) or 0.0))
        print(f"\n=== {s}: {len(ms)} open markets ===")
        for m in ms[:3]:
            t = m["ticker"]
            rows = pull_tape(t)
            a = analyse(t, rows)
            if not a:
                print(f"  {t}: no tape")
                continue
            a["series"] = s
            a["close_time"] = m.get("close_time")
            a["volume_fp"] = m.get("volume_fp")
            out.append(a)
            print(f"  {t}")
            print(f"    {a['n_trades']} trades / {a['contracts']} contracts over {a['span_min']}min"
                  f"  ({a['ct_per_min']} ct/min)")
            print(f"    2-min buckets: n={a['buckets_2min']} EMPTY={a['pct_empty_2min']}% "
                  f"median={a['median_trades_2min']} trades / {a['median_ct_2min']} ct, "
                  f"p90={a['p90_trades_2min']}")
            print(f"    taker groups={a['taker_groups']}  multi-trade={a['groups_multi_trade']}  "
                  f"MULTI-PRICE SWEEPS={a['groups_multi_price_SWEEP']} ({a['sweep_rate_pct']}%)  "
                  f"blocks={a['block_trades']}")

    print("\n\n=== FAMILY ROLLUP ===")
    for fam, pref in (("GAS", "KXAAAGAS"), ("TEMP", "KXTEMP")):
        rs = [r for r in out if r["series"].startswith(pref)]
        if not rs:
            print(f"{fam}: no data")
            continue
        print(f"{fam}: n_contracts={len(rs)}  "
              f"median ct/min={statistics.median([r['ct_per_min'] for r in rs]):.2f}  "
              f"median %empty-2min={statistics.median([r['pct_empty_2min'] for r in rs]):.1f}%  "
              f"median sweep rate={statistics.median([r['sweep_rate_pct'] for r in rs]):.1f}%")
    with open("tape_density.json", "w") as fh:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "rows": out}, fh, indent=1)
    print("\nwrote tape_density.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
