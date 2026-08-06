#!/usr/bin/env python3
"""W3/D2 — build the per-series CREDIT-FEEDBACK feed the capital-aware rank consumes.

Follow the profit (scale plan B1): series the venue has actually PAID rank up; series we
FILLED that never paid — once their payouts are defensibly due — rank down. Receipts only:
credits come from credit_history (per-EVENT attribution, canon §M11), fills from
/portfolio/orders, due-ness from /portfolio/settlements.

VERDICT RULES (exact, pinned in test_d2_feedback.py). Evidence is per-EVENT — that is the
B1 "lag exclusion" intent: a not-yet-due EVENT is excluded from evidence, it does not
shield its series (blind review 2026-08-05 F3: the first cut gated on every ticker of the
SERIES being settled, so an actively-traded serial series could never be convicted — the
exact class the penalty was built for):
  paid            >=1 liquidity credit ever for any event of the series. Checked first, so
                  one payment immediately clears a standing penalty.
  never_paid_due  zero credits ever AND >=1 DUE filled event — an event whose filled
                  tickers are ALL settled >72h ago (the W10-established margin: all 57
                  observed credits landed within ~2 days of close). Fresh or unsettled
                  events are simply not evidence either way.
  insufficient    everything else — resting-only presence, or fills only on events still
                  inside the margin. Nothing resting-only is ever punished.

Output: kalshi_credit_feedback.json {schema: 1, generated, series: {S: {verdict, ...}}}.
Consumed by kalshi_capital_rank.load_credit_feedback (fail-OPEN {}). Read-only against the
venue. OFFLINE builder — run by hand or a timer at deploy naming; never imported by the
trading cycle.
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCHEMA = 1
DUE_MARGIN_H = 72.0
CREDIT_PREFIX = "Liquidity Incentive for event "


def _iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _series(t):
    return (t or "").split("-")[0]


def _event(t):
    """STRING-FALLBACK event key = ticker minus its strike suffix. A-2 (identity review,
    operator 'go' 2026-08-06): this fragments real venue shapes (a 4-field legacy ticker
    or a T-negative strike splits one event into fragments, and a fully-settled FRAGMENT
    read 'due' while sibling strikes were open — prematurely arming never_paid_due).
    The builder now resolves the TRUE event via market metadata (event_of); this string
    form remains only as the counted fallback when a metadata read fails."""
    parts = (t or "").split("-")
    return "-".join(parts[:-1]) if len(parts) > 2 else (t or "")


def build_feedback(credits, orders, settlements, now, event_of=None):
    """Pure. credits/orders/settlements are raw venue rows; now is a tz-aware datetime.
    event_of: optional callable ticker -> venue event_ticker (metadata-resolved);
    None/per-ticker-None falls back to the string _event."""
    ev_of = event_of or (lambda t: None)
    credited = collections.defaultdict(lambda: {"usd": 0.0, "n": 0, "last": ""})
    for r in credits:
        reason = r.get("reason") or ""
        if not reason.startswith(CREDIT_PREFIX):
            continue
        s = _series(reason[len(CREDIT_PREFIX):])
        credited[s]["usd"] += float(r.get("amount_cents") or 0) / 100.0
        credited[s]["n"] += 1
        ts = str(r.get("created_at") or "")
        if ts > credited[s]["last"]:
            credited[s]["last"] = ts          # B-4: trust gets a timestamp

    fills_ct = collections.defaultdict(float)
    filled_by_event = collections.defaultdict(set)   # event -> its filled tickers
    seen_series = set()          # every series with ANY order, so a resting-only series
    for o in orders:             # appears as an explicit "insufficient", never as absence
        t = o.get("ticker")
        if not t:
            continue
        seen_series.add(_series(t))
        try:
            ct = float(o.get("fill_count_fp") or 0)
        except (TypeError, ValueError):
            ct = 0.0
        if ct > 0:
            fills_ct[_series(t)] += ct
            filled_by_event[ev_of(t) or _event(t)].add(t)

    settled_at = {}
    for s in settlements:
        t = s.get("ticker")
        if t and s.get("settled_time"):
            try:
                settled_at[t] = _iso(s["settled_time"])
            except Exception:
                pass

    due_events = collections.defaultdict(int)        # series -> filled events fully due
    for ev, tickers in filled_by_event.items():
        due = True
        for t in tickers:
            st = settled_at.get(t)
            if st is None or (now - st).total_seconds() < DUE_MARGIN_H * 3600.0:
                due = False
                break
        if due:
            due_events[_series(ev)] += 1

    out = {}
    for s in set(credited) | set(fills_ct) | seen_series:
        row = {"credited_usd": round(credited[s]["usd"], 2) if s in credited else 0.0,
               "credits_n": credited[s]["n"] if s in credited else 0,
               "last_credit_ts": (credited[s]["last"] or None) if s in credited else None,
               "filled_ct": round(fills_ct.get(s, 0.0), 2),
               "due_filled_events": due_events.get(s, 0)}
        if row["credits_n"] > 0:
            row["verdict"] = "paid"
        elif row["due_filled_events"] > 0:
            row["verdict"] = "never_paid_due"
        else:
            row["verdict"] = "insufficient"
        out[s] = row
    return out


def feed_integrity_alarms(credits, matched_n, limit, prev_series, new_series):
    """A-1/B-7 (identity review, operator 'go' 2026-08-06): the whole trust plane parses
    one venue string — alarm loudly on anything that smells like silent decay. Pure;
    returns alarm strings (empty = healthy)."""
    alarms = []
    total = len(credits)
    if total and matched_n < total * 0.9:
        alarms.append(f"!! CREDIT PARSE RATE {matched_n}/{total} — the venue may have "
                      f"reworded the credit reason (A-1); verdicts UNTRUSTWORTHY")
    if limit and total >= limit:
        alarms.append(f"!! credit_history rows == limit ({limit}) — TRUNCATION LIKELY "
                      f"(B-7); a dropped credit can flip paid->convicted")
    for s, prev in (prev_series or {}).items():
        if (prev.get("verdict") == "paid"
                and (new_series.get(s) or {}).get("verdict") != "paid"):
            alarms.append(f"!! VERDICT REGRESSION {s}: paid -> "
                          f"{(new_series.get(s) or {}).get('verdict')} — a paid series "
                          f"can only lose that verdict through data loss; investigate "
                          f"before consuming this feed")
    return alarms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="kalshi_credit_feedback.json")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args()
    from maker_kalshi_client import KalshiOrderClient
    c = KalshiOrderClient(mode="live")
    now = dt.datetime.now(dt.timezone.utc)
    orders = []
    for stt in ("canceled", "executed", "resting"):
        orders += (c.get_orders(status=stt).get("orders") or [])
    creds = c.get_credit_history().get("credits") or []
    setts = c.get_settlements().get("settlements") or []
    # A-2: resolve the TRUE event ticker for every FILLED ticker via market metadata,
    # persisted in an alongside cache (events are static per market). String fallback
    # only on read failure, and counted.
    import urllib.request
    cache_path = os.path.join(os.path.dirname(a.out) or ".", "kalshi_event_map.json")
    try:
        ev_map = json.load(open(cache_path))
    except Exception:
        ev_map = {}
    filled = sorted({o.get("ticker") for o in orders
                     if o.get("ticker") and float(o.get("fill_count_fp") or 0) > 0})
    misses = [t for t in filled if t not in ev_map]
    fell_back = 0
    for t in misses:
        try:
            m = json.load(urllib.request.urlopen(
                f"https://api.elections.kalshi.com/trade-api/v2/markets/{t}",
                timeout=15)).get("market", {})
            if m.get("event_ticker"):
                ev_map[t] = m["event_ticker"]
            else:
                fell_back += 1
        except Exception:
            fell_back += 1
        import time as _t
        _t.sleep(0.2)
    try:
        with open(cache_path, "w") as fh:
            json.dump(ev_map, fh, indent=0, sort_keys=True)
    except Exception:
        pass
    print(f"event map: {len(filled)} filled tickers, {len(misses)} fetched, "
          f"{fell_back} STRING-FALLBACK" + ("  !! metadata degraded" if fell_back else ""))
    matched_n = sum(1 for r in creds
                    if (r.get("reason") or "").startswith(CREDIT_PREFIX))
    fb = build_feedback(creds, orders, setts, now, event_of=ev_map.get)
    try:
        prev_series = (json.load(open(a.out)).get("series") or {})
    except Exception:
        prev_series = {}
    for alarm in feed_integrity_alarms(creds, matched_n, 1000, prev_series, fb):
        print(alarm)
    doc = {"schema": SCHEMA, "generated": now.isoformat(), "series": fb}
    counts = collections.Counter(r["verdict"] for r in fb.values())
    print(f"series={len(fb)} verdicts={dict(counts)}")
    for s, r in sorted(fb.items(), key=lambda kv: kv[1]["verdict"]):
        print(f"  {s:24s} {r['verdict']:15s} credits=${r['credited_usd']:8.2f} "
              f"n={r['credits_n']:2d} filled_ct={r['filled_ct']:9.2f}")
    if not a.print_only:
        with open(a.out, "w") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
