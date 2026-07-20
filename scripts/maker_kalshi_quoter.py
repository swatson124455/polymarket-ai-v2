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
# series allowlist: if set, ONLY quote markets whose series (ticker before the first
# '-') is listed. The pilot scopes to the weather/temp slice; empty = no filter (legacy).
SERIES_ALLOW = [s for s in os.environ.get("KALSHI_SERIES_ALLOW", "").split(",") if s.strip()]
# --- DELTA-NEUTRALITY (inventory control) — the core maker mandate ---
# inv = signed net contracts on a ticker (+ = long yes, - = long no/short yes). PER-TICKER
# (Kalshi's ladder margin-offset is a CAPITAL concern, not directional delta — do NOT net
# across strikes here). Above SOFT: skew 1 tick inside + shrink the ACCUMULATING side, keep
# the REDUCING side at reference (its fill passively unwinds at $0 maker fee). Above HARD:
# pull the accumulating side entirely (JOIN) / pull the whole market (ACTIVATE, void-safe).
INV_SOFT_CT = _envf("KALSHI_INV_SOFT_CT", 30.0)
INV_HARD_CT = _envf("KALSHI_INV_HARD_CT", 80.0)
TICK = 0.01
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
        if SERIES_ALLOW and t.split("-")[0] not in SERIES_ALLOW:
            continue                       # series allowlist (pilot = weather/temp only)
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
    """Parse [[price_str,size_str]...] to [(price,size)] floats. Returns
    (levels, n_malformed). Rows that fail to PARSE are counted (n_malformed) —
    a systematic parse failure (e.g. API shape change) must not be invisible;
    rows with size<=0 are legit-empty and NOT counted as malformed."""
    out, malformed = [], 0
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            malformed += 1
            continue
        if s > 0:
            out.append((p, s))
    return out, malformed


def _capped_join(best, other_price):
    """Contracts to rest at `best` so this side's $ stays within half the
    per-market cap; >=1 (caller gates unpriceable elsewhere)."""
    per_side = MAX_MARKET_CAPITAL / 2.0
    n = min(JOIN_SIZE, int(per_side / best)) if best > 0 else 0
    return max(1, n)


def desired_quotes(m, yes_levels, no_levels, now, own=None, inv=0.0, stats=None):
    """Desired resting orders for one market. Returns list of
    {side, price_dollars, count, reason} or [] (gated / unquotable).
    own: {'yes':contracts,'no':contracts} of OUR current resting size on each side.
    inv: OUR signed net position on this ticker (+ long yes, - long no); drives the
    delta-neutral shaping — throttle the accumulating side, keep the reducing side at
    reference as a $0-fee passive unwind."""
    own = own or {"yes": 0.0, "no": 0.0}
    inv = float(inv or 0.0)
    (yl, bad_y), (nl, bad_n) = _levels(yes_levels), _levels(no_levels)
    if stats is not None:
        stats["dropped_book_rows"] = stats.get("dropped_book_rows", 0) + bad_y + bad_n
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
    if best_y + best_n >= 1.0:
        return []                                   # crossed/degenerate book — a yes
        # bid @best_y and no bid @best_n would cross (yes_bid >= yes_ask); skip so a
        # stale-book quote can never take even if post_only were silently ignored.
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
        # ACTIVATE: we supply the Target-Size depth. Inventory lever here is PULL-THE-MARKET
        # (never price-skew or shrink below target — that re-voids the snapshot and zeroes
        # BOTH sides' reward). Carrying inventory on a thin market we're activating is the
        # worst adverse-selection spot, so stop quoting it entirely above SOFT.
        if abs(inv) > INV_SOFT_CT:
            return []
        add_y = max(JOIN_SIZE, target - ext_y)
        add_n = max(JOIN_SIZE, target - ext_n)
        cap = best_y * add_y + best_n * add_n
        if cap > MAX_ACTIVATE_CAPITAL:
            return []                               # too expensive to activate
        quotes.append({"side": "yes", "price_dollars": best_y, "count": int(add_y), "reason": "activate"})
        quotes.append({"side": "no", "price_dollars": best_n, "count": int(add_n), "reason": "activate"})
    else:
        # JOIN: external depth already meets Target both sides, so shrinking/pulling OUR size
        # never voids the market — free to shape by inventory.
        y_price, y_cnt = best_y, _capped_join(best_y, best_n)
        n_price, n_cnt = best_n, _capped_join(best_n, best_y)
        # accumulating side = the one whose fill grows |inv|. long yes(+): yes-bid accumulates,
        # no-bid reduces (a filled no-bid shorts yes). long no(-): mirror.
        if abs(inv) > INV_SOFT_CT:
            over = min(1.0, (abs(inv) - INV_SOFT_CT) / max(1.0, INV_HARD_CT - INV_SOFT_CT))
            if inv > 0:                             # long yes -> throttle YES (accumulating)
                if abs(inv) > INV_HARD_CT:
                    y_cnt = 0                       # pull the accumulating side entirely
                else:
                    y_price = round(best_y - TICK, 4)   # 1 tick inside: half score, fewer fills
                    y_cnt = max(1, int(y_cnt * (1 - over)))
            else:                                   # long no -> throttle NO (accumulating)
                if abs(inv) > INV_HARD_CT:
                    n_cnt = 0
                else:
                    n_price = round(best_n - TICK, 4)
                    n_cnt = max(1, int(n_cnt * (1 - over)))
        # re-apply spread_sanity to any skewed price; keep only priceable, non-empty sides
        if y_cnt > 0 and MIN_PRICE_DOLLARS < y_price <= MAX_PRICE_DOLLARS:
            quotes.append({"side": "yes", "price_dollars": y_price, "count": y_cnt, "reason": "join"})
        if n_cnt > 0 and MIN_PRICE_DOLLARS < n_price <= MAX_PRICE_DOLLARS:
            quotes.append({"side": "no", "price_dollars": n_price, "count": n_cnt, "reason": "join"})
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
    os.chdir(DATA_DIR)
    now = utcnow()
    client = KalshiOrderClient()          # dry_run unless operator-configured
    if os.path.exists(STOP_FILE):
        # emergency stop DE-RISKS the book — it does not merely freeze quoting.
        print("STOP sentinel present; flattening resting orders + exiting")
        if client.mode != "dry_run":
            _flatten_all(client)
        return 0
    _reads[0] = 0
    cyc = int(now.timestamp())            # per-cycle nonce for unique order ids
    st = load_state()
    plan = {"ts": now.isoformat(), "mode": client.mode}
    created_ok = []
    cancels, creates = [], []
    fetch_failed = 0
    quote_fail = 0                        # desired_quotes raised (our-logic error, surfaced)
    first_quote_err = None
    qstats = {"dropped_book_rows": 0}     # malformed book rows skipped by _levels
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
        held_by = {}                          # signed net position per ticker (delta)
        held_cost = 0.0
        if client.mode == "dry_run":
            standing = st.get("simulated_standing", {})
            own = {}
        else:
            try:
                standing, raw_rows = _live_standing(client)
            except Exception as e:
                # cannot read our resting orders -> act on NOTHING this cycle (safe):
                # never cancel/create blind. Orders stay as-is; retry next cycle.
                plan["standing_read_failed"] = repr(e)[:120]
                print(f"WARNING could not read standing ({e!r}); skipping cycle (no order ops)")
                return 0                     # finally: appends plan + saves state
            parsed = sum(len(v) for v in standing.values())
            if raw_rows > 0 and parsed == 0:
                # reconcile guard: the exchange HAS resting orders we failed to parse.
                # Do NOT create the book on top of them (that stacks collateral). Halt.
                plan["reconcile_fail"] = raw_rows
                print(f"WARNING reconcile FAIL: {raw_rows} resting rows parsed to 0 — halting (no order ops)")
                return 0
            own = own_resting(standing)
            # DELTA: read signed inventory ONCE, AHEAD of the quote loop, so shaping acts on
            # THIS cycle's position (not one cycle stale). Fail CLOSED (defer) if unreadable —
            # never shape/create blind to our own delta.
            try:
                held_cost, held_by = _held_cost(client)
            except Exception as e:
                plan["positions_read_failed"] = repr(e)[:120]
                print(f"WARNING could not read positions ({e!r}); skipping cycle (delta unknown)")
                return 0

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
                                   now, own=own.get(t), inv=held_by.get(t, 0.0), stats=qstats)
            except Exception as e:
                # isolate one degenerate market, but SURFACE it as quote_fail (a
                # systematic desired_quotes failure must not hide inside gated_out)
                q = []
                quote_fail += 1
                if first_quote_err is None:
                    first_quote_err = f"{t}: {e!r}"
            if q:
                desired[t] = q

        desired, capped_markets = cap_desired(desired, usd_day)     # aggregate $ cap
        cancels, creates = diff_orders(standing, desired)
        creates, budget_dropped = bound_creates(creates, cancels, usd_day)  # whole-ticker

        # execute — each order isolated; one failure never aborts the rest
        cancel_fail = create_fail = create_skipped = 0
        oid_ticker = {o["order_id"]: t for t, ol in standing.items() for o in ol}
        cancelled_ok = set()
        for oid in cancels:
            try:
                client.cancel_order(oid); cancelled_ok.add(oid)
            except Exception:
                cancel_fail += 1
        # tickers whose cancel FAILED -> defer their creates a cycle (never stack
        # stale+new on the same ticker); a failed-cancel oid maps to its ticker.
        failed_cancel_tickers = {oid_ticker.get(oid) for oid in cancels if oid not in cancelled_ok}
        # REAL committed capital = surviving standing (not-cancelled) + held inventory.
        # This is the guard the $ cap actually needs (cap_desired only bounds the
        # freshly-computed desired book, blind to survivors + fills).
        committed = sum(o["price_dollars"] * o["count"]
                        for ol in standing.values() for o in ol
                        if o["order_id"] not in cancelled_ok)
        # held_cost was read ahead of the quote loop (fail-closed there); reuse it — no
        # second positions fetch, and the cycle already halted if it was unreadable.
        committed += held_cost
        for i, c in enumerate(creates):
            cost = c["price_dollars"] * c["count"]
            if c["ticker"] in failed_cancel_tickers:
                create_skipped += 1                 # paired cancel failed; defer
                continue
            if committed + cost > MAX_TOTAL_CAPITAL:
                create_skipped += 1                 # hard committed-capital pre-check
                continue
            try:
                client.create_quote(c["ticker"], c["side"], c["price_dollars"], c["count"],
                                    post_only=True, client_order_id=order_id_for(cyc, i, c["side"]))
                created_ok.append((c, f"sim-{cyc}-{i}")); committed += cost
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
            "create_skipped": create_skipped,
            "quote_fail": quote_fail, "first_quote_err": first_quote_err,
            "dropped_book_rows": qstats["dropped_book_rows"],
            "activate_markets": sum(1 for qs in desired.values()
                                    if qs and qs[0].get("reason") == "activate"),
            "est_capital_usd": round(sum(_mkt_capital(qs) for qs in desired.values()), 2),
            # REAL committed $ (surviving standing + held inventory + new creates) —
            # the number that must respect MAX_TOTAL_CAPITAL, not the desired est above.
            "committed_usd": round(committed, 2),
            "held_cost_usd": round(held_cost, 2),
        })
    finally:
        # bookkeeping ALWAYS runs, even if the cycle body raised
        append_plan(plan)
        save_state(st)
    # escalate to WARNING on a SYSTEMATIC failure (not per-item noise): most quotes
    # failing to compute, most creates rejected, or the whole footprint gated out.
    fp = plan.get("footprint", 0) or 0
    cr = plan.get("creates", 0) or 0
    sysfail = (plan.get("quote_fail", 0) > max(3, 0.5 * fp) or
               (cr and plan.get("create_fail", 0) >= cr) or
               (fp and plan.get("quoted_markets", 0) == 0 and not plan.get("fetch_failed")))
    status = "cycle ok" if not sysfail else "WARNING systematic failure"
    print(f"{status} mode={plan['mode']} footprint={plan.get('footprint','?')} "
          f"quoted={plan.get('quoted_markets','?')} ops={plan.get('order_ops','?')} "
          f"(cancel {plan.get('cancels',0)}/create {plan.get('creates',0)}) "
          f"fails={plan.get('cancel_fail',0)}c/{plan.get('create_fail',0)}cr/"
          f"{plan.get('quote_fail',0)}q skipped={plan.get('create_skipped',0)} "
          f"badrows={plan.get('dropped_book_rows',0)} "
          f"capped={plan.get('capped_markets',0)} write_tokens={plan.get('write_tokens',0)} "
          f"reads={_reads[0]} committed=${plan.get('committed_usd', plan.get('est_capital_usd',0)):,.2f}"
          f"/{MAX_TOTAL_CAPITAL:,.0f} held=${plan.get('held_cost_usd',0):,.2f}"
          + (f" first_err={plan.get('first_quote_err')}" if plan.get("first_quote_err") else ""))
    return 0


def _flatten_all(client):
    """Cancel EVERY resting order (best-effort, per-order isolated). Shared by the
    STOP handler and the standalone kill switch — an emergency stop must de-risk."""
    try:
        orders = client.get_orders("resting").get("orders") or []
    except Exception as e:
        print(f"flatten: could NOT read resting orders ({e!r}) — run flatten_kalshi.py manually")
        return
    n = 0
    for o in orders:
        try:
            client.cancel_order(o["order_id"]); n += 1
        except Exception:
            pass
    print(f"flatten: cancelled {n}/{len(orders)} resting orders")


def _held_cost(client):
    """(total_cost, {ticker: signed_contracts}) of held inventory (fills). Cost is
    CONSERVATIVE — each held contract can be worth up to $1, so |pos|*1 reserves the
    max. Real committed capital must include this, not just the resting book.
    RAISES if positions cannot be read — the caller must fail CLOSED (defer creates),
    never treat unknown inventory as $0 (matches the standing-read/reconcile guards)."""
    pos = client.get_positions()          # may raise -> caller defers all creates
    by, total = {}, 0.0
    for p in (pos.get("market_positions") or []):
        # PROD-VERIFIED 2026-07-20: field is position_fp (string, fractional, signed);
        # 'position' does not exist -> reading it silently blinded the committed cap.
        n = float(p.get("position_fp") or p.get("position") or 0)
        if not n:
            continue
        by[p.get("ticker")] = n
        total += abs(n) * 1.0
    return total, by


def _live_standing(client):
    """Returns (standing_dict, raw_row_count). Reads resting V2 orders back into our
    (outcome, outcome-price) form. Per-order parse is ISOLATED so one malformed
    record cannot crash the cycle before cancels/wind-down run. The raw_row_count
    lets the caller reconcile (rows>0 but parsed==0 => parse failure => halt)."""
    out = defaultdict(list)
    orders = client.get_orders("resting").get("orders") or []
    for o in orders:
        try:
            outcome = o.get("outcome_side")   # 'yes' | 'no'
            price_str = o.get(f"{outcome}_price_dollars") if outcome else None
            if outcome is None or price_str is None:
                continue
            cnt = o.get("remaining_count_fp") or o.get("remaining_count") or \
                o.get("initial_count_fp") or o.get("count") or 0
            out[o["ticker"]].append({"side": outcome, "price_dollars": float(price_str),
                                     "count": int(float(cnt)), "order_id": o["order_id"]})
        except Exception:
            continue
    return dict(out), len(orders)


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
