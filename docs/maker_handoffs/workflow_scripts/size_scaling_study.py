"""SIZE-vs-ACCRUAL scaling study ($0, tape-only). The one missing parameter.
Join: quotes-*.jsonl (per-cycle OUR resting ct/price/ref per market) x estimates
tape (hourly cumulative accrual per program) over 2026-08-06..08-12 + R1 probe.
Output: accrual-$/day rate bucketed by our two-sided resting size and tightness."""
import sys, json, glob, os, gzip, datetime
from collections import defaultdict
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
os.chdir("/opt/pa2-maker-kalshi-live")
from reward_pnl_report import parse_iso

pm = json.load(open("kalshi_program_map.json"))
tk2pid = {}
for pid, pr in pm.items():
    t = pr.get("market_ticker")
    if t:
        tk2pid[t] = str(pid)

# ---- accrual per program per hour (delta of cumulative) ----
acc = defaultdict(dict)              # pid -> hour_iso -> cumulative (last in hour)
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
        hour = ts[:13]
        for e in snap.get("estimates") or []:
            acc[str(e.get("program_id"))][hour] = \
                float(e.get("reward_centicents") or 0) / 10000.0

def hour_delta(pid, hour):
    """accrual gained IN this hour = cum(this hour) - cum(previous available hour)."""
    hours = acc.get(pid)
    if not hours or hour not in hours:
        return None
    prev = [h for h in hours if h < hour]
    if not prev:
        return None
    return max(hours[hour] - hours[max(prev)], 0.0)

# ---- our presence per (ticker, hour) from quotes tapes ----
pres = defaultdict(lambda: {"cyc": 0, "two": 0, "ct": 0.0, "off": 0.0, "offn": 0})
files = sorted(glob.glob("quotes-*.jsonl") + glob.glob("quotes-*.jsonl.gz"))
rows = 0
for fp in files:
    op = gzip.open if fp.endswith(".gz") else open
    with op(fp, "rt") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            rows += 1
            t, ts = r.get("ticker"), r.get("ts") or ""
            if not t or len(ts) < 13:
                continue
            k = (t, ts[:13])
            p = pres[k]
            p["cyc"] += 1
            yct, nct = float(r.get("y_rest_ct") or 0), float(r.get("n_rest_ct") or 0)
            if yct > 0 and nct > 0:
                p["two"] += 1
                p["ct"] += (yct + nct) / 2.0
                for side in ("y", "n"):
                    px, ref = r.get(side + "_px"), r.get(side + "_ref")
                    if px is not None and ref is not None:
                        p["off"] += max(0.0, round((ref - px) / 0.01))
                        p["offn"] += 1

# ---- join ----
buckets = defaultdict(lambda: {"h": 0, "acc": 0.0})
best = []
joined = 0
for (t, hour), p in pres.items():
    if p["two"] == 0:
        continue
    pid = tk2pid.get(t)
    if not pid:
        continue
    d = hour_delta(pid, hour)
    if d is None:
        continue
    joined += 1
    mean_ct = p["ct"] / p["two"]
    two_frac = p["two"] / p["cyc"]
    off = (p["off"] / p["offn"]) if p["offn"] else -1
    sb = ("a_1-9" if mean_ct < 10 else "b_10-29" if mean_ct < 30 else
          "c_30-59" if mean_ct < 60 else "d_60-134" if mean_ct < 135 else "e_135+")
    ob = "t_at_ref" if off <= 0.5 else ("u_1-2off" if off <= 2.5 else "v_3+off")
    fb = "full" if two_frac > 0.7 else "part"
    buckets[(sb, ob, fb)]["h"] += 1
    buckets[(sb, ob, fb)]["acc"] += d
    best.append((d, t, hour, round(mean_ct, 1), round(off, 1), round(two_frac, 2)))

print("quote rows:", rows, "| joined ticker-hours (two-sided presence):", joined)
print("%-10s %-9s %-5s %6s %12s %14s" % ("size_ct", "ticks_off", "pres", "hours",
                                          "acc$/hour", "implied $/day"))
for k in sorted(buckets):
    b = buckets[k]
    rate = b["acc"] / b["h"]
    print("%-10s %-9s %-5s %6d %12.5f %14.2f" % (k[0], k[1], k[2], b["h"], rate,
                                                  rate * 24))
best.sort(reverse=True)
print()
print("top 12 single ticker-hours by accrual gained:")
for d, t, hour, ct, off, tf in best[:12]:
    print("  %.4f  %-34s %s ct=%s off=%s pres=%s" % (d, t, hour, ct, off, tf))
