"""THE script that produced the $1-cliff canon table (per-program predictions).
Archived per review F5 — the earlier r1_ratio_study.py aggregates per EVENT and
cannot reproduce the canon doc's table. Run on the box with live.env sourced.

Review F6/F7/F8 hardening: per-program snapshot staleness reported (last_ts vs
end); programs > 2 recorder intervals stale flagged UNKNOWN; unmapped-pid count
printed; series ordered by max ts, not file iteration; malformed ts tolerated."""
import sys, json, glob, os, datetime
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
os.chdir("/opt/pa2-maker-kalshi-live")
from reward_pnl_report import parse_iso, ticker_to_event, credits_by_event
from maker_kalshi_client import KalshiOrderClient

STALE_S = 2 * 3600 + 600          # 2 hourly recorder intervals + slack
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
unmapped = sum(1 for pid in series
               if not ((pm.get(pid) or {}).get("market_ticker")
                       and (pm.get(pid) or {}).get("end_date")))
print("read", now.isoformat(), "| tape programs:", len(series),
      "| dropped (unmapped/one-field):", unmapped)

by_event = {}
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
    stale_s = (end_dt - best[0]).total_seconds() if best[0] else None
    by_event.setdefault(ticker_to_event(tk), []).append(
        {"strike": tk.rsplit("-", 1)[1], "acc": round(best[1], 4),
         "stale_s": None if stale_s is None else int(stale_s)})
print("HYPOTHESIS: paid(event) = sum over its PROGRAMS with accrued-at-end >= $1")
tot_pred = tot_paid = 0.0
n_match = n_events = 0
for ev, progs in sorted(by_event.items(),
                        key=lambda x: -sum(p["acc"] for p in x[1])):
    if sum(p["acc"] for p in progs) < 0.005 and \
            (paid.get(ev) or {}).get("paid", 0.0) == 0:
        continue
    unknown = [p for p in progs if p["stale_s"] is None or p["stale_s"] > STALE_S]
    p_act = (paid.get(ev) or {}).get("paid", 0.0)
    pred = sum(p["acc"] for p in progs if p["acc"] >= 1.0)
    n_events += 1
    ok = abs(pred - p_act) < 0.02
    n_match += ok
    tot_pred += pred
    tot_paid += p_act
    print("%-30s %s pred %.2f actual %.2f %s%s" % (
        ev, [(p["strike"], p["acc"]) for p in progs], pred, p_act,
        "MATCH" if ok else "MISS",
        "  STALE>2int:%d" % len(unknown) if unknown else ""))
print("events %d match %d | TOTAL pred %.2f actual %.2f" %
      (n_events, n_match, tot_pred, tot_paid))
print()
print("F3 check — raw credit rows for the paid events (granularity):")
for c in credits:
    r = c.get("reason") or ""
    if any(k in r for k in ("KXTOPMODEL-26AUG31", "KXAPRPOTUS-26AUG07",
                            "KXADJOURNRECESS-26AUG")):
        print("  ", c.get("created_at"), (c.get("amount_cents") or 0) / 100.0, "|", r[:110])
