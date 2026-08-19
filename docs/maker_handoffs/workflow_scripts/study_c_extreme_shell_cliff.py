#!/usr/bin/env python3
"""STUDY C (read-only, $0): does the $1-cliff pay in the EXTREME-PRICE shell?

Widens the n=1 existence proof: every concluded program on the estimates tape is
classified by its market's price shell at conclusion (hourly candle mid at the
last candle <= program end), then the cliff-canon payment test is read per shell.
Base logic = the archived r1_cliff_hypothesis_test.py (per-program accrued at
conclusion, event-level paid match); the addition is the shell classification.
"""
import datetime
import glob
import json
import os
import sys
import time

sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
os.chdir("/opt/pa2-maker-kalshi-live")
from reward_pnl_report import parse_iso, ticker_to_event, credits_by_event  # noqa: E402
from maker_kalshi_client import KalshiOrderClient                           # noqa: E402
import kalshi_attribution_ledger as kal                                     # noqa: E402

now = datetime.datetime.now(datetime.timezone.utc)
pm = json.load(open("kalshi_program_map.json"))
series = {}
for fp in sorted(glob.glob("estimates-*.jsonl")):
    for ln in open(fp):
        if not ln.strip():
            continue
        try:
            snap = json.loads(ln)
        except ValueError:
            continue
        ts = snap.get("ts")
        try:
            tsd = parse_iso(ts) if ts else None
        except Exception:
            continue
        for e in snap.get("estimates") or []:
            series.setdefault(str(e.get("program_id")), []).append(
                (tsd, float(e.get("reward_centicents") or 0) / 10000.0))
credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
paid = credits_by_event(credits)
print("read", now.isoformat(), "| tape programs:", len(series))

progs = []
for pid, ser in series.items():
    pr = pm.get(pid) or {}
    tk, end = pr.get("market_ticker"), pr.get("end_date")
    if not tk or not end:
        continue
    end_dt = parse_iso(end)
    if end_dt > now - datetime.timedelta(hours=48):
        continue                                # payment envelope still open
    best = (None, 0.0)
    for tsd, v in ser:
        if tsd and tsd <= end_dt and (best[0] is None or tsd > best[0]):
            best = (tsd, v)
    if best[1] < 0.005:
        continue                                # dust: no accrual to test
    progs.append({"ticker": tk, "end": end_dt, "acc": round(best[1], 4)})

print("programs with accrual and closed envelopes:", len(progs))


def shell_of(mid):
    if mid is None:
        return "unknown"
    if mid <= 0.05 or mid >= 0.95:
        return "extreme_05"
    if mid <= 0.10 or mid >= 0.90:
        return "extreme_10"
    if mid <= 0.15 or mid >= 0.85:
        return "shell_10_15"
    return "mid_15_85"


cache = {}
for p in progs:
    t = p["ticker"]
    key = (t, int(p["end"].timestamp()) // 3600)
    if key in cache:
        p["mid"] = cache[key]
        continue
    s = t.split("-")[0]
    end_ts = int(p["end"].timestamp())
    try:
        d = kal.get(f"{kal.P}/series/{s}/markets/{t}/candlesticks"
                    f"?start_ts={end_ts - 86400}&end_ts={end_ts}&period_interval=60")
        cs = d.get("candlesticks") or []
        mid = None
        for c in reversed(cs):
            try:
                bid = float(((c.get("yes_bid") or {}).get("close_dollars")) or 0)
                ask = float(((c.get("yes_ask") or {}).get("close_dollars")) or 1)
            except (TypeError, ValueError):
                continue
            if bid > 0 or ask < 1:
                mid = (bid + ask) / 2.0
                break
    except Exception:
        mid = None
    cache[key] = mid
    p["mid"] = mid
    time.sleep(0.12)

# event-level paid vs predicted, then attribute the verdict to each above-cliff program
by_event = {}
for p in progs:
    by_event.setdefault(ticker_to_event(p["ticker"]), []).append(p)

table = {}
for ev, ps in by_event.items():
    p_act = (paid.get(ev) or {}).get("paid", 0.0)
    pred = sum(p["acc"] for p in ps if p["acc"] >= 1.0)
    ev_match = abs(pred - p_act) < 0.02
    for p in ps:
        sh = shell_of(p.get("mid"))
        row = table.setdefault(sh, {"n": 0, "above_cliff": 0, "above_paid_ok": 0,
                                    "sub": 0, "acc_sum": 0.0})
        row["n"] += 1
        row["acc_sum"] += p["acc"]
        if p["acc"] >= 1.0:
            row["above_cliff"] += 1
            if ev_match and pred > 0:
                row["above_paid_ok"] += 1
        else:
            row["sub"] += 1

print("shell | programs | above-cliff | above-cliff-in-MATCHED-event | sub-$1 | accrued sum")
for sh, r in sorted(table.items()):
    print("%-11s %5d %8d %12d %10d   $%.2f"
          % (sh, r["n"], r["above_cliff"], r["above_paid_ok"], r["sub"], r["acc_sum"]))
ex = [p for p in progs if shell_of(p.get("mid")) in ("extreme_05", "extreme_10")
      and p["acc"] >= 1.0]
print()
print("ABOVE-CLIFF PROGRAMS IN EXTREME SHELLS (the existence-proof class):")
for p in sorted(ex, key=lambda x: -x["acc"]):
    ev = ticker_to_event(p["ticker"])
    print("  %-34s acc $%.4f mid %s end %s | event paid $%.2f"
          % (p["ticker"], p["acc"],
             None if p.get("mid") is None else round(p["mid"], 3),
             p["end"].isoformat()[:16], (paid.get(ev) or {}).get("paid", 0.0)))
json.dump([{**p, "end": p["end"].isoformat()} for p in progs],
          open("/tmp/STUDY_C_SHELLS.json", "w"), indent=1, default=str)
print("wrote /tmp/STUDY_C_SHELLS.json")
