#!/usr/bin/env python3
"""W10 — ZERO-PAYER MECHANISM STUDY (operator-raised 2026-08-04).

QUESTION: ~20 settled events with real presence were credited $0.00 against a ~$26.04
forecast (master plan §3). Why? Hypotheses, tested in the operator-ruled order:
  H1  the $1 minimum-credit floor (min observed credit $1.01 of 54 — sub-$1 accruals
      may truncate to zero)
  H2  program-window normalization (presence landing outside the reward window)
  H3  share dilution (other makers' size cutting our share below the floor)
  H4  movement (operator hypothesis: books may need movement to pay — program terms say
      RESTING liquidity pays, so this SHOULD be false, but it is tested, not assumed)

THIS SCRIPT IS PHASE 1: pull fresh venue data (read-only), freeze it, and build the
per-EVENT join of presence vs credits that every hypothesis test runs on.
It re-derives the zero-payer set from primary sources rather than trusting the prior
session's list (RULE THIRTEEN: re-derive at speaking time).

Sources (all venue-canonical, all timestamped in the snapshot):
  /portfolio/orders (canceled+executed+resting)  -> resting intervals per market
  /portfolio/settlements                          -> settled tickers + times
  /v1/users/{id}/credit_history                   -> per-EVENT reward receipts
  /trade-api/v2/markets/{ticker} (public)         -> event_ticker, open/close, life

Presence = UNION of resting intervals per market (kalshi_presence_calibrate.union_seconds
— overlapping orders do not double-count), summed across a event's markets.

Read-only against the venue. Writes ONLY the snapshot/output JSON under --outdir
(default /tmp/w10). Never touches /opt code, never places/cancels orders, never
clears STOP.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_presence_calibrate import _iso, union_seconds  # noqa: E402

CREDIT_PREFIX = "Liquidity Incentive for event "


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def fetch_all(q):
    """Pull the four feeds fresh. Returns (raw dict, read_ts iso)."""
    read_ts = utcnow().isoformat()
    orders = []
    for stt in ("canceled", "executed", "resting"):
        rows = (q.KalshiOrderClient(mode="live").get_orders(status=stt).get("orders")
                or [])
        print(f"  orders[{stt}]: {len(rows)}", file=sys.stderr)
        orders += rows
    c = q.KalshiOrderClient(mode="live")
    setts = c.get_settlements().get("settlements") or []
    creds = c.get_credit_history().get("credits") or []
    print(f"  settlements: {len(setts)}  credits: {len(creds)}", file=sys.stderr)
    meta = {}
    tickers = sorted({o.get("ticker") for o in orders if o.get("ticker")})
    for i, t in enumerate(tickers):
        try:
            m = q.public_get(f"/trade-api/v2/markets/{t}").get("market") or {}
            meta[t] = {k: m.get(k) for k in
                       ("event_ticker", "open_time", "close_time", "status",
                        "result", "settlement_value_dollars")}
        except Exception as e:
            meta[t] = {"error": repr(e)[:120]}
        time.sleep(0.15)
        if (i + 1) % 50 == 0:
            print(f"  market meta {i+1}/{len(tickers)}", file=sys.stderr)
    return ({"read_ts": read_ts, "orders": orders, "settlements": setts,
             "credits": creds, "market_meta": meta}, read_ts)


def credits_by_event(creds):
    """{event_ticker: {usd, ts_list}} from credit_history reason strings. Credits whose
    reason does not carry an event (e.g. the referral) land under key None."""
    out = {}
    for r in creds:
        reason = r.get("reason") or ""
        ev = reason[len(CREDIT_PREFIX):].strip() if reason.startswith(CREDIT_PREFIX) else None
        d = out.setdefault(ev, {"usd": 0.0, "n": 0, "ts": []})
        d["usd"] += float(r.get("amount_cents") or 0) / 100.0
        d["n"] += 1
        d["ts"].append(r.get("created_at"))
    return out


def build_event_table(raw, now):
    """Per-EVENT join: presence, notional, market count, close/settle state, credits."""
    meta = raw["market_meta"]
    settled_ts = {s.get("ticker"): s.get("settled_time") for s in raw["settlements"]}
    # per-market resting intervals and notional
    per_mkt = {}
    for o in raw["orders"]:
        t = o.get("ticker")
        if not t:
            continue
        try:
            a, b = _iso(o["created_time"]), _iso(o["last_update_time"])
        except Exception:
            continue
        if (b - a).total_seconds() < 0:
            continue
        d = per_mkt.setdefault(t, {"iv": [], "notional_s": 0.0, "orders": 0})
        d["iv"].append((a, b))
        d["orders"] += 1
        # resting notional-seconds: price*count integrated over the order's life
        try:
            px = float(o.get("yes_price_dollars") or o.get("no_price_dollars") or 0)
            ct = float(o.get("initial_count_fp") or o.get("initial_count") or 0)
            d["notional_s"] += px * ct * (b - a).total_seconds()
        except Exception:
            pass

    events = {}
    for t, d in per_mkt.items():
        m = meta.get(t) or {}
        ev = m.get("event_ticker") or "-".join(t.split("-")[:-1]) or t
        e = events.setdefault(ev, {
            "markets": 0, "orders": 0, "covered_h": 0.0, "notional_usd_h": 0.0,
            "first_rest": None, "last_rest": None, "latest_close": None,
            "all_settled": True, "n_settled": 0, "tickers": []})
        cov = union_seconds(d["iv"])
        e["markets"] += 1
        e["orders"] += d["orders"]
        e["covered_h"] += cov / 3600.0
        e["notional_usd_h"] += d["notional_s"] / 3600.0
        e["tickers"].append(t)
        fr = min(a for a, _ in d["iv"]).isoformat()
        lr = max(b for _, b in d["iv"]).isoformat()
        e["first_rest"] = min(filter(None, [e["first_rest"], fr]))
        e["last_rest"] = max(filter(None, [e["last_rest"], lr]))
        ct = m.get("close_time")
        if ct:
            e["latest_close"] = max(filter(None, [e["latest_close"], ct]))
        if t in settled_ts:
            e["n_settled"] += 1
        else:
            e["all_settled"] = False

    creds = credits_by_event(raw["credits"])
    for ev, e in events.items():
        c = creds.get(ev)
        e["credit_usd"] = round(c["usd"], 2) if c else 0.0
        e["credit_n"] = c["n"] if c else 0
        e["credit_ts"] = c["ts"] if c else []
        lc = e["latest_close"]
        e["hours_since_close"] = (
            round((now - _iso(lc)).total_seconds() / 3600.0, 1) if lc else None)
        e["covered_h"] = round(e["covered_h"], 3)
        e["notional_usd_h"] = round(e["notional_usd_h"], 3)
    unattributed = {k: v for k, v in creds.items()
                    if k is not None and k not in events}
    return events, {"no_event_reason_usd": round(creds.get(None, {}).get("usd", 0.0), 2),
                    "credited_events_not_in_orders": unattributed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/w10")
    ap.add_argument("--from-snapshot", default=None,
                    help="rebuild the event table from a frozen snapshot instead of "
                         "hitting the venue")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    if a.from_snapshot:
        with open(a.from_snapshot) as fh:
            raw = json.load(fh)
        read_ts = raw["read_ts"]
    else:
        import maker_kalshi_quoter as q
        raw, read_ts = fetch_all(q)
        tag = read_ts.replace(":", "").split(".")[0]
        snap = os.path.join(a.outdir, f"w10_snapshot_{tag}.json")
        with open(snap, "w") as fh:
            json.dump(raw, fh)
        print(f"frozen snapshot: {snap}", file=sys.stderr)

    now = _iso(read_ts)
    events, notes = build_event_table(raw, now)

    # DUE = payout had time to arrive. Canon: payout keys on the PROGRAM window end which
    # can precede OR follow market close (close+1 held 24 of 33). Every observed credit
    # landed by ~2 days after its event's close, so >72h since close with $0.00 is
    # treated as a zero-payer, and the margin is stated with the result.
    zero, paid = {}, {}
    for ev, e in sorted(events.items()):
        if e["hours_since_close"] is None or e["hours_since_close"] < 72:
            continue
        (paid if e["credit_usd"] > 0 else zero)[ev] = e

    out = {"schema": 1, "read_ts": read_ts, "due_margin_h": 72,
           "n_events_traded": len(events),
           "n_due": len(zero) + len(paid), "n_paid": len(paid), "n_zero": len(zero),
           "zero_payers": zero, "paid_events": paid, "notes": notes}
    path = os.path.join(a.outdir, "w10_event_table.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(f"events traded={len(events)} due(>72h)={out['n_due']} "
          f"paid={len(paid)} zero={len(zero)}")
    print(f"zero-payer presence: " + ", ".join(
        f"{ev}({e['covered_h']}h/${e['credit_usd']})" for ev, e in
        sorted(zero.items(), key=lambda kv: -kv[1]['covered_h'])[:25]))
    print("wrote", path)


if __name__ == "__main__":
    main()
