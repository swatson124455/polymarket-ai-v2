#!/usr/bin/env python3
"""OBS_HOLD gate evaluation (handoff §10 Pre-registration 2; weekly timer).

Scores the pre-registered arming-gate criteria against live data — the gate was OVERRIDDEN
by operator naming on 2026-08-12 ("turn all on"), so this runs as TELEMETRY: it tells the
operator whether the armed hold's floor actually separates payers from non-payers.

Pre-registered criteria (verbatim from §10):
  - sample: fresh allowlist tickers first sized after T0 whose event's programs CONCLUDED
  - UNPOWERED unless n>=10 concluded fresh-ticker events AND >=2 events PAID >= $1
  - FALSE-BENCH rate = fraction of PAID(>=$1) events where NO strike reached accrued
    >= $1.20 within its first OBS_HOLD_FRESH_S (24h) — the events the hold hurt.
    PASS iff <= 10%.
  - SENSOR FIDELITY: every PAID event must have paid >= 0.5 x accrued-at-conclusion
    (the KXAPRPOTUS 0.481 over-prediction is the known floor).

READ-ONLY. Appends one JSON row per run to obs_gate_eval-YYYYMM.jsonl.
Exit: 0 on any verdict (UNPOWERED/PASS/FAIL are data, not faults); 1 on operational alarms
(missing tapes/inputs — the §6.1 machine-visibility doctrine).
"""
import datetime as dt
import glob
import json
import os
import re
import sys

DATA = os.environ.get("KALSHI_DATA_DIR", "/opt/pa2-maker-kalshi-live")
T0 = os.environ.get("KALSHI_OBS_GATE_T0", "2026-08-12T01:40:43+00:00")
FRESH_S = float(os.environ.get("KALSHI_OBS_HOLD_FRESH_S", "86400"))
FLOOR = float(os.environ.get("KALSHI_OBS_HOLD_MIN_USD", "1.20"))
PAID_MIN = 1.00
_EVENT_RE = re.compile(r"for event (\S+)")
_ALARMS = [0]


def _alarm(msg):
    _ALARMS[0] += 1
    print(f"# ALARM {msg}", flush=True)


def parse_iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def evaluate(rows, n_min=10, payers_min=2, false_bench_max=0.10, fidelity_min=0.5):
    """Pure gate arithmetic. rows: [{event, paid_usd, any_floor_within_fresh(bool),
    accrued_at_conclusion}] — concluded events only. Returns the verdict dict."""
    n = len(rows)
    paid = [r for r in rows if r["paid_usd"] >= PAID_MIN]
    out = {"n_concluded": n, "n_paid": len(paid)}
    if n < n_min or len(paid) < payers_min:
        out["verdict"] = "UNPOWERED"
        return out
    benched = [r for r in paid if not r["any_floor_within_fresh"]]
    out["false_bench_rate"] = round(len(benched) / len(paid), 3)
    out["false_bench_events"] = [r["event"] for r in benched]
    bad_fid = [r for r in paid if r["accrued_at_conclusion"] > 0
               and r["paid_usd"] < fidelity_min * r["accrued_at_conclusion"]]
    out["fidelity_fail_events"] = [r["event"] for r in bad_fid]
    out["verdict"] = ("PASS" if out["false_bench_rate"] <= false_bench_max
                      and not bad_fid else "FAIL")
    return out


def main():
    now = dt.datetime.now(dt.timezone.utc)
    t0 = parse_iso(T0)
    print(f"# OBS_HOLD gate eval — {now.isoformat()}  (T0={T0}, floor=${FLOOR}, "
          f"fresh={FRESH_S / 3600:.0f}h)")
    # 1. fresh tickers: first SIZED quote row after T0, + first-seen ts
    first_seen, first_sized = {}, {}
    qfiles = sorted(glob.glob(os.path.join(DATA, "quotes-2026*.jsonl")))
    if not qfiles:
        _alarm("no quotes tapes found — sample is EMPTY")
    for qf in qfiles:
        for line in open(qf):
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("ticker")
            if not t:
                continue
            first_seen.setdefault(t, d.get("ts"))
            if t not in first_sized and ((d.get("y_ct") or 0) or (d.get("n_ct") or 0)):
                first_sized[t] = d.get("ts")
    fresh = {t: ts for t, ts in first_sized.items()
             if ts and parse_iso(ts) >= t0 and parse_iso(first_seen[t]) >= t0}
    # 2. program map: event -> latest program end; ticker -> program ids
    pmap = json.load(open(os.path.join(DATA, "kalshi_program_map.json")))
    prog_of_ticker = {}
    end_of_event = {}
    for pid, pr in pmap.items():
        mt = (pr or {}).get("market_ticker")
        if not mt:
            continue
        prog_of_ticker.setdefault(mt, []).append(pid)
        ev = mt.rsplit("-", 1)[0]
        end = pr.get("end_date") or ""
        if end > end_of_event.get(ev, ""):
            end_of_event[ev] = end
    # 3. estimates tape: per program, first ts accrued >= FLOOR + running per-event sum
    floor_ts = {}
    ev_accrued = {}
    efiles = sorted(glob.glob(os.path.join(DATA, "estimates-2026*.jsonl")))
    if not efiles:
        _alarm("no estimates tapes found — floor crossings unmeasurable")
    for ef in efiles:
        for line in open(ef):
            try:
                snap = json.loads(line)
            except Exception:
                continue
            ts = snap.get("ts")
            per_ev = {}
            for e in snap.get("estimates") or []:
                pid = str(e.get("program_id"))
                v = (e.get("reward_centicents") or 0) / 10000.0
                mt = (pmap.get(pid) or {}).get("market_ticker")
                if not mt:
                    continue
                if v >= FLOOR and pid not in floor_ts:
                    floor_ts[pid] = ts
                ev = mt.rsplit("-", 1)[0]
                per_ev[ev] = per_ev.get(ev, 0.0) + v
            for ev, tot in per_ev.items():
                # accrued-at-conclusion approximated by the last snapshot's event sum
                ev_accrued[ev] = tot
    # 4. paid by event
    sys.path.insert(0, DATA)
    os.chdir(DATA)
    from maker_kalshi_client import KalshiOrderClient
    credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
    paid_ev = {}
    for c in credits:
        m = _EVENT_RE.search(c.get("reason") or "")
        if m:
            paid_ev[m.group(1)] = paid_ev.get(m.group(1), 0.0) + (c["amount_cents"] / 100.0)
    # 5. assemble concluded fresh events
    ev_rows = {}
    for t, sized_ts in fresh.items():
        ev = t.rsplit("-", 1)[0]
        end = end_of_event.get(ev)
        if not end or parse_iso(end) > now:
            continue                                   # pending, never counted (F4 lesson)
        r = ev_rows.setdefault(ev, {"event": ev, "paid_usd": paid_ev.get(ev, 0.0),
                                    "any_floor_within_fresh": False,
                                    "accrued_at_conclusion": ev_accrued.get(ev, 0.0)})
        for pid in prog_of_ticker.get(t, []):
            fts = floor_ts.get(pid)
            if fts and (parse_iso(fts) - parse_iso(first_seen[t])).total_seconds() <= FRESH_S:
                r["any_floor_within_fresh"] = True
    rows = list(ev_rows.values())
    out = evaluate(rows)
    out["ts"] = now.isoformat()
    out["fresh_tickers"] = len(fresh)
    print(json.dumps(out, indent=1))
    with open(os.path.join(DATA, "obs_gate_eval-%s.jsonl" % now.strftime("%Y%m")), "a") as fh:
        fh.write(json.dumps({**out, "rows": rows}, separators=(",", ":")) + "\n")
    sys.exit(1 if _ALARMS[0] else 0)


if __name__ == "__main__":
    main()
