#!/usr/bin/env python3
"""0b (operator-ordered 2026-08-06, evidence-only) — FAR-DATED PAYOUT TIMING.

Question: when a market's incentive PROGRAM ends long before the market CLOSES,
does the venue pay the reward at program end, or lump it at market close+1
(locking the cash until close)? Canon (§M11 era) says "lump at close+1" but that
was measured on programs whose end ~= close. FIX H (KALSHI_FARCLOSE_PAYING_
EXCEPTION=1) now admits markets closing weeks past program end, so the timing
matters for cash-lock and for the P2 verdict window.

Evidence, all fresh reads, no assumptions:
  1. credit_history (bot's key)   — every credit ts + credited EVENT
  2. /markets?event_ticker=EV     — the credited event's market close_time(s)
  3. /incentive_programs (active) — program window fields for the CURRENT
                                    admitted set (raw field names printed, not
                                    guessed)
Per-credit: delta = credit_ts - max(close_time of the event's markets).
  BEFORE_CLOSE (delta < 0)  -> the venue paid while the market was still open:
                               direct evidence payment is NOT gated on close.
  CLOSE_WINDOW (0..48h)     -> consistent with close+1.
  LATE (>48h)               -> neither, listed for completeness.
Also enumerates the CURRENT FIX-H admitted set (receipt-proven series, market
close beyond the 8-day clock, program end inside it) with close dates.

Read-only against the venue. No state is written. Run on the VPS with live.env
sourced (client needs the key + KALSHI_USER_ID).
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiOrderClient  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
CREDIT_PREFIX = "Liquidity Incentive for event "
HORIZON_DAYS = 8.0


def _get(path):
    """Throttled + 429-retried public read: the first run lost 15/35 event reads to
    HTTP 429 bursts, leaving 24/57 credits unclassifiable — a denominator hole, not
    a data limit.

    n-3 (2026-08-08): the retry arm caught HTTPError ONLY, so a transient URLError /
    socket timeout — a DNS blip or a dropped connection, not a rate limit — propagated out
    of the loop and aborted the whole evidence run, re-opening the same denominator hole
    the throttle was added to close."""
    last_err = None
    for attempt in range(5):
        time.sleep(0.6 + attempt * 2.0)
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=30))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code != 429:
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e            # transient transport fault — retry, do not abort the run
    raise last_err


def _iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def classify(ts, close_min, close_max, nmk):
    """(tag, delta_h, basis) for one credit. M-3 fix (2026-08-08).

    Credits are per EVENT (the reason string carries only the event ticker), but an event can
    hold many strikes with DIFFERENT close times. The old rule measured every credit against
    close_MAX — the LATEST close in the event — so an ordinary close+1 payment for a strike
    that closed early printed as `BEFORE_CLOSE` with a large negative delta. That single tag is
    what the "payment is NOT gated on close" canon rests on, so the defect could mint a false
    timing canon (and the 2026-08-07T15:00Z APRPOTUS checkpoint is read through it).

    Only two shapes are watertight evidence:
      * single-market event (nmk <= 1)      -> basis 'single'    — close is unambiguous
      * multi-market, credit before EVERY close (ts < close_min) -> basis 'multi-min'
    A multi-market credit landing BETWEEN the earliest and latest close is genuinely
    AMBIGUOUS — it may be close+1 for an early strike or before-close for a late one — and is
    now reported as such instead of being counted as before-close evidence."""
    if nmk is not None and nmk <= 1:
        d_h = (ts - close_max).total_seconds() / 3600.0
        tag = "BEFORE_CLOSE" if d_h < 0 else "CLOSE_WINDOW" if d_h <= 48 else "LATE"
        return tag, d_h, "single"
    if ts < close_min:
        return "BEFORE_CLOSE", (ts - close_min).total_seconds() / 3600.0, "multi-min"
    d_h = (ts - close_max).total_seconds() / 3600.0
    if d_h > 48:
        return "LATE", d_h, "multi-max"
    if d_h >= 0:
        return "CLOSE_WINDOW", d_h, "multi-max"
    return "AMBIGUOUS", d_h, "multi-ambiguous"


def main():
    now = dt.datetime.now(dt.timezone.utc)
    print(f"# 0b payout-timing evidence — {now.isoformat()}")

    c = KalshiOrderClient(mode="live")   # authed READS only; no write method is called
    creds = c.get_credit_history().get("credits") or []
    print(f"credit_history rows: {len(creds)} (limit 1000; == limit would mean truncation)")

    rows = []
    ev_close = {}
    for r in creds:
        reason = r.get("reason") or ""
        if not reason.startswith(CREDIT_PREFIX):
            continue
        ev = reason[len(CREDIT_PREFIX):].strip()
        ts = _iso(r.get("created_at"))
        usd = float(r.get("amount_cents") or 0) / 100.0
        if ev not in ev_close:
            try:
                mkts = _get(f"/markets?event_ticker={ev}&limit=1000").get("markets") or []
                closes = sorted(_iso(m["close_time"]) for m in mkts if m.get("close_time"))
                ev_close[ev] = (closes[0], closes[-1], len(mkts)) if closes else None
            except Exception as e:
                ev_close[ev] = None
                print(f"  !! market read failed for {ev}: {e}")
        rows.append((ev, ts, usd))

    print(f"\n## per-credit timing vs the credited event's market close "
          f"(n={len(rows)} credits, {len(ev_close)} events)")
    before, window, late, unknown, ambig = [], [], [], [], []
    for ev, ts, usd in sorted(rows, key=lambda x: x[1]):
        cc = ev_close.get(ev)
        if not cc:
            unknown.append((ev, ts, usd))
            continue
        close_min, close_max, nmk = cc
        tag, d_h, basis = classify(ts, close_min, close_max, nmk)
        {"BEFORE_CLOSE": before, "CLOSE_WINDOW": window,
         "LATE": late, "AMBIGUOUS": ambig}[tag].append((ev, ts, usd, d_h, basis))
        print(f"  {tag:12s} {ev:38s} ${usd:7.2f}  credit {ts:%Y-%m-%d %H:%M}Z  "
              f"close {close_max:%Y-%m-%d %H:%M}Z  delta {d_h:+9.1f}h  "
              f"(mkts {nmk}, basis {basis})")
    for ev, ts, usd in unknown:
        print(f"  UNKNOWN      {ev:38s} ${usd:7.2f}  credit {ts:%Y-%m-%d %H:%M}Z  "
              f"close UNREADABLE")
    n = len(before) + len(window) + len(late) + len(ambig)
    print(f"\n  summary (denominator: {n} close-readable credits of {len(rows)} total): "
          f"BEFORE_CLOSE {len(before)} | CLOSE_WINDOW(0-48h) {len(window)} | "
          f"LATE(>48h) {len(late)} | AMBIGUOUS {len(ambig)} | UNKNOWN {len(unknown)}")
    # STANDING DETECTOR: the discriminator is the WATERTIGHT subset, never the raw tally.
    watertight = [r for r in before if r[4] in ("single", "multi-min")]
    print(f"  WATERTIGHT before-close evidence: {len(watertight)} of {len(before)} "
          f"BEFORE_CLOSE tags (single-market events, or credited before EVERY close in the "
          f"event). Only these support 'payment is not gated on close'.")
    if ambig:
        print(f"  ⚠ {len(ambig)} credit(s) landed BETWEEN the earliest and latest close of a "
              f"multi-market event — direction UNDECIDABLE from event-grain data; resolve "
              f"per-market via the estimates recorder's program_id -> market_ticker map.")
    if before:
        print("  BEFORE_CLOSE cases (payment not gated on close — the 0b discriminator):")
        for ev, ts, usd, d_h in sorted(before, key=lambda x: x[3]):
            print(f"    {ev:38s} paid {-d_h:8.1f}h BEFORE close  (${usd:.2f})")

    # ---- current FIX-H admitted set ----
    print("\n## FIX-H admitted set (receipt-proven series, close beyond "
          f"{HORIZON_DAYS:.0f}d, program end inside)")
    progs, cursor = [], ""
    for _ in range(5):
        d = _get("/incentive_programs?status=active&limit=10000"
                 + (f"&cursor={cursor}" if cursor else ""))
        progs += d.get("incentive_programs") or []
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
    if progs:
        print(f"  program row fields (evidence, first row): {sorted(progs[0].keys())}")
    try:
        fb = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "kalshi_credit_feedback.json")))
    except Exception:
        fb = json.load(open("/opt/pa2-maker-kalshi-live/kalshi_credit_feedback.json"))
    proven = {s for s, r in (fb.get("series") or {}).items()
              if (r.get("credits_n") or 0) > 0}
    horizon = now + dt.timedelta(days=HORIZON_DAYS)
    admitted = []
    for p in progs:
        t = p.get("market_ticker") or ""
        if not t or (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if t.split("-")[0] not in proven:
            continue
        end_raw = (p.get("end_date") or p.get("end_ts") or p.get("end_time") or
                   p.get("period_end") or p.get("end"))
        try:
            close = _iso(_get(f"/markets/{t}")["market"]["close_time"])
        except Exception as e:
            print(f"  !! close read failed {t}: {e}")
            continue
        try:
            pend = _iso(end_raw) if end_raw else None
        except Exception:
            pend = None
        if close > horizon and pend is not None and pend <= horizon:
            admitted.append((t, close, pend, (p.get("period_reward") or 0) / 10000))
    for t, close, pend, pool in sorted(admitted, key=lambda x: x[1]):
        print(f"  {t:38s} close {close:%Y-%m-%d %H:%M}Z  program_end "
              f"{pend:%Y-%m-%d %H:%M}Z  pool ${pool:.0f}/day  "
              f"(cash-lock if close+1: {(close - pend).days}d)")
    print(f"  admitted now: {len(admitted)} markets")


if __name__ == "__main__":
    main()
