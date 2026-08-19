#!/usr/bin/env python3
"""REWARD P&L REPORT (item F, operator plan 2026-08-11). GET-only; changes nothing.

The forward-looking earnings gauge: the estimates feed predicts its own credits to the
cent (two pre-registered passes: KXAPRPOTUS $1.6319->$1.63, KXTOPMODEL-26AUG31
$2.4691->$2.46), so accrued-vs-paid BY EVENT is the honest daily answer to "are we
earning" — instead of the lagging credit read alone.

Per event: accrued (latest recorder snapshot, joined via the merge-only program map),
paid (credit_history reason string), and a status that NEVER conflates pending with
leakage (the F4 lesson, self-review 2026-08-11):
    EARNING   accrued > 0, program still running
    PENDING   program concluded, inside the payment envelope, not yet paid
    PAID      credits landed (ratio reported; PARTIAL if paid < PARTIAL_FRAC * accrued)
    LEAKAGE   program concluded > LEAK_AFTER_H hours ago, accrued >= LEAK_MIN_USD, $0 paid
    DUST      accrued below LEAK_MIN_USD and nothing paid (sub-floor, expected $0)

Run on the box:  ./venv/bin/python reward_pnl_report.py   (needs KALSHI_* env like the
recorders; systemd EnvironmentFile= or a root shell that sourced live.env).
"""
import datetime
import glob
import json
import os
import re
import sys

DATA = os.environ.get("KALSHI_DATA_DIR", "/opt/pa2-maker-kalshi-live")
PARTIAL_FRAC = 0.90
LEAK_MIN_USD = 0.50
CLIFF_USD = 1.00             # per-PROGRAM payment cliff (canon 2026-08-18, 38/38 backtest)
LEAK_AFTER_H = 48.0          # observed payment envelope; FIX-H paid at ~38.4h
_EVENT_RE = re.compile(r"for event (\S+)")


def parse_iso(s):
    """Venue timestamps are UTC; a naive value (no offset) is attached UTC rather than
    poisoning aware-vs-naive comparisons with TypeError downstream (review F5, 2026-08-13)."""
    dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def ticker_to_event(t):
    """THE event-derivation rule for market tickers (single exported home, review F6/F10):
    strip the strike segment. Known limit (A-2, kalshi_credit_feedback): 4-field legacy
    tickers and strikeless market tickers fragment — callers that can must prefer venue
    metadata; string-rule consumers must at least share THIS one copy."""
    return t.rsplit("-", 1)[0]


def credits_by_event(credits):
    """{event: {'paid': $, 'n': int, 'last': iso}} from credit_history rows. Rows whose
    reason doesn't carry an event (deposits, promos) are returned under '?' — counted,
    never silently dropped, never attributed to a series."""
    out = {}
    for c in credits or []:
        m = _EVENT_RE.search(c.get("reason") or "")
        ev = m.group(1) if m else "?"
        r = out.setdefault(ev, {"paid": 0.0, "n": 0, "last": None})
        r["paid"] += (c.get("amount_cents") or 0) / 100.0
        r["n"] += 1
        ts = c.get("created_at")
        if ts and (r["last"] is None or ts > r["last"]):
            r["last"] = ts
    return out


def history_at_conclusion(lines, program_map, now):
    """{program_id: {'accrued': $ at conclusion, 'end': iso}} for CONCLUDED programs,
    from the FULL estimates tape (iterable of raw jsonl lines).

    THE 2026-08-18 GAUGE FIX: concluded programs VANISH from the live snapshot, so
    the latest-snapshot read made every stiffed event invisible (historical
    n_leakage: 0 was vacuous — proven against the $1-cliff backtest). The last
    tape value at/before each program's end is the honest accrued-at-conclusion."""
    out = {}
    for ln in lines:
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
        if tsd is None:
            continue
        for e in snap.get("estimates") or []:
            pid = str(e.get("program_id"))
            pr = (program_map or {}).get(pid) or {}
            end = pr.get("end_date")
            if not end:
                continue
            try:
                end_dt = parse_iso(end)
            except Exception:
                continue
            if end_dt > now or tsd > end_dt:
                continue
            cur = out.get(pid)
            if cur is None or tsd > cur["_ts"]:
                out[pid] = {"accrued": float(e.get("reward_centicents") or 0) / 10000.0,
                            "end": end, "_ts": tsd}
    for v in out.values():
        v.pop("_ts", None)
    return out


def merge_history_events(acc, history, program_map, live_pids):
    """Fold concluded-program at-conclusion values into the event dict wherever the
    program is ABSENT from the live snapshot (present ones are already counted)."""
    for pid, h in (history or {}).items():
        if pid in live_pids:
            continue
        pr = (program_map or {}).get(pid) or {}
        t = pr.get("market_ticker")
        ev = ticker_to_event(t) if t else "?"
        r = acc.setdefault(ev, {"accrued": 0.0, "end": None, "max_prog": 0.0})
        r["accrued"] += h["accrued"]
        r["max_prog"] = max(r.get("max_prog", 0.0), h["accrued"])
        if h["end"] and (r["end"] is None or h["end"] > r["end"]):
            r["end"] = h["end"]
    return acc


def accrued_by_event(snapshot, program_map):
    """{event: {'accrued': $, 'end': latest program end iso}} from ONE estimates snapshot.
    Unmapped program_ids are summed under '?' so map gaps are visible, not silent."""
    out = {}
    for e in (snapshot or {}).get("estimates") or []:
        pr = (program_map or {}).get(str(e.get("program_id"))) or {}
        t = pr.get("market_ticker")
        ev = ticker_to_event(t) if t else "?"
        r = out.setdefault(ev, {"accrued": 0.0, "end": None, "max_prog": 0.0})
        v = float(e.get("reward_centicents") or 0) / 10000.0
        r["accrued"] += v
        r["max_prog"] = max(r["max_prog"], v)
        end = pr.get("end_date")
        if end and (r["end"] is None or end > r["end"]):
            r["end"] = end
    return out


def classify(ev, acc, paid_row, now):
    """One event's status. acc = {'accrued','end'} or None; paid_row = credits row or None.
    PENDING vs LEAKAGE turns ONLY on the program clock — never on impatience."""
    accrued = (acc or {}).get("accrued", 0.0)
    paid = (paid_row or {}).get("paid", 0.0)
    if paid > 0:
        base = accrued if accrued > 0 else None
        partial = base is not None and paid < PARTIAL_FRAC * base
        return ("PAID_PARTIAL" if partial else "PAID", accrued, paid)
    end = (acc or {}).get("end")
    if not end:
        return ("DUST" if accrued < LEAK_MIN_USD else "UNKNOWN_END", accrued, paid)
    try:
        concluded = parse_iso(end) <= now
        hours_past = (now - parse_iso(end)).total_seconds() / 3600.0
    except Exception:
        return ("UNKNOWN_END", accrued, paid)
    if not concluded:
        return ("EARNING" if accrued > 0 else "DUST", accrued, paid)
    if accrued < LEAK_MIN_USD:
        return ("DUST", accrued, paid)
    if hours_past <= LEAK_AFTER_H:
        return ("PENDING", accrued, paid)
    # $1-cliff canon (2026-08-18): the cliff is PER-PROGRAM — an event of several
    # sub-$1 programs summing over $1 is still expected $0 (ACTBLUETOP shape).
    # Alarm-worthy leakage = at least ONE program cleared the cliff, still unpaid.
    if (acc or {}).get("max_prog", accrued) < CLIFF_USD:
        return ("SUBCLIFF", accrued, paid)
    return ("LEAKAGE", accrued, paid)


def build_report(snapshot, program_map, credits, now, history=None):
    acc = accrued_by_event(snapshot, program_map)
    live_pids = {str(e.get("program_id")) for e in (snapshot or {}).get("estimates") or []}
    acc = merge_history_events(acc, history, program_map, live_pids)
    paid = credits_by_event(credits)
    rows = []
    for ev in sorted(set(acc) | set(paid)):
        status, a, p = classify(ev, acc.get(ev), paid.get(ev), now)
        rows.append({"event": ev, "status": status, "accrued": round(a, 4),
                     "paid": round(p, 2), "end": (acc.get(ev) or {}).get("end"),
                     "ratio": round(p / a, 3) if a > 0 and p > 0 else None})
    totals = {"accrued_open": round(sum(r["accrued"] for r in rows
                                        if r["status"] in ("EARNING", "PENDING")), 4),
              "paid_lifetime": round(sum(r["paid"] for r in rows), 2),
              "n_leakage": sum(1 for r in rows if r["status"] == "LEAKAGE"),
              "n_subcliff": sum(1 for r in rows if r["status"] == "SUBCLIFF"),
              "n_paid_partial": sum(1 for r in rows if r["status"] == "PAID_PARTIAL")}
    return {"ts": now.isoformat(), "totals": totals, "rows": rows}


def main():
    sys.path.insert(0, DATA)
    os.chdir(DATA)
    from maker_kalshi_client import KalshiOrderClient
    now = datetime.datetime.now(datetime.timezone.utc)
    files = sorted(glob.glob(os.path.join(DATA, "estimates-*.jsonl")))
    last = None
    if files:
        with open(files[-1]) as fh:
            for ln in fh:
                if ln.strip():
                    last = ln
    snapshot = json.loads(last) if last else {}
    program_map = json.load(open(os.path.join(DATA, "kalshi_program_map.json")))
    def _all_lines():
        for fp in files:
            with open(fp) as fh:
                yield from fh
    history = history_at_conclusion(_all_lines(), program_map, now)
    credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
    rep = build_report(snapshot, program_map, credits, now, history=history)
    print(json.dumps(rep["totals"]))
    order = {"LEAKAGE": 0, "PAID_PARTIAL": 1, "PENDING": 2, "EARNING": 3,
             "UNKNOWN_END": 4, "PAID": 5, "SUBCLIFF": 6, "DUST": 7}
    for r in sorted(rep["rows"], key=lambda r: (order.get(r["status"], 9), -r["accrued"])):
        if r["status"] == "DUST" and r["paid"] == 0:
            continue                                   # sub-floor noise; totals still count it
        print("%-13s %-34s accrued %8.4f  paid %7.2f  ratio %-5s end %s"
              % (r["status"], r["event"], r["accrued"], r["paid"],
                 r["ratio"] if r["ratio"] is not None else "-", r["end"] or "-"))
    out = os.path.join(DATA, "reward_pnl-%s.json" % now.strftime("%Y%m"))
    with open(out, "a") as fh:
        fh.write(json.dumps(rep, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
