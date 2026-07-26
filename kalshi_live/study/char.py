#!/usr/bin/env python3
"""Characterize the frozen telemetry dataset. Denominators first."""
import json, collections, statistics

rows = [json.loads(l) for l in open("quotes_frozen.jsonl")]
print("N_ROWS", len(rows))
ts = sorted(r["ts"] for r in rows)
print("TS_MIN", ts[0], "TS_MAX", ts[-1])
cycles = sorted({r["cyc"] for r in rows})
print("N_CYCLES", len(cycles), "SPAN_S", cycles[-1] - cycles[0])
gaps = [b - a for a, b in zip(cycles, cycles[1:])]
print("CYCLE_GAP_median_s", statistics.median(gaps) if gaps else None,
      "min", min(gaps) if gaps else None, "max", max(gaps) if gaps else None)

tick = collections.Counter(r["ticker"] for r in rows)
ser = collections.Counter(r["series"] for r in rows)
print("N_DISTINCT_MARKETS", len(tick), "N_DISTINCT_SERIES", len(ser))
print("TOP_SERIES", ser.most_common(12))

# qualification rates -- denominator = all rows
qy = sum(1 for r in rows if r.get("y_qual"))
qn = sum(1 for r in rows if r.get("n_qual"))
both = sum(1 for r in rows if r.get("y_qual") and r.get("n_qual"))
print("Y_QUAL", qy, "N_QUAL", qn, "BOTH_QUAL(R3 pays)", both,
      "PCT_BOTH", round(100.0 * both / len(rows), 2))

# were we actually resting during this window?
rest = sum(1 for r in rows if (r.get("y_rest_ct") or 0) + (r.get("n_rest_ct") or 0) > 0)
print("ROWS_WITH_OUR_ORDER_RESTING", rest, "PCT", round(100.0 * rest / len(rows), 2))

# targets and DF actually seen
print("TARGETS", collections.Counter(r["target"] for r in rows).most_common(10))
print("DFS", collections.Counter(r["df"] for r in rows).most_common(5))

# pool distribution
pools = sorted(r["usd_day"] for r in rows)
print("POOL_usd_day min/med/max", pools[0], pools[len(pools)//2], pools[-1])

# how deep is the qualifying book relative to our 20ct?
bd = sorted(r["y_book_df"] for r in rows if r.get("y_qual"))
if bd:
    print("Y_BOOK_DF (qualifying rows) min/p25/med/p75/max",
          round(bd[0],1), round(bd[len(bd)//4],1), round(bd[len(bd)//2],1),
          round(bd[3*len(bd)//4],1), round(bd[-1],1))
