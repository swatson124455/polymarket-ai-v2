#!/usr/bin/env python3
"""STUDY A v2 (corrected): availability with LIFETIME denominators + mid-shell
distribution. v1 flaw (self-caught): candle-hours only exist when the venue emits
them -> denominator overweighted active hours. Here: denominator = hours from
market open_time (capped at 7d back) to now; hours with no candle = NOT two-sided
(conservative). Each two-sided hour is bucketed by mid shell."""
import datetime, json, sys, time
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
import kalshi_attribution_ledger as kal

NOW = int(time.time())
START = NOW - 7 * 86400
SHELLS = [(0.0, 0.05, "00-05"), (0.05, 0.10, "05-10"), (0.10, 0.15, "10-15"),
          (0.15, 0.85, "15-85"), (0.85, 0.90, "85-90"), (0.90, 0.95, "90-95"),
          (0.95, 1.01, "95-100")]

def iso_ts(s):
    return int(datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())

qual = json.load(open("/tmp/F9_RECOUNT.json"))["qualifying"]
tickers = [r["ticker"] for r in qual]
meta = {}
for i in range(0, len(tickers), 90):
    d = kal.get(kal.P + "/markets?limit=1000&tickers=" + ",".join(tickers[i:i+90]))
    for m in d.get("markets") or []:
        meta[m["ticker"]] = m
print("read_ts:", datetime.datetime.now(datetime.timezone.utc).isoformat(),
      " markets:", len(qual))
agg_life = agg_two = 0
shell_agg = {s[2]: 0 for s in SHELLS}
rows = []
for r in qual:
    t = r["ticker"]; series = t.split("-")[0]
    m = meta.get(t) or {}
    ot = m.get("open_time")
    t0 = max(iso_ts(ot), START) if ot else START
    life_h = max(int((NOW - t0) / 3600), 1)
    try:
        d = kal.get(f"{kal.P}/series/{series}/markets/{t}/candlesticks"
                    f"?start_ts={t0}&end_ts={NOW}&period_interval=60")
    except Exception as e:
        rows.append({"ticker": t, "error": str(e)[:80]}); continue
    two = 0; shells = {s[2]: 0 for s in SHELLS}
    for c in d.get("candlesticks") or []:
        try:
            bid = float(((c.get("yes_bid") or {}).get("close_dollars")) or 0)
            ask = float(((c.get("yes_ask") or {}).get("close_dollars")) or 1)
        except (TypeError, ValueError):
            continue
        if bid > 0 and ask < 1.0:
            two += 1
            mid = (bid + ask) / 2.0
            for lo, hi, name in SHELLS:
                if lo <= mid < hi:
                    shells[name] += 1; break
    rows.append({"ticker": t, "life_h": life_h, "two_sided_h": two,
                 "two_pct_of_life": round(100 * two / life_h, 1), "shells": shells})
    agg_life += life_h; agg_two += two
    for k in shells: shell_agg[k] += shells[k]
    time.sleep(0.15)
print(f"AGGREGATE: lifetime-hours {agg_life}, two-sided {agg_two} "
      f"({100*agg_two/max(agg_life,1):.1f}% of LIFETIME)")
print("two-sided hours by mid shell (denominator = the %d two-sided hours):" % agg_two)
for _, _, name in SHELLS:
    v = shell_agg[name]
    print(f"  {name}: {v} h ({100*v/max(agg_two,1):.1f}%)")
inv_10_90 = shell_agg["00-05"] + shell_agg["05-10"] + shell_agg["90-95"] + shell_agg["95-100"]
inv_15_85 = inv_10_90 + shell_agg["10-15"] + shell_agg["85-90"]
print(f"investable (outside 0.10-0.90): {inv_10_90} h = {100*inv_10_90/max(agg_life,1):.1f}% of lifetime")
print(f"investable (outside 0.15-0.85): {inv_15_85} h = {100*inv_15_85/max(agg_life,1):.1f}% of lifetime")
rows.sort(key=lambda x: -(x.get("two_sided_h") or 0))
for x in rows[:12]:
    print(" %-34s life %4s  two %4s (%s%%)  shells %s"
          % (x["ticker"], x.get("life_h"), x.get("two_sided_h"),
             x.get("two_pct_of_life"), x.get("shells")))
json.dump(rows, open("/tmp/STUDY_A2_AVAILABILITY.json", "w"), indent=1)
print("wrote /tmp/STUDY_A2_AVAILABILITY.json")
