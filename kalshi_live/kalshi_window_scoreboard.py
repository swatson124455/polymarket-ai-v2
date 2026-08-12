#!/usr/bin/env python3
"""DAILY WINDOW SCOREBOARD (operator-named 2026-08-12: "1 and 2 gtg"). GET-only; changes nothing.

Running gauge for the pre-registered 7-day window (KALSHI_HANDOFF_2026-08-10_POST_INCIDENT.md
Pre-registration 1; live per §10-C: T0 = 2026-08-12T01:40:43Z). The verdict itself is scored
ONCE, on 2026-08-19, per the handoff §5 procedure — this script only makes the same two gauges
observable DAILY so a losing trajectory is visible mid-window instead of endpoint-only.

    CREDITS gauge  credit_history rows whose event's PROGRAMS concluded inside [T0, T7]
                   (program-map end_date; unmapped/undated rows bucketed, never counted,
                   never silently dropped — the $15 referral lands in the '?' bucket).
    DRAG gauge     position-aware replay_fills cash over fills in [T0, min(now, T7)] plus
                   settlement revenue in the same window (the recorder's basis; bid-mark
                   artifacts excluded by construction).
    pass_now       credits_counted > |drag_total| — TRAJECTORY indicator only, NOT the verdict.

SELF-CHECK: cash identity vs the T0 baseline ($274.4691, recorder 2026-08-12T01:39:03Z, §10-C):
latest recorder cash − T0 cash − credits-paid-since-T0 − drag should be ~0. A large gap means a
number here is wrong OR external money moved (deposit/withdrawal — the ledger's documented
blind spot). WARNING printed, exit stays 0: a legit deposit must not flap the unit daily.

Run on the box: ./venv/bin/python kalshi_window_scoreboard.py  (same env as the recorders).
Appends one row per run to window_scoreboard-YYYYMM.jsonl next to this script.
"""
import datetime
import glob
import json
import os
import sys

DATA = os.environ.get("KALSHI_DATA_DIR", "/opt/pa2-maker-kalshi-live")

T0 = "2026-08-12T01:40:43+00:00"          # §10-C, operator-named restart
T7 = "2026-08-19T01:40:43+00:00"          # T0 + 7d
OBS_DEADLINE = "2026-08-21T01:40:43+00:00"  # T7 + 48h credit-observation envelope
T0_CASH = 274.4691                        # recorder 2026-08-12T01:39:03Z (§10-C baseline)
IDENTITY_WARN_USD = 2.0


def parse_iso(s):
    return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def event_end_map(program_map):
    """{event: latest end_date iso} from the merge-only program map (survives program
    disappearance from the active list — the §5 requirement)."""
    out = {}
    for pr in (program_map or {}).values():
        t = (pr or {}).get("market_ticker")
        end = (pr or {}).get("end_date")
        if not t or not end:
            continue
        ev = t.rsplit("-", 1)[0]
        if ev not in out or end > out[ev]:
            out[ev] = end
    return out


def score_credits(credits_by_ev, ev_end, t0, t7):
    """Bucket per-event paid credits against the window. Returns (counted_usd, buckets)
    where buckets = {counted, pre_t0, post_t7, unmapped} each {'usd', 'events'}."""
    buckets = {k: {"usd": 0.0, "events": []} for k in
               ("counted", "pre_t0", "post_t7", "unmapped")}
    for ev, row in sorted(credits_by_ev.items()):
        paid = row.get("paid", 0.0)
        end = ev_end.get(ev)
        if ev == "?" or end is None:
            k = "unmapped"
        else:
            try:
                e = parse_iso(end)
            except ValueError:
                e = None
            if e is None:
                k = "unmapped"
            elif e < t0:
                k = "pre_t0"
            elif e > t7:
                k = "post_t7"
            else:
                k = "counted"
        buckets[k]["usd"] += paid
        buckets[k]["events"].append(ev)
    for b in buckets.values():
        b["usd"] = round(b["usd"], 2)
    return buckets["counted"]["usd"], buckets


def window_drag(all_events, all_settles, t0, hi):
    """(fills_cash, settle_rev, n_fills, n_setts) inside [t0, hi]. all_events comes from
    replay_fills over the FULL tape (position-aware cash needs the pre-window history;
    every ticker was flat at T0 per §10-C, but full-tape replay stays the canonical form).
    Rows with no timestamp are excluded from the window and COUNTED so a venue field
    rename is visible, never silent (the 07-30 watermark-hazard lesson)."""
    from kalshi_attribution_ledger import settlement_revenue
    fills_cash, n_fills, no_ts = 0.0, 0, 0
    for e in all_events:
        ts = e["fill"].get("created_time")
        if not ts:
            no_ts += 1
            continue
        t = parse_iso(ts)
        if t0 <= t <= hi:
            fills_cash += e["cash"]
            n_fills += 1
    settle_rev, n_setts = 0.0, 0
    for s in all_settles:
        ts = s.get("settled_time")
        if not ts:
            no_ts += 1
            continue
        t = parse_iso(ts)
        if t0 <= t <= hi:
            settle_rev += settlement_revenue(s)
            n_setts += 1
    return fills_cash, settle_rev, n_fills, n_setts, no_ts


def latest_recorder_cash():
    files = sorted(glob.glob(os.path.join(DATA, "cash-*.jsonl")))
    if not files:
        return None, None
    last = None
    with open(files[-1]) as fh:
        for ln in fh:
            if ln.strip():
                last = ln
    if not last:
        return None, None
    row = json.loads(last)
    return row.get("cash"), row.get("ts")


def main():
    sys.path.insert(0, DATA)
    os.chdir(DATA)
    from kalshi_attribution_ledger import get_paginated, replay_fills, P
    from reward_pnl_report import credits_by_event
    from maker_kalshi_client import KalshiOrderClient

    now = datetime.datetime.now(datetime.timezone.utc)
    t0, t7 = parse_iso(T0), parse_iso(T7)
    hi = min(now, t7)

    credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
    by_ev = credits_by_event(credits)
    ev_end = event_end_map(json.load(open(os.path.join(DATA, "kalshi_program_map.json"))))
    counted, buckets = score_credits(by_ev, ev_end, t0, t7)

    all_fills = get_paginated(f"{P}/portfolio/fills", "fills")
    all_settles = get_paginated(f"{P}/portfolio/settlements", "settlements")
    all_events, _ = replay_fills(all_fills)
    fills_cash, settle_rev, n_fills, n_setts, no_ts = window_drag(
        all_events, all_settles, t0, hi)
    drag_total = fills_cash + settle_rev

    # credits paid since T0 regardless of program-conclusion bucket (for the cash identity)
    paid_since_t0 = 0.0
    for c in credits:
        ts = c.get("created_at")
        if not ts:
            continue
        try:
            if parse_iso(ts) >= t0:
                paid_since_t0 += (c.get("amount_cents") or 0) / 100.0
        except ValueError:
            pass  # unparseable created_at: excluded from the identity, visible via the gap
    cash_now, cash_ts = latest_recorder_cash()
    identity_gap = (round(cash_now - T0_CASH - paid_since_t0 - drag_total, 4)
                    if cash_now is not None else None)

    row = {"ts": now.isoformat(), "t0": T0, "t7": T7, "obs_deadline": OBS_DEADLINE,
           "credits_counted": round(counted, 2),
           "credits_buckets": {k: {"usd": v["usd"], "n": len(v["events"])}
                               for k, v in buckets.items()},
           "drag_fills_cash": round(fills_cash, 4), "drag_settle_rev": round(settle_rev, 4),
           "drag_total": round(drag_total, 4), "n_fills_window": n_fills,
           "n_setts_window": n_setts, "n_rows_no_ts": no_ts,
           "pass_now": counted > abs(drag_total),
           "recorder_cash": cash_now, "recorder_cash_ts": cash_ts,
           "paid_since_t0": round(paid_since_t0, 2), "identity_gap": identity_gap}
    print(json.dumps({k: row[k] for k in ("ts", "credits_counted", "drag_total",
                                          "pass_now", "identity_gap")}))
    print("counted %s | pre_t0 %s | post_t7 %s | unmapped %s (never counted)"
          % tuple((buckets[k]["usd"], len(buckets[k]["events"]))
                  for k in ("counted", "pre_t0", "post_t7", "unmapped")))
    if no_ts:
        print(f"WARNING {no_ts} fills/settlement rows have NO timestamp — venue rename? "
              f"Those rows are OUTSIDE the drag window right now.")
    if identity_gap is not None and abs(identity_gap) > IDENTITY_WARN_USD:
        print(f"WARNING cash identity gap {identity_gap:+.4f} > ${IDENTITY_WARN_USD:.0f} — "
              f"either a number here is wrong or external money moved (deposit/withdrawal).")
    out = os.path.join(DATA, "window_scoreboard-%s.jsonl" % now.strftime("%Y%m"))
    with open(out, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
