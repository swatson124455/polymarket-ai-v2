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
LEAK_AFTER_H = 48.0          # observed payment envelope; FIX-H paid at ~38.4h
_EVENT_RE = re.compile(r"for event (\S+)")


def parse_iso(s):
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


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


def accrued_by_event(snapshot, program_map):
    """{event: {'accrued': $, 'end': latest program end iso}} from ONE estimates snapshot.
    Unmapped program_ids are summed under '?' so map gaps are visible, not silent."""
    out = {}
    for e in (snapshot or {}).get("estimates") or []:
        pr = (program_map or {}).get(str(e.get("program_id"))) or {}
        t = pr.get("market_ticker")
        ev = t.rsplit("-", 1)[0] if t else "?"
        r = out.setdefault(ev, {"accrued": 0.0, "end": None})
        r["accrued"] += float(e.get("reward_centicents") or 0) / 10000.0
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
    return ("LEAKAGE" if hours_past > LEAK_AFTER_H else "PENDING", accrued, paid)


def build_report(snapshot, program_map, credits, now):
    acc = accrued_by_event(snapshot, program_map)
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
    credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
    rep = build_report(snapshot, program_map, credits, now)
    print(json.dumps(rep["totals"]))
    order = {"LEAKAGE": 0, "PAID_PARTIAL": 1, "PENDING": 2, "EARNING": 3,
             "UNKNOWN_END": 4, "PAID": 5, "DUST": 6}
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
