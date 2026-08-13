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

from reward_pnl_report import parse_iso, ticker_to_event

DATA = os.environ.get("KALSHI_DATA_DIR", "/opt/pa2-maker-kalshi-live")

T0 = "2026-08-12T01:40:43+00:00"          # §10-C, operator-named restart
T7 = "2026-08-19T01:40:43+00:00"          # T0 + 7d
OBS_DEADLINE = "2026-08-21T01:40:43+00:00"  # T7 + 48h credit-observation envelope
T0_CASH = 274.4691                        # recorder 2026-08-12T01:39:03Z (§10-C baseline)
IDENTITY_WARN_USD = 2.0


def event_end_map(program_map):
    """{event: latest end_date iso} from the merge-only program map (survives program
    disappearance from the active list — the §5 requirement). Event rule imported from
    reward_pnl_report so the two daily artifacts can never disagree on it (review F10)."""
    out = {}
    for pr in (program_map or {}).values():
        t = (pr or {}).get("market_ticker")
        end = (pr or {}).get("end_date")
        if not t or not end:
            continue
        ev = ticker_to_event(t)
        if ev not in out or end > out[ev]:
            out[ev] = end
    return out


def filter_credits(credits, lo, hi):
    """Keep credit rows whose created_at is inside [lo, hi] (hi=None → uncapped).
    Returns (rows, n_dropped_no_ts) — rows without a parseable created_at are COUNTED,
    never silently dropped (review F1/F7). This filter is what makes the counted gauge
    per-observation instead of lifetime (review F1) and enforces the credit-observation
    deadline (review F8)."""
    rows, dropped = [], 0
    for c in credits or []:
        ts = c.get("created_at")
        if not ts:
            dropped += 1
            continue
        try:
            t = parse_iso(ts)
        except (ValueError, TypeError):
            dropped += 1
            continue
        if t >= lo and (hi is None or t <= hi):
            rows.append(c)
    return rows, dropped


def recon_mismatches(replay_pos, venue_pos, settled_tickers, tol=0.005):
    """The ledger's per-run self-check (kalshi_attribution_ledger.collect) reused: replayed
    end positions must equal the venue's position_fp on every unsettled ticker. Disagreement
    means the cash model drifted or the tape was truncated — the drag number in that run is
    NOT trustworthy (review F3; the original scoreboard discarded this check)."""
    checkable = set(venue_pos) | {t for t, v in replay_pos.items()
                                  if abs(v) > tol and t not in settled_tickers}
    return {t: [round(venue_pos.get(t, 0.0), 4), round(replay_pos.get(t, 0.0), 4)]
            for t in sorted(checkable)
            if abs(venue_pos.get(t, 0.0) - replay_pos.get(t, 0.0)) > tol}


def pass_gauge(counted, drag_total):
    """Pre-reg 1 rule 'PASS iff credits > |drag|' stated for a drag that is a LOSS.
    Net form counted + drag_total > 0 is identical there and stays correct when the
    window's trading cash nets positive (review F2: abs() turned a profit into a hurdle)."""
    return counted + drag_total > 0


def window_state(now, t7, obs_deadline):
    """open (inside the 7 days) / post_window (credits still observable) / concluded."""
    if now <= t7:
        return "open"
    if now <= obs_deadline:
        return "post_window"
    return "concluded"


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
    credit_alarms = []
    if len(credits) >= 1000:
        # F4: single un-cursored GET; at the limit the feed is TRUNCATING silently
        # (venue precedent: incentive_programs?limit=1000). Which end vanishes is
        # server-order-dependent — nothing derived from `credits` is trustworthy.
        credit_alarms.append("credit_history returned >= limit rows — feed TRUNCATED, "
                             "credits gauges NOT trustworthy")
    obs_deadline = parse_iso(OBS_DEADLINE)
    state = window_state(now, t7, obs_deadline)
    # F1/F8: only credits PAID inside [T0, obs_deadline] enter the gauge — an event's
    # lifetime pre-window credits must never ride in on a program that concludes in-window,
    # and nothing paid after the pre-registered deadline is ever counted.
    cr_obs, cr_dropped = filter_credits(credits, t0, obs_deadline)
    by_ev = credits_by_event(cr_obs)
    ev_end = event_end_map(json.load(open(os.path.join(DATA, "kalshi_program_map.json"))))
    counted, buckets = score_credits(by_ev, ev_end, t0, t7)

    # F3: T0-anchored fetch — valid because the account was FLAT at T0 (§10-C baseline:
    # 0 positions, recorder 2026-08-12T01:39:03Z), so the since-T0 tape IS the complete
    # position history for this window. Bounds the daily pull; the cap guard and the recon
    # check below catch the silent-truncation cliff the ledger documents.
    alarms = credit_alarms
    min_ts = f"&min_ts={int(t0.timestamp())}"
    all_fills = get_paginated(f"{P}/portfolio/fills", "fills", extra=min_ts)
    all_settles = get_paginated(f"{P}/portfolio/settlements", "settlements", extra=min_ts)
    if len(all_fills) >= 10000 or len(all_settles) >= 10000:
        alarms.append("pagination hit the 50x200 cap — tape may be TRUNCATED, drag NOT trustworthy")
    all_events, replay_pos = replay_fills(all_fills)
    fills_cash, settle_rev, n_fills, n_setts, no_ts = window_drag(
        all_events, all_settles, t0, hi)
    drag_total = fills_cash + settle_rev
    venue_rows = get_paginated(f"{P}/portfolio/positions", "market_positions",
                               extra="&count_filter=position")
    mism = recon_mismatches(
        replay_pos,
        {p.get("ticker"): float(p.get("position_fp") or p.get("position") or 0)
         for p in venue_rows},
        {s.get("ticker") for s in all_settles})
    if mism:
        alarms.append(f"position recon mismatch [venue, replayed] on {len(mism)} ticker(s): "
                      f"{mism} — drag NOT trustworthy")

    # credits paid since T0 regardless of program-conclusion bucket (for the cash identity)
    # identity leg: ALL credits since T0 regardless of bucket or deadline (cash moved).
    cr_since_t0, _ = filter_credits(credits, t0, None)
    paid_since_t0 = sum((c.get("amount_cents") or 0) / 100.0 for c in cr_since_t0)
    cash_now, cash_ts = latest_recorder_cash()
    identity_gap = (round(cash_now - T0_CASH - paid_since_t0 - drag_total, 4)
                    if cash_now is not None else None)

    row = {"ts": now.isoformat(), "t0": T0, "t7": T7, "obs_deadline": OBS_DEADLINE,
           "window_state": state, "credits_dropped_no_ts": cr_dropped,
           "credits_counted": round(counted, 2),
           "credits_buckets": {k: {"usd": v["usd"], "n": len(v["events"])}
                               for k, v in buckets.items()},
           "drag_fills_cash": round(fills_cash, 4), "drag_settle_rev": round(settle_rev, 4),
           "drag_total": round(drag_total, 4), "n_fills_window": n_fills,
           "n_setts_window": n_setts, "n_rows_no_ts": no_ts,
           "pass_now": pass_gauge(counted, drag_total),
           "recorder_cash": cash_now, "recorder_cash_ts": cash_ts,
           "paid_since_t0": round(paid_since_t0, 2), "identity_gap": identity_gap,
           "alarms": alarms}
    for a in alarms:
        print(f"WARNING {a}")
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
