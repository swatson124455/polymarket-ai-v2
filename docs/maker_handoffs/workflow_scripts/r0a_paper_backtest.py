#!/usr/bin/env python3
"""R0a — PAPER BACKTEST (money ladder rung 0a, roadmap 2026-08-13 §3). READ-ONLY.

Applies the v2.0 filters RETROACTIVELY to our own full fill+credit tape and reports
what the filtered subset actually earned. GET-only + local tape reads; places,
cancels, modifies NOTHING.

Filters (per roadmap §3 R0a):
  F-CLOSE8D   entry only if market close_time <= 8 days after the event's FIRST fill
              (event-level; also reported per-fill for the delayed-entry variant)
  F-TOXIC     mechanism-based toxicity (NOT series names):
                announcement  = title carries a discrete-actor announcement verb
                ladder_near_strike = market has a strike AND our mean fill price
                                     in (0.10, 0.90)  [underlying near the strike]
                resolution_proximity = >50%% of our maker contracts filled with
                                     < 1h to close  [D3: <1h bucket -7.6c/ct]
  F-EVICT     2x-with-paid-ratio eviction sim — only computable where the estimates
              tape exists (2026-08-06+); denominator stated, never extrapolated.
Sizing (min/target) is NOT retroactively simulable — fills are what they are; stated
in the output rather than faked.

Method: position-aware cash via kalshi_attribution_ledger.replay_fills over the FULL
tape (D1 method); settlement revenue/100; credits EVENT-level via reward_pnl_report
regex; ticker->event = rsplit('-',1)[0].
Output: JSON to /tmp/R0A_PAPER_BACKTEST.json + human summary on stdout.
"""
import datetime
import glob
import json
import os
import re
import sys
from collections import defaultdict

DATA = "/opt/pa2-maker-kalshi-live"
sys.path.insert(0, DATA)
import kalshi_attribution_ledger as kal          # noqa: E402

_EVENT_RE = re.compile(r"for event (\S+)")
ANNOUNCE_KW = ("endorse", "mention", "say", "tweet", "announce", "withdraw",
               "resign", "nominat", "confirm", "veto", "adjourn", "recess",
               "statement", "pardon", "appoint", "drop out", "dropout", "dni")
CLOSE8D_S = 8 * 86400.0
PROX_S = 3600.0


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_iso(s):
    dt = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def ev_of(t):
    return t.rsplit("-", 1)[0]


def main():
    t0 = now_utc()
    fills = kal.get_paginated(f"{kal.P}/portfolio/fills", "fills")
    setts = kal.get_paginated(f"{kal.P}/portfolio/settlements", "settlements")
    from maker_kalshi_client import KalshiOrderClient
    os.chdir(DATA)
    credits = KalshiOrderClient(mode="live").get_credit_history(limit=1000)["credits"]
    read_ts = now_utc().isoformat()

    tickers = sorted({f.get("ticker") or f.get("market_ticker") for f in fills})
    meta = {}
    for i in range(0, len(tickers), 90):
        chunk = tickers[i:i + 90]
        d = kal.get(f"{kal.P}/markets?limit=1000&tickers=" + ",".join(chunk))
        for m in d.get("markets") or []:
            meta[m["ticker"]] = m
    n_meta_missing = sum(1 for t in tickers if t not in meta)

    # ---- position-aware replay (canon) ----
    events_cash = defaultdict(float)
    fees_by_ev = defaultdict(float)
    fill_rows = []               # per-fill annotated
    ev_first_fill = {}
    replay, _pos = kal.replay_fills(fills)
    for e in replay:
        f = e["fill"]
        t = f.get("ticker") or f.get("market_ticker")
        ev = ev_of(t)
        ts = parse_iso(f.get("created_time"))
        m = meta.get(t) or {}
        close = parse_iso(m["close_time"]) if m.get("close_time") else None
        tt_close = (close - ts).total_seconds() if close else None
        is_taker = kal.fill_fee(f) > 0
        events_cash[ev] += e["cash"]
        fees_by_ev[ev] += kal.fill_fee(f)
        if ev not in ev_first_fill or ts < ev_first_fill[ev][0]:
            ev_first_fill[ev] = (ts, tt_close)
        fill_rows.append({"ev": ev, "t": t, "ts": ts, "tt_close": tt_close,
                          "cash": e["cash"], "ct": kal.fill_count(f),
                          "px": kal.fill_price(f), "taker": is_taker})

    setts_rev = defaultdict(float)
    for s in setts:
        setts_rev[ev_of(s.get("ticker") or "")] += kal.settlement_revenue(s)

    paid_by_ev = defaultdict(float)
    referral = 0.0
    for c in credits:
        m = _EVENT_RE.search(c.get("reason") or "")
        amt = (c.get("amount_cents") or 0) / 100.0
        if m:
            paid_by_ev[m.group(1)] += amt
        else:
            referral += amt

    # ---- per-event classification ----
    all_events = sorted(set(events_cash) | set(setts_rev) | set(paid_by_ev))
    ev_rows = {}
    for ev in all_events:
        evfills = [r for r in fill_rows if r["ev"] == ev]
        realized = events_cash.get(ev, 0.0) + setts_rev.get(ev, 0.0)
        paid = paid_by_ev.get(ev, 0.0)
        # F-CLOSE8D at first fill
        ff = ev_first_fill.get(ev)
        close8d = None
        if ff and ff[1] is not None:
            close8d = ff[1] <= CLOSE8D_S
        # toxicity
        reasons = []
        # any ticker meta of the event (title shared across strikes)
        m0 = next((meta[r["t"]] for r in evfills if r["t"] in meta), {})
        title = ((m0.get("title") or "") + " " + (m0.get("subtitle") or "")).lower()
        if any(k in title for k in ANNOUNCE_KW):
            reasons.append("announcement")
        strike = m0.get("floor_strike") if m0.get("floor_strike") is not None else m0.get("cap_strike")
        cts = sum(r["ct"] for r in evfills)
        if strike is not None and cts > 0:
            # contract-weighted mean acquired-outcome fill price
            mp = sum(r["px"] * r["ct"] for r in evfills) / max(cts, 1e-9)
            if 0.10 < mp < 0.90:
                reasons.append("ladder_near_strike")
        prox_ct = sum(r["ct"] for r in evfills
                      if r["tt_close"] is not None and 0 <= r["tt_close"] < PROX_S)
        if cts > 0 and prox_ct / cts > 0.5:
            reasons.append("resolution_proximity")
        keep = (close8d is not False) and not reasons
        ev_rows[ev] = {"net": realized + paid, "paid": paid, "realized": realized,
                       "fees": fees_by_ev.get(ev, 0.0), "fills": len(evfills),
                       "contracts": cts, "close8d": close8d, "toxic": reasons,
                       "keep": keep}

    def bucket(rows):
        return {"events": len(rows),
                "net": round(sum(r["net"] for r in rows), 2),
                "paid": round(sum(r["paid"] for r in rows), 2),
                "realized": round(sum(r["realized"] for r in rows), 2),
                "fills": sum(r["fills"] for r in rows)}

    rows = list(ev_rows.values())
    out = {
        "generated": read_ts, "t_start": t0.isoformat(),
        "n_fills": len(fills), "n_settlements": len(setts),
        "n_credit_rows": len(credits), "referral_excluded": referral,
        "n_events": len(all_events), "n_meta_missing_tickers": n_meta_missing,
        "full_tape": bucket(rows),
        "kept": bucket([r for r in rows if r["keep"]]),
        "dropped": bucket([r for r in rows if not r["keep"]]),
        "dropped_by": {
            "close8d_fail": bucket([r for r in rows if r["close8d"] is False]),
            "announcement": bucket([r for r in rows if "announcement" in r["toxic"]]),
            "ladder_near_strike": bucket([r for r in rows if "ladder_near_strike" in r["toxic"]]),
            "resolution_proximity": bucket([r for r in rows if "resolution_proximity" in r["toxic"]]),
            "close8d_unknown": bucket([r for r in rows if r["close8d"] is None]),
        },
        "per_event": {ev: {k: (round(v, 4) if isinstance(v, float) else v)
                           for k, v in r.items()} for ev, r in ev_rows.items()},
        "sizing_note": "min/target sizing NOT retroactively simulable — fills are "
                       "historical at historical sizes; reported as-is.",
    }

    # ---- F-EVICT sim (estimates tape 08-06+ only; INFERRED) ----
    est_files = sorted(glob.glob(os.path.join(DATA, "estimates-*.jsonl")))
    pm = {}
    try:
        pm = json.load(open(os.path.join(DATA, "kalshi_program_map.json")))
    except Exception:
        pass
    # accrual time series per event: {ev: [(ts, cum_accrued$)]}
    acc_ts = defaultdict(list)
    for fp in est_files:
        with open(fp) as fh:
            for ln in fh:
                if not ln.strip():
                    continue
                try:
                    snap = json.loads(ln)
                except ValueError:
                    continue
                ts = snap.get("ts") or snap.get("read_ts")
                per_ev = defaultdict(float)
                for e in snap.get("estimates") or []:
                    pr = pm.get(str(e.get("program_id"))) or {}
                    tk = pr.get("market_ticker")
                    if tk:
                        per_ev[ev_of(tk)] += float(e.get("reward_centicents") or 0) / 10000.0
                for ev, a in per_ev.items():
                    acc_ts[ev].append((ts, a))
    # series paid/accrued ratio (concluded proxy: has paid or last accrual >48h old)
    ser_paid = defaultdict(float)
    ser_acc = defaultdict(float)
    for ev, r in ev_rows.items():
        ser = ev.split("-")[0]
        ser_paid[ser] += r["paid"]
        if acc_ts.get(ev):
            ser_acc[ser] += acc_ts[ev][-1][1]
    evict = {"note": "2x-with-paid-ratio eviction, INFERRED; denominator = events with "
                     "estimates-tape coverage (2026-08-06+) AND >=2 fills after coverage "
                     "start; savings proxy = |fill cash| after eviction trigger day",
             "events_covered": 0, "events_evicted": 0, "cash_avoided": 0.0,
             "paid_forgone": 0.0, "detail": []}
    for ev, series in acc_ts.items():
        if ev not in ev_rows:
            continue
        evf = sorted([r for r in fill_rows if r["ev"] == ev], key=lambda r: r["ts"])
        cov_start = parse_iso(series[0][0]) if series and series[0][0] else None
        if not cov_start:
            continue
        post = [r for r in evf if r["ts"] >= cov_start]
        if len(post) < 2:
            continue
        evict["events_covered"] += 1
        ser = ev.split("-")[0]
        ratio = (ser_paid[ser] / ser_acc[ser]) if ser_acc[ser] > 0 else (
            1.0 if ser_paid[ser] > 0 else 0.0)
        ratio = min(ratio, 1.0)
        # daily walk from first post-coverage fill
        start = post[0]["ts"]
        trig = None
        for dayn in range(2, 15):
            d_end = start + datetime.timedelta(days=dayn)
            if d_end > now_utc():
                break
            win_lo = d_end - datetime.timedelta(days=1)
            a_lo = next((a for ts, a in reversed(series)
                         if ts and parse_iso(ts) <= win_lo), None)
            a_hi = next((a for ts, a in reversed(series)
                         if ts and parse_iso(ts) <= d_end), None)
            if a_lo is None or a_hi is None:
                continue
            accrual = max(a_hi - a_lo, 0.0) * ratio
            cost = sum(-r["cash"] for r in evf if win_lo <= r["ts"] <= d_end
                       and r["cash"] < 0)
            if cost > 0 and accrual < 2.0 * cost:
                trig = d_end
                break
        if trig:
            avoided = sum(-r["cash"] for r in evf if r["ts"] > trig and r["cash"] < 0)
            recovered = sum(r["cash"] for r in evf if r["ts"] > trig and r["cash"] > 0)
            evict["events_evicted"] += 1
            evict["cash_avoided"] += avoided - recovered
            evict["detail"].append({"ev": ev, "trigger": trig.isoformat(),
                                    "gross_avoided": round(avoided, 2),
                                    "inflows_forgone": round(recovered, 2),
                                    "ratio_used": round(ratio, 3)})
    evict["cash_avoided"] = round(evict["cash_avoided"], 2)
    out["eviction_sim"] = evict

    with open("/tmp/R0A_PAPER_BACKTEST.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str)

    print(json.dumps({k: out[k] for k in
                      ("n_fills", "n_events", "full_tape", "kept", "dropped")}, indent=1))
    print("DROPPED_BY:", json.dumps(out["dropped_by"], indent=1))
    print("EVICTION:", json.dumps({k: v for k, v in evict.items() if k != "detail"}))
    print("META_MISSING:", n_meta_missing)


if __name__ == "__main__":
    main()
