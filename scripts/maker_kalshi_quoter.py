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

# ---- config (calibration dials — readouts tune these; env-overridable) ----
def _envi(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


FOOTPRINT_TOP = _envi("KALSHI_FOOTPRINT_TOP", 60)   # markets quoted per cycle
PER_SERIES_CAP = _envi("KALSHI_PER_SERIES_CAP", 10)
JOIN_SIZE = _envi("KALSHI_JOIN_SIZE", 100)          # contracts/side on non-void markets
MAX_ACTIVATE_CAPITAL = _envf("KALSHI_MAX_ACTIVATE_CAPITAL", 150.0)  # $/void market
MAX_MARKET_CAPITAL = _envf("KALSHI_MAX_MARKET_CAPITAL", 250.0)  # $ cap per market (both sides)
MAX_TOTAL_CAPITAL = _envf("KALSHI_MAX_TOTAL_CAPITAL", 10000.0)  # $ cap on the whole resting book
MAX_PRICE_DOLLARS = _envf("KALSHI_MAX_PRICE_DOLLARS", 0.97)  # never rest a bid above this
MIN_PRICE_DOLLARS = _envf("KALSHI_MIN_PRICE_DOLLARS", 0.01)  # never rest a bid at/below this
WIND_DOWN_MIN = _envi("KALSHI_WIND_DOWN_MIN", 45)   # pull quotes N min before end
WRITE_BUDGET_PER_CYCLE = _envi("KALSHI_WRITE_BUDGET", 400)  # order-ops ceiling/cycle
JOIN_ALWAYS = os.environ.get("KALSHI_JOIN_ALWAYS") == "1"   # drill switch (default off)
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
        # period_reward may be present-but-null (pending programs) -> `or 0`, not .get default
        rows.append({"ticker": t, "usd_day": ((p.get("period_reward") or 0) / 10000) / days,
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


def _levels(raw):
    """Parse [[price_str,size_str]...] to [(price,size)] floats, dropping any
    malformed/None entry (never let a bad book row raise mid-cycle)."""
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def _capped_join(best, other_price):
    """Contracts to rest at `best` so this side's $ stays within half the
    per-market cap; >=1 (caller gates unpriceable elsewhere)."""
    per_side = MAX_MARKET_CAPITAL / 2.0
    n = min(JOIN_SIZE, int(per_side / best)) if best > 0 else 0
    return max(1, n)


def desired_quotes(m, yes_levels, no_levels, now, own=None):
    """Desired resting orders for one market. Returns list of
    {side, price_dollars, count, reason} or [] (gated / unquotable).
    own: {'yes':contracts,'no':contracts} of OUR current resting size on each
    side (so activate sizes against EXTERNAL depth and does not chase itself)."""
    own = own or {"yes": 0.0, "no": 0.0}
    yl, nl = _levels(yes_levels), _levels(no_levels)
    best_y = max((p for p, _ in yl), default=None)
    best_n = max((p for p, _ in nl), default=None)
    end = parse_iso(m["end"])
    if end < now + timedelta(minutes=WIND_DOWN_MIN):
        return []                                   # wind_down
    if best_y is None or best_n is None:
        return []                                   # unpriceable side
    if not (MIN_PRICE_DOLLARS < best_y <= MAX_PRICE_DOLLARS) or \
       not (MIN_PRICE_DOLLARS < best_n <= MAX_PRICE_DOLLARS):
        return []                                   # spread_sanity (both bounds)
    # external depth = public depth minus our own resting order on that side
    ext_y = max(0.0, sum(s for _, s in yl) - float(own.get("yes", 0)))
    ext_n = max(0.0, sum(s for _, s in nl) - float(own.get("no", 0)))
    target = m["target"]
    void = ext_y < target or ext_n < target
    if JOIN_ALWAYS:
        # drill/testing switch: tiny join on both sides of any priceable market,
        # ignoring void/activate economics — exercises place/diff/cancel machinery.
        return [{"side": "yes", "price_dollars": best_y, "count": _capped_join(best_y, best_n), "reason": "join"},
                {"side": "no", "price_dollars": best_n, "count": _capped_join(best_n, best_y), "reason": "join"}]
    quotes = []
    if void:
        # size against EXTERNAL depth (stable across cycles; won't chase our own order)
        add_y = max(JOIN_SIZE, target - ext_y)
        add_n = max(JOIN_SIZE, target - ext_n)
        cap = best_y * add_y + best_n * add_n
        if cap > MAX_ACTIVATE_CAPITAL:
            return []                               # too expensive to activate
        quotes.append({"side": "yes", "price_dollars": best_y, "count": int(add_y), "reason": "activate"})
        quotes.append({"side": "no", "price_dollars": best_n, "count": int(add_n), "reason": "activate"})
    else:
        quotes.append({"side": "yes", "price_dollars": best_y,
                       "count": _capped_join(best_y, best_n), "reason": "join"})
        quotes.append({"side": "no", "price_dollars": best_n,
                       "count": _capped_join(best_n, best_y), "reason": "join"})
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


def order_id_for(cyc, i, side):
    """Unique client_order_id per create: cycle-nonce + per-create index + side.
    Unique within a cycle (index) and across cycles (nonce) — no ticker prefix,
    so no truncation collision and no cross-cycle reuse of Kalshi's dedup key."""
    return f"mk-{cyc}-{i}-{side}"


def own_resting(standing):
    """{ticker: {'yes':contracts,'no':contracts}} from our standing orders."""
    out = defaultdict(lambda: {"yes": 0.0, "no": 0.0})
    for t, orders in standing.items():
        for o in orders:
            if o.get("side") in ("yes", "no"):
                out[t][o["side"]] += float(o.get("count") or 0)
    return out


def _mkt_capital(quotes):
    return sum(q["price_dollars"] * q["count"] for q in quotes)


def cap_desired(desired, usd_day):
    """Keep whole markets in strict usd_day priority (highest first), stopping at
    the first that would breach MAX_TOTAL_CAPITAL — i.e. keep the most valuable,
    cut the tail. Returns (kept_desired, dropped_ticker_count)."""
    order = sorted(desired, key=lambda t: -usd_day.get(t, 0))
    kept, total = {}, 0.0
    for i, t in enumerate(order):
        c = _mkt_capital(desired[t])
        if total + c > MAX_TOTAL_CAPITAL:
            return kept, len(order) - i        # everything from here down is dropped
        kept[t] = desired[t]
        total += c
    return kept, 0


def bound_creates(creates, cancels, usd_day):
    """Keep whole-ticker create groups (highest usd_day first) so
    len(cancels)+kept <= WRITE_BUDGET. Never splits a market's two sides.
    Returns (kept_creates, dropped_ticker_count)."""
    if len(cancels) + len(creates) <= WRITE_BUDGET_PER_CYCLE:
        return creates, 0
    by_t = defaultdict(list)
    for c in creates:
        by_t[c["ticker"]].append(c)
    budget = max(0, WRITE_BUDGET_PER_CYCLE - len(cancels))
    kept, used, dropped = [], 0, 0
    for t in sorted(by_t, key=lambda t: -usd_day.get(t, 0)):
        grp = by_t[t]
        if used + len(grp) <= budget:
            kept.extend(grp)
            used += len(grp)
        else:
            dropped += 1
    return kept, dropped


def run_once():
    if os.path.exists(STOP_FILE):
        print("STOP sentinel present; exiting")
        return 0
    os.chdir(DATA_DIR)
    _reads[0] = 0
    now = utcnow()
    cyc = int(now.timestamp())            # per-cycle nonce for unique order ids
    client = KalshiOrderClient()          # dry_run unless operator-configured
    st = load_state()
    plan = {"ts": now.isoformat(), "mode": client.mode}
    created_ok = []
    cancels, creates = [], []
    fetch_failed = 0
    try:
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
        usd_day = {m["ticker"]: m["usd_day"] for m in footprint}

        # standing FIRST so activate can size against external (non-own) depth.
        # In demo/live the PUBLIC orderbook already includes our resting orders, so
        # subtract own to get external depth. In dry_run the public book never
        # contained our (never-placed) simulated orders, so own must be 0 there —
        # subtracting it would double-count and make activate oscillate every cycle.
        standing = st.get("simulated_standing", {}) if client.mode == "dry_run" else _live_standing(client)
        own = {} if client.mode == "dry_run" else own_resting(standing)

        desired = {}
        for m in footprint:
            t = m["ticker"]
            try:
                ob = public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
            except RuntimeError:
                break                     # budget exhausted — stop fetching
            except Exception:
                # transient fetch fail: RETAIN this market's standing (do not
                # let diff cancel everything on it); skip re-pricing this cycle
                if standing.get(t):
                    desired[t] = [{"side": o["side"], "price_dollars": o["price_dollars"],
                                   "count": o["count"]} for o in standing[t]]
                fetch_failed += 1
                continue
            try:
                q = desired_quotes(m, ob.get("yes_dollars") or [], ob.get("no_dollars") or [],
                                   now, own=own.get(t))
            except Exception:
                q = []                    # one degenerate market must not kill the cycle
            if q:
                desired[t] = q

        desired, capped_markets = cap_desired(desired, usd_day)     # aggregate $ cap
        cancels, creates = diff_orders(standing, desired)
        creates, budget_dropped = bound_creates(creates, cancels, usd_day)  # whole-ticker

        # execute — each order isolated; one failure never aborts the rest
        cancel_fail = create_fail = 0
        for oid in cancels:
            try:
                client.cancel_order(oid)
            except Exception:
                cancel_fail += 1
        for i, c in enumerate(creates):
            try:
                client.create_quote(c["ticker"], c["side"], c["price_dollars"], c["count"],
                                    post_only=True, client_order_id=order_id_for(cyc, i, c["side"]))
                created_ok.append((c, f"sim-{cyc}-{i}"))
            except Exception:
                create_fail += 1

        # next dry-run standing = prior standing - cancels + created (reflects truncation)
        if client.mode == "dry_run":
            cx = set(cancels)
            ns = {t: [o for o in olist if o.get("order_id") not in cx]
                  for t, olist in standing.items()}
            for c, oid in created_ok:
                ns.setdefault(c["ticker"], []).append(
                    {"side": c["side"], "price_dollars": c["price_dollars"],
                     "count": c["count"], "order_id": oid})
            st["simulated_standing"] = {t: v for t, v in ns.items() if v}

        plan.update({
            "footprint": len(footprint), "quoted_markets": len(desired),
            "cancels": len(cancels), "creates": len(creates),
            "order_ops": len(cancels) + len(creates),
            "write_tokens": len(creates) * 10 + len(cancels) * 2,
            # retained fetch-fail markets are already IN desired -> do NOT subtract
            # them again (that double-counted and could go negative)
            "reads": _reads[0], "gated_out": len(footprint) - len(desired),
            "fetch_failed": fetch_failed, "capped_markets": capped_markets,
            "budget_dropped_markets": budget_dropped,
            "cancel_fail": cancel_fail, "create_fail": create_fail,
            "activate_markets": sum(1 for qs in desired.values()
                                    if qs and qs[0].get("reason") == "activate"),
            "est_capital_usd": round(sum(_mkt_capital(qs) for qs in desired.values()), 2),
        })
    finally:
        # bookkeeping ALWAYS runs, even if the cycle body raised
        append_plan(plan)
        save_state(st)
    print(f"cycle ok mode={plan['mode']} footprint={plan.get('footprint','?')} "
          f"quoted={plan.get('quoted_markets','?')} ops={plan.get('order_ops','?')} "
          f"(cancel {plan.get('cancels',0)}/create {plan.get('creates',0)}) "
          f"fails={plan.get('cancel_fail',0)}c/{plan.get('create_fail',0)}cr "
          f"capped={plan.get('capped_markets',0)} write_tokens={plan.get('write_tokens',0)} "
          f"reads={_reads[0]} est_capital=${plan.get('est_capital_usd',0):,.0f}")
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
