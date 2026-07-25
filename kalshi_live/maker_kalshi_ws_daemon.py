#!/usr/bin/env python3
"""KALSHI WS MAKER DAEMON — event-driven requoting on top of the UNCHANGED quoter.

Replaces the 2-minute systemd timer with a long-lived process. Additive-only:
maker_kalshi_quoter.py is imported, NEVER modified — every guard (loss meter,
breakers, ladder pairing, caps, STOP, netev/capture gates) runs verbatim.

TWO STAGES:

  STAGE A (always on): WS-TRIGGERED FULL CYCLES.
    Book moves / our fills trigger an immediate M.run_once() — the complete
    guarded cycle, byte-identical logic to the timer — plus a WS_COLD_S
    heartbeat. Reaction ~= run_once REST latency (~2-4s) vs a 0-120s timer.

  STAGE B (KALSHI_WS_HOT=1, DEFAULT 0 — armed only after adversarial re-review):
    surgical reprice from the WS mirror between cycles.

STAGE B SAFETY MODEL (rebuilt after the 4-lens adversarial review 2026-07-25;
all four reviewers' mandatory findings are implemented here):
  - REPRICE-ONLY diff: create requires a same-side same-count standing match;
    side-pull is cancel-only; resize/expansion waits for the cold cycle.
  - REDUCING SIDE IS UNTOUCHABLE: when |inv| >= INV_TOLERANCE the hot path
    neither cancels nor creates on the reducing side (kills the stale-count
    unwind overshoot AND any stranding/KEEP_BOTH parity gap — review F3/F4).
  - CANCEL FAILURES ABORT: batch_cancel's failed list is inspected; any
    failure -> no creates, ctx invalidated, full cycle forced (review F1/C7).
  - COLD MUTUAL EXCLUSION: in_cold flag + ctx kill before every cold cycle —
    hot is dead while run_once runs (review F2, found by all reviewers).
  - DEPTH PRECONDITION: repricing an order requires the mirror to still show
    >= our count at our price — a shrunk level means "possibly just filled"
    -> invalidate + full cycle, never a refill (review F2-partial-fills).
  - FILL-ACK GATE: hot writes require the venue's "subscribed" ack for the
    fill channel this connection (review F3-fill-channel).
  - NOTIONAL HEADROOM: a reprice may not raise resting notional beyond the
    per-market / total capital headroom captured at ctx build (review F5).
  - OFF-LOOP WRITES: venue writes run on a single worker thread with the
    precheck re-validated at execution — blocking HTTP never stalls the WS
    event loop / ping keepalive (review F6/F7/F9).
  - Unique client ids: time_ns + process counter (review F3-collision).

SAFETY INHERITED, NOT REIMPLEMENTED: client defaults dry_run; live mode
requires KALSHI_LIVE_ARMED; all quotes post-only (never cross); run_once
holds the flock so a stray timer cycle can never overlap a daemon cycle.
DEPLOY NOTE: disable the 2-min timer when enabling this service —
  sudo systemctl disable --now polymarket-maker-kalshi-live.timer
(see docs/maker_handoffs/WS_DAEMON_DEPLOY.md). Not deployed by default.
"""
import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as M                      # noqa: E402  (UNCHANGED quoter)
from kalshi_ws_feed import Feed                      # noqa: E402

# ---- knobs (env-overridable; defaults are the safe ones) ----
WS_COLD_S = M._envi("KALSHI_WS_COLD_S", 60)           # heartbeat full cycle
# STOP heartbeat is the SAME as normal (re-review REGRESSION): widening this to
# 300s delayed the first flatten pass of an operator-written STOP from <=60s to
# <=300s — strictly worse than both the original build and the 2-min timer.
# Offset churn is the lesser evil; getting flat fast is the point of STOP.
WS_STOP_COLD_S = M._envi("KALSHI_WS_STOP_COLD_S", 60)
WS_FILL_COOLDOWN_S = M._envf("KALSHI_WS_FILL_COOLDOWN_S", 3.0)  # hot stand-down after a fill
_INV_MARGIN = M._envf("KALSHI_WS_INV_MARGIN_CT", 0.0)   # extra conservatism below INV_TOLERANCE
WS_CYCLE_MIN_GAP_S = M._envi("KALSHI_WS_CYCLE_MIN_GAP_S", 5)   # full-cycle debounce
WS_HOT = M._envi("KALSHI_WS_HOT", 0)                  # Stage B flag — DEFAULT OFF
WS_HOT_DEBOUNCE_MS = M._envi("KALSHI_WS_HOT_DEBOUNCE_MS", 250)
WS_HOT_WRITES_PER_S = M._envf("KALSHI_WS_HOT_WRITES_PER_S", 3.0)
WS_HOT_BURST = M._envi("KALSHI_WS_HOT_BURST", 6)
WS_STALE_S = M._envi("KALSHI_WS_STALE_S", 90)         # hot ctx max age
WS_MIN_MOVE = M._envf("KALSHI_WS_MIN_MOVE", 0.01)     # 1 tick
LOG_PATH = os.path.join(M.DATA_DIR, "ws_daemon_log.jsonl")


def _log(row):
    row["ts"] = M.utcnow().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _plan_sig():
    """(path, size) of today's plan file — foreign-writer tripwire (a stray
    TIMER/manual quoter run between daemon cycles; the daemon's own cold cycle
    is covered separately by in_cold + ctx kill)."""
    path = os.path.join(M.DATA_DIR, f"plans-{M.utcnow().strftime('%Y%m%d')}.jsonl")
    try:
        return path, os.path.getsize(path)
    except OSError:
        return path, 0


def _order_cost(side, price_dollars, count):
    """Collateral consumed by a resting order. Standing/desired prices are
    OUTCOME-scale (matching _live_standing and _mkt_capital), so cost is
    price*count on both sides."""
    return float(price_dollars) * float(count)


class TokenBucket:
    def __init__(self, rate, burst):
        self.rate, self.burst = rate, burst
        self.tokens = float(burst)
        self.t = time.monotonic()

    def take(self, n=1):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


class HotContext:
    """Per-ticker frozen risk context, rebuilt right after every cold cycle.
    stale() => unusable. Mutated only inside build() (cold worker thread) and
    by the write worker under std_lock; hot reads happen on the event loop —
    built_mono is written LAST in build() so a torn read can only see a stale
    (rejected) context, never a half-built accepted one."""

    def __init__(self):
        self.built_mono = None                        # None = never built / invalidated
        self.by_ticker = {}                           # t -> dict(m, own, inv, ev, cost)
        self.event_inv = {}                           # event_key -> max |inv| in that event
        self.standing = {}                            # t -> [order dicts] (live view)
        self.breaker = False
        self.plan_sig = ("", 0)
        self.headroom_total = 0.0                     # $ resting-notional headroom vs caps
        self.headroom_mkt = {}                        # t -> $ headroom vs per-market cap

    def invalidate(self):
        """Mark the context unusable. Uses None, NOT 0.0 (re-review SENTINEL):
        CLOCK_MONOTONIC is boot-relative on Linux, so 0.0 reads as 'fresh' for
        the first WS_STALE_S seconds of a boot-time systemd start."""
        self.built_mono = None

    def stale(self):
        if self.built_mono is None:
            return True
        return (time.monotonic() - self.built_mono) > WS_STALE_S

    def build(self, client, programs, std_lock):
        now = M.utcnow()
        footprint = M.select_footprint(programs, now)
        standing, _raw = M._live_standing(client)
        own = M.own_resting(standing)
        held_cost, held_by, cost_by = M._held_cost(client)
        naked_by = M.ladder_pairing(held_by, {})
        ev_delta = M.event_deltas(held_by)
        # breaker: same formula as run_once, over the state file's held history
        st = M.load_state()
        risk = M.naked_held_cost(held_by, cost_by)
        hist = [h for h in st.get("held_hist", [])
                if now.timestamp() - h[0] < M.BREAKER_WINDOW_S] or [[now.timestamp(), risk]]
        breaker = (risk - min(h[1] for h in hist) > M.BREAKER_HELD_GROWTH_USD
                   or risk > M.HELD_MAX_USD)
        by_ticker = {
            m["ticker"]: {"m": m, "own": own.get(m["ticker"]),
                          "inv": naked_by.get(m["ticker"], 0.0),
                          "ev": M.event_delta_for(ev_delta, m["ticker"]),
                          "cost": cost_by.get(m["ticker"], 0.0)}
            for m in footprint}
        # notional headroom vs capital caps (review F5): the hot path may not
        # push resting notional past what the cold guards evaluated.
        mkt_cost = {t: sum(_order_cost(o["side"], o["price_dollars"], o["count"])
                           for o in ol) for t, ol in standing.items()}
        total_cost = sum(mkt_cost.values())
        # per-EVENT max |inv| — hot refuses any ticker whose EVENT carries
        # inventory (re-review: covers BOTH the stale-inv reduce_side race AND
        # the ladder self-hedge hatch, which fires on ladder events with naked
        # legs and appends orders hot can never regenerate).
        event_inv = {}
        for t, v in naked_by.items():
            k = M._event_key(t)
            event_inv[k] = max(event_inv.get(k, 0.0), abs(v))
        for t, v in held_by.items():
            k = M._event_key(t)
            event_inv[k] = max(event_inv.get(k, 0.0), abs(v))
        with std_lock:
            self.breaker = breaker
            self.by_ticker = by_ticker
            self.event_inv = event_inv
            self.standing = standing
            self.headroom_mkt = {t: max(0.0, M.MAX_MARKET_CAPITAL - c)
                                 for t, c in mkt_cost.items()}
            self.headroom_total = max(0.0, M.MAX_TOTAL_CAPITAL - total_cost)
            self.plan_sig = _plan_sig()
            self.built_mono = time.monotonic()        # LAST: publishes the context
        return set(by_ticker) | set(standing)


def hot_reprice_ops(desired_t, standing_t, reduce_side=None):
    """STRICT REPRICE-PAIR diff for one ticker.
    Returns (ops:[{order_id, side, price_dollars, want_count}]) — each op is a
    cancel PAIRED with the replacement it authorizes. There is no standalone
    cancel and no standalone create.

    Rules:
      - same side, same count, different price -> reprice pair
      - anything else (side absent from desired, count mismatch, multi-level,
        unwind-tagged) -> NO ACTION, the guarded cold cycle owns it
      - reduce_side is UNTOUCHABLE

    NO STANDALONE SIDE-PULL (re-review LADDER-HATCH): run_once appends a
    `reason='ladder'` self-hedge to desired AFTER desired_quotes returns, so a
    hot recompute can never reproduce it — pulling "a side hot sees no desired
    for" would strip the floored-pair hedge off a sibling strike and convert a
    safe pair into naked settlement risk. Pulls stay a cold-cycle action.

    want_count is only the UPPER BOUND for the replacement; the executor must
    clamp it to the count the venue's cancel response confirms was still
    resting (BLOCKER B3-1: book depth cannot reveal our own remaining size)."""
    ops = []
    by_side_std = {}
    for o in standing_t or []:
        by_side_std.setdefault(o["side"], []).append(o)
    by_side_des = {}
    for q in desired_t or []:
        by_side_des.setdefault(q["side"], []).append(q)
    for side, std in by_side_std.items():
        if side == reduce_side:
            continue                                   # reducing side: hands off
        des = by_side_des.get(side, [])
        if len(std) != 1 or len(des) != 1:
            continue                                   # incl. des == [] -> no pull
        o, q = std[0], des[0]
        if q.get("reason") == "unwind":
            continue                                   # never hot-manage unwind quotes
        if (abs(float(o["count"]) - float(q["count"])) < 1e-9
                and abs(float(o["price_dollars"]) - float(q["price_dollars"])) >= 1e-9):
            ops.append({"order_id": o["order_id"], "side": side,
                        "price_dollars": q["price_dollars"],
                        "want_count": float(q["count"]),
                        "old_price": float(o["price_dollars"])})
    return ops


class Daemon:
    def __init__(self):
        self.client = M.KalshiOrderClient()
        self.cycle_req = threading.Event()            # a book/fill event wants a cycle
        self.stopping = threading.Event()
        self.last_cycle_mono = 0.0
        self.last_refs = {}                           # ticker -> (best_yes, best_no)
        self.ctx = HotContext()
        self.bucket = TokenBucket(WS_HOT_WRITES_PER_S, WS_HOT_BURST)
        self.last_hot_mono = {}                       # ticker -> mono of last hot action
        self.programs = []
        self.programs_mono = 0.0
        self.feed = None                              # current Feed (fill-ack gate reads it)
        self.in_cold = False                          # cold cycle in flight -> hot dead
        self.std_lock = threading.Lock()              # guards ctx.standing/headroom mutation
        self.cold_lock = threading.Lock()             # SERIALIZES hot writes vs run_once
        self.last_fill_mono = {}                      # event_key -> mono of last fill frame
        self._writer = ThreadPoolExecutor(max_workers=1)   # single ordered write worker
        self._coid = 0                                # process-unique client_order_id counter

    # ---------------- programs cache (10 min) ----------------
    def _programs(self):
        if time.monotonic() - self.programs_mono > 600 or not self.programs:
            progs, cursor = [], ""
            for _ in range(5):
                d = M.public_get("/trade-api/v2/incentive_programs?status=active&limit=10000"
                                 + (f"&cursor={cursor}" if cursor else ""))
                progs.extend(d.get("incentive_programs", []))
                cursor = d.get("next_cursor") or ""
                if not cursor:
                    break
            self.programs = progs
            self.programs_mono = time.monotonic()
        return self.programs

    # ---------------- cold path ----------------
    def cold_cycle(self):
        """The full guarded cycle — the quoter, verbatim — then rebuild hot ctx.
        Holds cold_lock for the WHOLE cycle so an already-submitted hot write can
        never interleave with run_once's read->diff->write (re-review B2-1: the
        in_cold flag alone did not JOIN the write worker, so a create landing
        after run_once's standing read was invisible to its diff = same-side
        stacking, the exact class the cancel-failure guard exists to prevent)."""
        t0 = time.monotonic()
        with self.cold_lock:
            try:
                M.run_once()
            except Exception as e:                    # cycle errors are loud, not fatal
                _log({"ev": "cold_cycle_error", "err": repr(e)[:200]})
            self.last_cycle_mono = time.monotonic()
            tickers = None
            if not os.path.exists(M.STOP_FILE) and self.client.mode != "dry_run":
                try:
                    tickers = self.ctx.build(self.client, self._programs(), self.std_lock)
                except Exception as e:
                    self.ctx.invalidate()             # ctx unusable -> hot disabled
                    _log({"ev": "ctx_build_error", "err": repr(e)[:200]})
        _log({"ev": "cold_cycle", "secs": round(time.monotonic() - t0, 2),
              "hot_ctx": bool(tickers)})
        return tickers

    # ---------------- hot path (Stage B) ----------------
    def _hot_precheck(self, ticker, mirror):
        if not WS_HOT:
            return "flag_off"
        if self.client.mode == "dry_run":
            return "dry_run"
        if self.in_cold:
            return "cold_running"                     # review F2: hot dead during run_once
        if os.path.exists(M.STOP_FILE):
            return "stop_file"
        if self.ctx.stale():
            return "ctx_stale"
        if self.ctx.breaker:
            return "breaker"                          # review F4: cold owns reduce-only shaping
        if mirror.dirty:
            return "mirror_dirty"
        if self.feed is not None and not self.feed.fills_confirmed:
            return "fills_unconfirmed"                # review F3: no ack -> no invalidation -> no hot
        if ticker not in self.ctx.by_ticker:
            return "not_in_ctx"
        p, sz = _plan_sig()
        if (p, sz) != self.ctx.plan_sig:
            return "foreign_writer"                   # a stray timer/manual quoter ran
        mono = time.monotonic()
        if (mono - self.last_hot_mono.get(ticker, 0.0)) * 1000 < WS_HOT_DEBOUNCE_MS:
            return "debounce"
        return None

    def hot_event(self, ticker, mirror, t_event_mono):
        """Loop-thread half: precheck + compute ops + submit to the write worker.
        Never blocks on HTTP (review F6/F7/F9)."""
        reason = self._hot_precheck(ticker, mirror)
        if reason:
            return reason
        # EVENT-INVENTORY GATE (re-review B3-3 + LADDER-HATCH): if ANY ticker in
        # this event carries inventory near the tolerance, stand down entirely.
        # ctx inv is up to WS_STALE_S old, so a fill we have not processed could
        # (a) invert reduce_side, or (b) mean run_once attached a ladder hedge we
        # cannot regenerate. One conservative gate closes both.
        ev_key = M._event_key(ticker)
        # floored at 1ct so a mis-set margin can never drive the threshold to or
        # below zero and silently disable the whole hot path.
        _inv_thresh = max(1.0, M.INV_TOLERANCE - _INV_MARGIN)
        if self.ctx.event_inv.get(ev_key, 0.0) >= _inv_thresh:
            return "event_has_inventory"
        # POST-FILL COOLDOWN: a fill anywhere in this event makes every cached
        # count suspect for a beat, even after ctx rebuild.
        if (time.monotonic() - self.last_fill_mono.get(ev_key, 0.0)) < WS_FILL_COOLDOWN_S:
            return "fill_cooldown"
        c = self.ctx.by_ticker[ticker]
        ys, ns = mirror.rows()
        try:
            q = M.desired_quotes(c["m"], ys, ns, M.utcnow(), own=c["own"], inv=c["inv"],
                                 event_delta=c["ev"], stats={}, cost=c["cost"])
        except Exception as e:
            _log({"ev": "hot_quote_error", "ticker": ticker, "err": repr(e)[:160]})
            return "quote_error"
        inv = c["inv"]
        reduce_side = None
        if abs(inv) >= M.INV_TOLERANCE:
            reduce_side = "no" if inv > 0 else "yes"
        with self.std_lock:
            standing_t = list(self.ctx.standing.get(ticker) or [])
        ops = hot_reprice_ops(q, standing_t, reduce_side=reduce_side)
        if not ops:
            return "no_op"
        # DEPTH PRECONDITION over ALL standing orders on this ticker (re-review
        # B3-2: scoping it to the touched orders discarded direct evidence that
        # our inventory just changed). NOTE this is only corroborating evidence —
        # it CANNOT see our own remaining size when other makers share the level;
        # the authoritative clamp is the cancel response in _exec_hot.
        for o in standing_t:
            book = mirror.yes if o["side"] == "yes" else mirror.no
            if book.get(round(float(o["price_dollars"]), 4), 0.0) < float(o["count"]) - 1e-9:
                self.ctx.invalidate()
                self.cycle_req.set()
                return "possible_fill"
        # NOTIONAL HEADROOM — RESERVED here, in ONE critical section with the
        # check (re-review H7-1b: reading on the loop thread and decrementing at
        # execution let N tickers pass the same total). Reservation is released
        # by the executor if the write does not happen.
        delta = 0.0
        for op in ops:
            delta += (_order_cost(op["side"], op["price_dollars"], op["want_count"])
                      - _order_cost(op["side"], op["old_price"], op["want_count"]))
        if delta > 0:
            with self.std_lock:
                if (delta > self.ctx.headroom_mkt.get(ticker, 0.0) + 1e-9
                        or delta > self.ctx.headroom_total + 1e-9):
                    return "notional_cap"
                self.ctx.headroom_total -= delta
                self.ctx.headroom_mkt[ticker] = self.ctx.headroom_mkt.get(ticker, 0.0) - delta
        if not self.bucket.take(2 * len(ops)):
            if delta > 0:
                with self.std_lock:                    # release the reservation
                    self.ctx.headroom_total += delta
                    self.ctx.headroom_mkt[ticker] = \
                        self.ctx.headroom_mkt.get(ticker, 0.0) + delta
            return "budget"
        self.last_hot_mono[ticker] = time.monotonic()
        built_snapshot = self.ctx.built_mono
        self._writer.submit(self._exec_hot, ticker, ops, delta,
                            t_event_mono, built_snapshot)
        return "submitted"

    def _abort_ok(self, built_snapshot):
        """Every precondition that must STILL hold at write time. Re-checked
        before EACH venue write, not once at entry (re-review B2-2: a fill or a
        cold cycle starting during a 15s-timeout cancel could not stop the
        follow-on create)."""
        if self.stopping.is_set() or self.in_cold or os.path.exists(M.STOP_FILE):
            return False
        if self.ctx.built_mono != built_snapshot:
            return False
        if self.feed is not None and not self.feed.fills_confirmed:
            return False                              # re-review FILL-ACK-2
        return True

    def _exec_hot(self, ticker, ops, reserved_cost, t_event_mono, built_snapshot):
        """Write-worker half. Runs OFF the event loop, holding cold_lock so a
        cold cycle can never interleave (re-review B2-1).

        For each op: cancel, read the venue's CONFIRMED remaining count from the
        cancel response, and re-create AT MOST that many. If the venue does not
        state the remaining count, we do NOT re-create — the order is simply
        gone (shrink direction) and the cold cycle replaces it. This makes the
        partial-fill refill (B3-1) structurally impossible: we can never place
        more than the venue just told us was unfilled."""
        ok_c = ok_n = 0
        spent = 0.0
        with self.cold_lock:
            try:
                for op in ops:
                    if not self._abort_ok(built_snapshot):
                        _log({"ev": "hot_abort_stale", "ticker": ticker})
                        break
                    oid = op["order_id"]
                    resp = self.client.cancel_order(oid)
                    remaining = self.client.cancel_remaining_ct(resp)
                    ok_c += 1
                    with self.std_lock:               # the order is gone either way
                        self.ctx.standing[ticker] = [
                            o for o in (self.ctx.standing.get(ticker) or [])
                            if o["order_id"] != oid]
                    if remaining is None:
                        # UNKNOWN remaining -> never re-create blind.
                        _log({"ev": "hot_cancel_no_remaining", "ticker": ticker})
                        self.ctx.invalidate()
                        self.cycle_req.set()
                        break
                    place = min(float(op["want_count"]), float(remaining))
                    if place <= 0:
                        # fully filled while resting: nothing to replace, and our
                        # inventory just changed -> hand to the guarded cycle.
                        _log({"ev": "hot_was_filled", "ticker": ticker})
                        self.ctx.invalidate()
                        self.cycle_req.set()
                        break
                    if not self._abort_ok(built_snapshot):
                        _log({"ev": "hot_abort_stale_precreate", "ticker": ticker})
                        self.ctx.invalidate()
                        self.cycle_req.set()
                        break
                    self._coid += 1
                    r = self.client.create_quote(
                        ticker, op["side"], op["price_dollars"], place,
                        client_order_id=f"ws-{time.time_ns()}-{self._coid}-{op['side']}")
                    o = r.get("order") if isinstance(r, dict) and \
                        isinstance(r.get("order"), dict) else (r if isinstance(r, dict) else {})
                    new_oid = o.get("order_id")
                    if not new_oid:
                        # the order MAY exist server-side — never invent an id.
                        self.ctx.invalidate()
                        self.cycle_req.set()
                        _log({"ev": "hot_create_no_id", "ticker": ticker})
                        break
                    with self.std_lock:
                        self.ctx.standing.setdefault(ticker, []).append(
                            {"order_id": new_oid, "side": op["side"],
                             "price_dollars": op["price_dollars"], "count": place})
                    spent += max(0.0, _order_cost(op["side"], op["price_dollars"], place)
                                 - _order_cost(op["side"], op["old_price"], place))
                    ok_n += 1
            except Exception as e:
                _log({"ev": "hot_write_error", "ticker": ticker, "err": repr(e)[:160]})
                self.ctx.invalidate()                 # view uncertain -> force cold rebuild
                self.cycle_req.set()
            finally:
                # release any reservation we did not actually spend
                unspent = reserved_cost - spent
                if abs(unspent) > 1e-9:
                    with self.std_lock:
                        self.ctx.headroom_total += unspent
                        self.ctx.headroom_mkt[ticker] = \
                            self.ctx.headroom_mkt.get(ticker, 0.0) + unspent
        ms = round((time.monotonic() - t_event_mono) * 1000)
        _log({"ev": "hot_reprice", "ticker": ticker, "cancels": ok_c, "creates": ok_n,
              "reaction_ms": ms})

    # ---------------- wiring ----------------
    def on_book(self, ticker, mirror):
        t_event = time.monotonic()
        by, bn = mirror.best()
        prev = self.last_refs.get(ticker)
        self.last_refs[ticker] = (by, bn)
        if prev is None:
            return
        moved = (by is not None and prev[0] is not None and abs(by - prev[0]) >= WS_MIN_MOVE) \
            or (bn is not None and prev[1] is not None and abs(bn - prev[1]) >= WS_MIN_MOVE) \
            or (by is None) != (prev[0] is None) or (bn is None) != (prev[1] is None)
        if not moved:
            return
        out = self.hot_event(ticker, mirror, t_event) if WS_HOT else "stage_a"
        if out in ("stage_a", "flag_off", "ctx_stale", "mirror_dirty", "not_in_ctx",
                   "quote_error", "budget", "foreign_writer", "dry_run", "breaker",
                   "fills_unconfirmed", "stop_file", "cold_running", "notional_cap",
                   "event_has_inventory", "fill_cooldown", "possible_fill"):
            self.cycle_req.set()                      # fall back to a full guarded cycle

    def on_fill(self, msg):
        t = msg.get("market_ticker", "") or msg.get("ticker", "")
        _log({"ev": "fill", "ticker": t, "count": msg.get("count")})
        if t:
            # stand hot down for the whole EVENT for a beat — sibling strikes'
            # cached counts are suspect too (re-review B3-3).
            self.last_fill_mono[M._event_key(t)] = time.monotonic()
        self.ctx.invalidate()                         # inventory changed: ctx stale NOW
        self.cycle_req.set()                          # full guard pass immediately

    def _new_feed(self, watch):
        feed = Feed(watch, on_book=self.on_book, on_fill=self.on_fill,
                    want_fills=self.client.mode != "dry_run")
        self.feed = feed
        return feed

    async def _run_cold(self):
        """Cold cycle with hot mutual exclusion (review F2): kill the hot ctx
        and raise the in_cold flag BEFORE the worker starts; both are set on
        the event-loop thread, the same thread that runs hot_event."""
        self.in_cold = True
        self.ctx.invalidate()
        try:
            return await asyncio.to_thread(self.cold_cycle)
        finally:
            self.in_cold = False

    async def main(self):
        _log({"ev": "daemon_start", "mode": self.client.mode, "hot": bool(WS_HOT),
              "cold_s": WS_COLD_S})
        tickers = await self._run_cold() or set()
        watch = sorted(tickers)[:40] or ["KXB200MON-26JUL31-6.960"]
        feed = self._new_feed(watch)
        stop_ev = asyncio.Event()
        feed_task = asyncio.create_task(feed.run(stop_event=stop_ev))
        try:
            while not self.stopping.is_set():
                stopped = os.path.exists(M.STOP_FILE)
                interval = WS_STOP_COLD_S if stopped else WS_COLD_S
                # cycle_req is honored under STOP TOO (re-review REGRESSION):
                # suppressing it delayed the flatten pass; run_once under STOP
                # maker-flattens, which is exactly what we want to happen sooner.
                triggered = self.cycle_req.is_set()
                due = (time.monotonic() - self.last_cycle_mono) >= interval
                gap_ok = (time.monotonic() - self.last_cycle_mono) >= WS_CYCLE_MIN_GAP_S
                if (triggered and gap_ok) or due:
                    self.cycle_req.clear()
                    new = await self._run_cold()
                    if new and sorted(new)[:40] != watch:
                        watch = sorted(new)[:40]      # footprint changed: resubscribe
                        self.last_refs = {t: v for t, v in self.last_refs.items()
                                          if t in set(watch)}
                        stop_ev.set()
                        try:                          # bounded (review: 302s freeze fix)
                            await asyncio.wait_for(feed_task, timeout=15)
                        except (asyncio.TimeoutError, Exception):
                            feed_task.cancel()
                        stop_ev = asyncio.Event()
                        feed = self._new_feed(watch)
                        feed_task = asyncio.create_task(feed.run(stop_event=stop_ev))
                if feed_task.done():
                    # WATCHDOG (review F5a): the feed must never die silently —
                    # log the exception and rebuild; Stage A degrades to the
                    # heartbeat in between, never unattended.
                    err = None
                    try:
                        err = feed_task.exception()
                    except Exception:
                        pass
                    _log({"ev": "feed_task_died", "err": repr(err)[:200]})
                    stop_ev = asyncio.Event()
                    feed = self._new_feed(watch)
                    feed_task = asyncio.create_task(feed.run(stop_event=stop_ev))
                await asyncio.sleep(0.2)
        finally:
            self.stopping.set()
            stop_ev.set()
            try:
                await asyncio.wait_for(feed_task, timeout=10)
            except (asyncio.TimeoutError, Exception):
                feed_task.cancel()
            self._writer.shutdown(wait=True)
            _log({"ev": "daemon_stop"})


def main():
    d = Daemon()
    try:
        asyncio.run(d.main())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
