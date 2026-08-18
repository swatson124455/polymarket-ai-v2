"""Paid-vs-accrued ratio study from EXISTING tape ($0, read-only).
For every program with estimates-tape history whose end_date + 48h payment
envelope has passed: accrued-at-conclusion (last snapshot value at/before end)
vs credits PAID for its event (credit_history). Also: has any sub-$1 accrual
ever been paid?"""
import sys, json, glob, os, datetime
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
os.chdir("/opt/pa2-maker-kalshi-live")
import re
from reward_pnl_report import parse_iso, ticker_to_event, credits_by_event
from maker_kalshi_client import KalshiOrderClient
import kalshi_attribution_ledger as kal

now = datetime.datetime.now(datetime.timezone.utc)
pm = json.load(open("kalshi_program_map.json"))

# accrual series per program: keep LAST value at or before program end
series = {}
for fp in sorted(glob.glob("estimates-*.jsonl")):
    with open(fp) as fh:
        for ln in fh:
            if not ln.strip():
                continue
            try:
                snap = json.loads(ln)
            except ValueError:
                continue
            ts = snap.get("ts")
            for e in snap.get("estimates") or []:
                pid = str(e.get("program_id"))
                v = float(e.get("reward_centicents") or 0) / 10000.0
                series.setdefault(pid, []).append((ts, v))

credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
paid = credits_by_event(credits)
read_ts = now.isoformat()

rows = []
for pid, ser in series.items():
    pr = pm.get(pid) or {}
    tk, end = pr.get("market_ticker"), pr.get("end_date")
    if not tk or not end:
        continue
    end_dt = parse_iso(end)
    if end_dt > now - datetime.timedelta(hours=48):
        continue                     # payment envelope not yet closed
    ev = ticker_to_event(tk)
    acc_end = 0.0
    for ts, v in ser:
        if ts and parse_iso(ts) <= end_dt:
            acc_end = v              # series is chronological per file order
    rows.append({"pid": pid, "ticker": tk, "event": ev, "end": end,
                 "accrued_at_end": round(acc_end, 4)})

# aggregate per EVENT (credits are event-level; sibling programs sum)
ev_rows = {}
for r in rows:
    e = ev_rows.setdefault(r["event"], {"accrued": 0.0, "n_programs": 0,
                                        "end": r["end"]})
    e["accrued"] += r["accrued_at_end"]
    e["n_programs"] += 1
print("read_ts", read_ts, "| programs with concluded+enveloped history:", len(rows),
      "| events:", len(ev_rows))
print("%-34s %9s %9s %6s %s" % ("event", "accrued", "paid", "ratio", "n_prog"))
n_ratio, ratios = 0, []
sub1_paid = sub1_unpaid = over1_paid = over1_unpaid = 0
for ev, r in sorted(ev_rows.items(), key=lambda x: -x[1]["accrued"]):
    p = (paid.get(ev) or {}).get("paid", 0.0)
    a = r["accrued"]
    ratio = (p / a) if a > 0.005 else None
    if a > 0.005:
        if a < 1.0:
            sub1_paid += (1 if p > 0 else 0); sub1_unpaid += (1 if p == 0 else 0)
        else:
            over1_paid += (1 if p > 0 else 0); over1_unpaid += (1 if p == 0 else 0)
        if p > 0:
            ratios.append(ratio); n_ratio += 1
    if a > 0.005 or p > 0:
        print("%-34s %9.4f %9.2f %6s %5d" % (ev[:34], a, p,
              ("%.3f" % ratio) if ratio is not None else "-", r["n_programs"]))
print()
print("EVENTS accrued>=\\$1 at end: paid %d / unpaid %d" % (over1_paid, over1_unpaid))
print("EVENTS accrued <\\$1 (>0) at end: paid %d / unpaid %d" % (sub1_paid, sub1_unpaid))
if ratios:
    ratios.sort()
    print("ratio (paid/accrued) over %d paid events: min %.3f median %.3f max %.3f"
          % (n_ratio, ratios[0], ratios[len(ratios)//2], ratios[-1]))
