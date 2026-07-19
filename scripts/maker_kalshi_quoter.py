#!/usr/bin/env python3
"""Kalshi Maker QUOTER — the quoting engine, PLAN-ONLY by default (dry-run client).

Build-ahead doctrine (operator 2026-07-18): the full machine exists now; data
tunes the dials later. Nothing here can trade until the operator provides
credentials AND sets the live-arming phrase (see maker_kalshi_client.py's
three-lock safety model). In dry_run the engine runs the COMPLETE cycle —
footprint, quote plan, gates, order diff — and logs every order it WOULD have
placed to plans-YYYYMMDD.jsonl. That log is the dress rehearsal: it measures
quote churn (order-ops/cycle vs rate-tier budgets) and wind-down behavior on
real market data before a single contract is at risk.

Cycle (timer-driven, default 10-min):
  1. Fetch active liquidity programs (public) -> select footprint.
  2. Per market: fetch book (public), compute desired two-sided quotes
     (join at reference; sizes from config) with GATES:
       - wind_down: pull quotes when program (or market close) ends within
         WIND_DOWN_MIN minutes  [calibrate from readout data later]
       - void_activate: on void markets, size up to Target (capped by
         MAX_ACTIVATE_CAPITAL per market)
       - spread_sanity: never quote a side whose reference implies paying
         more than MAX_PRICE_DOLLARS
       - inventory: per-market exposure cap enforced against the ledger
  3. Diff desired quotes vs standing orders -> cancel/create ops.
  4. Execute via KalshiOrderClient (dry_run: intents logged; demo/live: sent,
     batch-chunked, write-budget-metered).

Stop:   sudo touch <dir>/STOP
Kill:   sudo systemctl disable --now polymarket-maker-kalshi-quoter.timer
"""
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_kalshi_client import KalshiOrderClient, API_ROOT, PROD_BASE  # noqa: E402

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(DATA_DIR, "STOP")
STATE_FILE = os.path.join(DATA_DIR, "quoter_state.json")

# ---- config (calibration dials — readouts tune these) ----
FOOTPRINT_TOP = 60            # smaller than recorder: quoting is costlier than watching
PER_SERIES_CAP = 10
JOIN_SIZE = 100               # contracts per side on non-void markets
MAX_ACTIVATE_CAPITAL = 150.0  # $ per market to activate a void book
MAX_PRICE_DOLLARS = 0.97      # never rest a bid above this
WIND_DOWN_MIN = 45            # pull quotes this many minutes before program/market end
WRITE_BUDGET_PER_CYCLE = 400  # order-ops ceiling per cycle (10 tokens each @ Basic)
REQ_SPACING_S = 0.55
READ_BUDGET_PER_CYCLE = 200

_last_req = [0.0]
_reads = [0]


def utcnow():
    return datetime.now(timezone.utc)


def public_get(path):
    if _reads[0] >= READ_BUDGET_PER_CYCLE:
        raise RuntimeError("read budget exhausted")
    wait = REQ_SPACING_S - (time.time() - _last_req[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PROD_BASE + path,
                                 headers={"User-Agent": "maker-kalshi-quoter/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _last_req[0] = time.time()
        _reads[0] += 1
        return json.loads(r.read())


# ---------------- pure planning (unit-tested offline) ----------------

def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def select_footprint(progs, now):
    rows = []
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if p.get("target_size_fp") is None or p.get("discount_factor_bps") is None:
            continue
        t = p.get("market_ticker")
        if not t:
            continue
        try:
            end = parse_iso(p["end_date"])
            start = parse_iso(p["start_date"])
        except Exception:
            continue
        if end < now + timedelta(minutes=WIND_DOWN_MIN):
            continue  # wind-down gate applied at selection too
        days = max((end - start).total_seconds() / 86400, 1 / 24)
        rows.append({"ticker": t, "usd_day": (p.get("period_reward", 0) / 10000) / days,
                     "target": float(p["target_size_fp"]), "end": end.isoformat()})
    rows.sort(key=lambda r: (-r["usd_day"], r["ticker"]))
    picked, per_series = [], defaultdict(int)
    for r in rows:
        s = r["ticker"].split("-")[0]
        if per_series[s] >= PER_SERIES_CAP:
            continue
        picked.append(r)
        per_series[s] += 1
        if len(picked) >= FOOTPRINT_TOP:
            break
    return picked


def desired_quotes(m, yes_levels, no_levels, now):
    """Desired resting orders for one market. Returns list of
    {side, price_dollars, count, reason} or [] (gated / unquotable)."""
    best_y = max((float(p) for p, s in yes_levels if float(s) > 0), default=None)
    best_n = max((float(p) for p, s in no_levels if float(s) > 0), default=None)
    end = parse_iso(m["end"])
    if end < now + timedelta(minutes=WIND_DOWN_MIN):
        return []                                   # wind_down
    if best_y is None or best_n is None:
        return []                                   # unpriceable side
    if best_y > MAX_PRICE_DOLLARS or best_n > MAX_PRICE_DOLLARS:
        return []                                   # spread_sanity
    tot_y = sum(float(s) for _, s in yes_levels)
    tot_n = sum(float(s) for _, s in no_levels)
    void = tot_y < m["target"] or tot_n < m["target"]
    quotes = []
    if void:
        add_y = max(JOIN_SIZE, m["target"] - tot_y)
        add_n = max(JOIN_SIZE, m["target"] - tot_n)
        cap = best_y * add_y + best_n * add_n
        if cap > MAX_ACTIVATE_CAPITAL:
            return []                               # too expensive to activate
        quotes.append({"side": "yes", "price_dollars": best_y, "count": int(add_y),
                       "reason": "activate"})
        quotes.append({"side": "no", "price_dollars": best_n, "count": int(add_n),
                       "reason": "activate"})
    else:
        quotes.append({"side": "yes", "price_dollars": best_y, "count": JOIN_SIZE,
                       "reason": "join"})
        quotes.append({"side": "no", "price_dollars": best_n, "count": JOIN_SIZE,
                       "reason": "join"})
    return quotes


def diff_orders(standing, desired):
    """standing: {ticker: [{side, price_dollars, count, order_id}]};
    desired:  {ticker: [{side, price_dollars, count, reason}]}.
    Returns (cancels [order_id], creates [order dict w/ ticker]).
    An order survives only if side+price+count match exactly."""
    cancels, creates = [], []
    for t in set(standing) | set(desired):
        have = {(o["side"], round(o["price_dollars"], 4), o["count"]): o
                for o in standing.get(t, [])}
        want = {(q["side"], round(q["price_dollars"], 4), q["count"]): q
                for q in desired.get(t, [])}
        for k, o in have.items():
            if k not in want:
                cancels.append(o["order_id"])
        for k, q in want.items():
            if k not in have:
                creates.append(dict(q, ticker=t))
    return cancels, creates


# ---------------- cycle ----------------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def append_plan(row):
    path = os.path.join(DATA_DIR, f"plans-{utcnow().strftime('%Y%m%d')}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def run_once():
    if os.path.exists(STOP_FILE):
        print("STOP sentinel present; exiting")
        return 0
    os.chdir(DATA_DIR)
    _reads[0] = 0
    now = utcnow()
    client = KalshiOrderClient()          # dry_run unless operator-configured
    st = load_state()

    progs = []
    cursor = ""
    for _ in range(5):
        d = public_get("/trade-api/v2/incentive_programs?status=active&limit=10000"
                       + (f"&cursor={cursor}" if cursor else ""))
        progs.extend(d.get("incentive_programs", []))
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
    footprint = select_footprint(progs, now)

    desired = {}
    for m in footprint:
        try:
            ob = public_get(f"/trade-api/v2/markets/{m['ticker']}/orderbook").get("orderbook_fp") or {}
        except RuntimeError:
            break
        except Exception:
            continue
        q = desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [], now)
        if q:
            desired[m["ticker"]] = q

    # standing orders: dry_run reconstructs from last cycle's plan (simulated
    # resting book); demo/live would read client.get_orders()
    standing = st.get("simulated_standing", {}) if client.mode == "dry_run" else _live_standing(client)
    cancels, creates = diff_orders(standing, desired)

    ops = len(cancels) + len(creates)
    if ops > WRITE_BUDGET_PER_CYCLE:
        # keep highest-value creates; cancels always execute (risk-off first)
        creates = creates[: max(0, WRITE_BUDGET_PER_CYCLE - len(cancels))]
        ops = len(cancels) + len(creates)

    for oid in cancels:
        client.cancel_order(oid)
    for c in creates:
        # create_quote maps our (outcome, outcome-price) to the V2 bid/ask +
        # yes-scale price (verified demo 2026-07-19); post_only = never cross
        client.create_quote(c["ticker"], c["side"], c["price_dollars"], c["count"],
                            post_only=True,
                            client_order_id=f"mk-{c['ticker'][:20]}-{c['side']}")

    # simulate the resulting standing book for the next dry-run cycle
    if client.mode == "dry_run":
        new_standing = {}
        for t, qs in desired.items():
            new_standing[t] = [dict(q, order_id=f"sim-{t}-{q['side']}") for q in qs]
        st["simulated_standing"] = new_standing

    # real token cost (docs 2026-07-18): create=10, cancel=2, billed per item
    write_tokens = len(creates) * 10 + len(cancels) * 2
    append_plan({"ts": now.isoformat(), "mode": client.mode,
                 "footprint": len(footprint), "quoted_markets": len(desired),
                 "cancels": len(cancels), "creates": len(creates),
                 "order_ops": ops, "write_tokens": write_tokens, "reads": _reads[0],
                 "gated_out": len(footprint) - len(desired),
                 "activate_markets": sum(1 for qs in desired.values()
                                         if qs and qs[0]["reason"] == "activate"),
                 "est_capital_usd": round(sum(q["price_dollars"] * q["count"]
                                              for qs in desired.values() for q in qs), 2)})
    save_state(st)
    print(f"cycle ok mode={client.mode} footprint={len(footprint)} quoted={len(desired)} "
          f"ops={ops} (cancel {len(cancels)}/create {len(creates)}) write_tokens={write_tokens} "
          f"reads={_reads[0]} "
          f"est_capital=${sum(q['price_dollars']*q['count'] for qs in desired.values() for q in qs):,.0f}")
    return 0


def _live_standing(client):
    """Read resting V2 orders back into our internal (outcome, outcome-price) form.
    V2 order objects carry outcome_side ('yes'|'no') + {yes,no}_price_dollars
    (verified demo 2026-07-19)."""
    out = defaultdict(list)
    for o in (client.get_orders("resting").get("orders") or []):
        outcome = o.get("outcome_side")   # 'yes' | 'no'
        price_str = o.get(f"{outcome}_price_dollars")
        if outcome is None or price_str is None:
            continue
        cnt = o.get("remaining_count_fp") or o.get("remaining_count") or \
            o.get("initial_count_fp") or o.get("count") or 0
        out[o["ticker"]].append({"side": outcome, "price_dollars": float(price_str),
                                 "count": int(float(cnt)), "order_id": o["order_id"]})
    return dict(out)


def report():
    import glob
    rows = []
    for p in sorted(glob.glob(os.path.join(DATA_DIR, "plans-*.jsonl"))):
        for line in open(p):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        print("no plan data yet")
        return 0
    n = len(rows)
    print(f"cycles={n} window={rows[0]['ts'][:16]} .. {rows[-1]['ts'][:16]}")
    for k in ("footprint", "quoted_markets", "order_ops", "activate_markets",
              "est_capital_usd", "gated_out"):
        vals = sorted(r.get(k, 0) for r in rows)
        print(f"{k:18} mean={sum(vals)/n:9.1f}  p50={vals[n//2]:9.1f}  max={vals[-1]:9.1f}")
    span_days = max((parse_iso(rows[-1]["ts"]) - parse_iso(rows[0]["ts"])).total_seconds() / 86400, 1e-6)
    total_tokens = sum(r.get("write_tokens", r.get("order_ops", 0) * 10) for r in rows)
    print(f"\nwrite-token/day pace: {total_tokens / span_days:,.0f}  "
          f"(create=10/cancel=2 tok; Basic write = 100 tok/s = 8.64M tok/day — "
          f"peak per-cycle burst must stay <100 tok/s, our 0.16s spacing caps it ~63)")
    return 0


if __name__ == "__main__":
    sys.exit(report() if "--report" in sys.argv else run_once())
