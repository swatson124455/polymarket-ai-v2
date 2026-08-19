"""SIZE-vs-ACCRUAL v2 — hardened per review F1-F8. $0, tape-only.
F1: deltas only between ADJACENT hours; joined/total presence printed.
F2: per market-DAY sums reported (not x24 of selected hours).
F3: bucket by min(y,n) resting ct.
F4: distinct tickers + ticker-days per cell.
F5: ts formats asserted on first rows.
F6: ticker->pid collisions counted.
F7: within-ticker size contrast (same ticker, small vs large hours).
F8: survivable-allowlist-only subset (D3 measured classes)."""
import sys, json, glob, os, gzip, datetime
from collections import defaultdict
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
os.chdir("/opt/pa2-maker-kalshi-live")

SURVIVABLE = {"KXAAAGASD", "KXAAAGASW", "KXTOPMODEL", "KXCLAYTONDNI",
              "KXDIESELW", "KXUSDJPY", "KXCLARITYVOTE"}   # D3 §c measured list

pm = json.load(open("kalshi_program_map.json"))
tk2pids = defaultdict(list)
for pid, pr in pm.items():
    t = pr.get("market_ticker")
    if t:
        tk2pids[t].append(str(pid))
collisions = sum(1 for t, ps in tk2pids.items() if len(ps) > 1)

acc = defaultdict(dict)
first_est_ts = None
for fp in sorted(glob.glob("estimates-*.jsonl")):
    for ln in open(fp):
        if not ln.strip():
            continue
        try:
            snap = json.loads(ln)
        except ValueError:
            continue
        ts = snap.get("ts")
        if not ts:
            continue
        if first_est_ts is None:
            first_est_ts = ts
        for e in snap.get("estimates") or []:
            acc[str(e.get("program_id"))][ts[:13]] = \
                float(e.get("reward_centicents") or 0) / 10000.0

def adjacent_delta(pid, hour):
    hours = acc.get(pid)
    if not hours or hour not in hours:
        return None
    dt = datetime.datetime.strptime(hour, "%Y-%m-%dT%H")
    prev = (dt - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H")
    if prev not in hours:
        return None                     # F1: no gap-spanning deltas
    return max(hours[hour] - hours[prev], 0.0)

pres = defaultdict(lambda: {"cyc": 0, "two": 0, "minct": 0.0})
first_q_ts = None
for fp in sorted(glob.glob("quotes-*.jsonl") + glob.glob("quotes-*.jsonl.gz")):
    op = gzip.open if fp.endswith(".gz") else open
    with op(fp, "rt") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t, ts = r.get("ticker"), r.get("ts") or ""
            if not t or len(ts) < 13:
                continue
            if first_q_ts is None:
                first_q_ts = ts
            k = (t, ts[:13])
            p = pres[k]
            p["cyc"] += 1
            yct, nct = float(r.get("y_rest_ct") or 0), float(r.get("n_rest_ct") or 0)
            if yct > 0 and nct > 0:
                p["two"] += 1
                p["minct"] += min(yct, nct)

print("ts formats: est=%r quotes=%r (both must be ...T##:.. UTC ISO)" %
      (first_est_ts[:20], first_q_ts[:20]))
print("ticker->pid collisions (F6):", collisions)

total_two = sum(1 for p in pres.values() if p["two"] > 0)
cells = defaultdict(lambda: {"h": 0, "acc": 0.0, "tk": set(), "td": set()})
per_ticker_sizes = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
daily = defaultdict(lambda: [0.0, set()])
joined = 0
for (t, hour), p in pres.items():
    if p["two"] == 0:
        continue
    pids = tk2pids.get(t) or []
    d = None
    for pid in pids:                    # sum across sibling programs of same ticker
        dd = adjacent_delta(pid, hour)
        if dd is not None:
            d = (d or 0.0) + dd
    if d is None:
        continue
    joined += 1
    minct = p["minct"] / p["two"]
    sb = ("a_1-9" if minct < 10 else "b_10-29" if minct < 30 else
          "c_30-59" if minct < 60 else "d_60+")
    surv = t.split("-")[0] in SURVIVABLE
    for key in (("all", sb), ("SURV" if surv else "toxicish", sb)):
        c = cells[key]
        c["h"] += 1
        c["acc"] += d
        c["tk"].add(t)
        c["td"].add((t, hour[:10]))
    per_ticker_sizes[t][sb][0] += 1
    per_ticker_sizes[t][sb][1] += d
    daily[(t, hour[:10], sb, surv)][0] += d
    daily[(t, hour[:10], sb, surv)][1].add(hour)

print("two-sided presence ticker-hours: %d | joined (adjacent-delta): %d (%.0f%%)" %
      (total_two, joined, 100.0 * joined / max(total_two, 1)))
print("%-22s %6s %9s %8s %8s %12s" % ("cell", "hours", "tickers", "tk-days",
                                       "$/hour", "$/hr*24"))
for key in sorted(cells):
    c = cells[key]
    rate = c["acc"] / c["h"]
    print("%-22s %6d %9d %8d %8.5f %12.2f" % ("/".join(key), c["h"], len(c["tk"]),
                                               len(c["td"]), rate, rate * 24))
print()
print("per MARKET-DAY accrual sums (F2; >=6 joined hours that day):")
md = [(v[0], len(v[1]), t, day, sb, surv) for (t, day, sb, surv), v in daily.items()
      if len(v[1]) >= 6]
md.sort(reverse=True)
for tot, nh, t, day, sb, surv in md[:15]:
    print("  %.4f over %2dh  %-34s %s %s %s" %
          (tot, nh, t, day, sb, "SURV" if surv else "tox"))
print()
print("within-ticker size contrast (F7; tickers with hours in BOTH a/b and c):")
for t, sizes in sorted(per_ticker_sizes.items()):
    small_h = sizes["a_1-9"][0] + sizes["b_10-29"][0]
    small_a = sizes["a_1-9"][1] + sizes["b_10-29"][1]
    big_h, big_a = sizes["c_30-59"][0], sizes["c_30-59"][1]
    if small_h >= 3 and big_h >= 3:
        print("  %-34s small %.5f/h (n=%d)  big %.5f/h (n=%d)  ratio %.1fx" %
              (t, small_a / small_h, small_h, big_a / big_h, big_h,
               (big_a / big_h) / max(small_a / small_h, 1e-9)))
