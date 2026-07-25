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
WS_STOP_COLD_S = M._envi("KALSHI_WS_STOP_COLD_S", 300)  # heartbeat while STOP present
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
        self.built_mono = 0.0
        self.by_ticker = {}                           # t -> dict(m, own, inv, ev, cost)
        self.standing = {}                            # t -> [order dicts] (live view)
        self.breaker = False
        self.plan_sig = ("", 0)
        self.headroom_total = 0.0                     # $ resting-notional headroom vs caps
        self.headroom_mkt = {}                        # t -> $ headroom vs per-market cap

    def stale(self):
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
        with std_lock:
            self.breaker = breaker
            self.by_ticker = by_ticker
            self.standing = standing
            self.headroom_mkt = {t: max(0.0, M.MAX_MARKET_CAPITAL - c)
                                 for t, c in mkt_cost.items()}
            self.headroom_total = max(0.0, M.MAX_TOTAL_CAPITAL - total_cost)
            self.plan_sig = _plan_sig()
            self.built_mono = time.monotonic()        # LAST: publishes the context
        return set(by_ticker) | set(standing)


def hot_reprice_ops(desired_t, standing_t, reduce_side=None):
    """REPRICE-ONLY diff for one ticker.
    Returns (cancels:[order_id], creates:[{side,price_dollars,count}]).
    Rules: same-side same-count price change -> cancel+create; standing side
    absent from desired -> cancel-only; desired side absent from standing or
    count mismatch -> NO ACTION (resize is cold-cycle work).
    reduce_side (review F3/F4): that side is UNTOUCHABLE — no cancels, no
    creates; the guarded cold cycle owns all unwind management."""
    cancels, creates = [], []
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
        if not des:
            cancels.extend(o["order_id"] for o in std)     # pull the side (shrink-only)
            continue
        if len(std) == 1 and len(des) == 1:
            o, q = std[0], des[0]
            if q.get("reason") == "unwind":
                continue                               # never hot-manage unwind quotes
            if (abs(float(o["count"]) - float(q["count"])) < 1e-9
                    and abs(float(o["price_dollars"]) - float(q["price_dollars"])) >= 1e-9):
                cancels.append(o["order_id"])
                creates.append({"side": side, "price_dollars": q["price_dollars"],
                                "count": q["count"]})
        # multi-level or count-changed: leave for the cold cycle (guarded path)
    return cancels, creates


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
        """The full guarded cycle — the quoter, verbatim — then rebuild hot ctx."""
        t0 = time.monotonic()
        try:
            M.run_once()
        except Exception as e:                        # cycle errors are loud, not fatal
            _log({"ev": "cold_cycle_error", "err": repr(e)[:200]})
        self.last_cycle_mono = time.monotonic()
        tickers = None
        if not os.path.exists(M.STOP_FILE) and self.client.mode != "dry_run":
            try:
                tickers = self.ctx.build(self.client, self._programs(), self.std_lock)
            except Exception as e:
                self.ctx.built_mono = 0.0             # ctx unusable -> hot disabled
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
        cancels, creates = hot_reprice_ops(q, standing_t, reduce_side=reduce_side)
        if not cancels and not creates:
            return "no_op"
        # DEPTH PRECONDITION (review F2-partial-fills): every order we are about
        # to touch must still be fully visible at its level in the mirror; a
        # shrunk level means "possibly just filled" -> full guarded cycle.
        cancel_set = set(cancels)
        for o in standing_t:
            if o["order_id"] not in cancel_set:
                continue
            book = mirror.yes if o["side"] == "yes" else mirror.no
            if book.get(round(float(o["price_dollars"]), 4), 0.0) < float(o["count"]) - 1e-9:
                self.ctx.built_mono = 0.0
                self.cycle_req.set()
                return "possible_fill"
        # NOTIONAL HEADROOM (review F5): repricing may not raise resting notional
        # beyond the capital headroom the cold guards evaluated.
        old_cost = sum(_order_cost(o["side"], o["price_dollars"], o["count"])
                       for o in standing_t if o["order_id"] in cancel_set)
        new_cost = sum(_order_cost(cr["side"], cr["price_dollars"], cr["count"])
                       for cr in creates)
        delta = new_cost - old_cost
        if delta > 0 and (delta > self.ctx.headroom_mkt.get(ticker, 0.0) + 1e-9
                          or delta > self.ctx.headroom_total + 1e-9):
            return "notional_cap"
        if not self.bucket.take(len(cancels) + len(creates)):
            return "budget"
        self.last_hot_mono[ticker] = time.monotonic()
        built_snapshot = self.ctx.built_mono
        self._writer.submit(self._exec_hot, ticker, cancels, creates, delta,
                            t_event_mono, built_snapshot)
        return "submitted"

    def _exec_hot(self, ticker, cancels, creates, delta_cost, t_event_mono, built_snapshot):
        """Write-worker half: re-validate, then execute. Runs OFF the event loop."""
        # RE-VALIDATION at execution time (review F6): the world may have moved
        # between submit and run — a cold cycle starting, STOP appearing, or the
        # ctx being invalidated all abort the write.
        if (self.stopping.is_set() or self.in_cold or os.path.exists(M.STOP_FILE)
                or self.ctx.built_mono != built_snapshot):
            _log({"ev": "hot_abort_stale", "ticker": ticker})
            return
        ok_c = ok_n = 0
        try:
            if cancels:
                res = self.client.batch_cancel(cancels)
                done = {c.get("order", {}).get("order_id") if isinstance(c, dict) else None
                        for c in (res.get("cancelled") or [])}
                failed = res.get("failed") or []
                ok_c = len(cancels) - len(failed)
                with self.std_lock:
                    kept = [o for o in (self.ctx.standing.get(ticker) or [])
                            if o["order_id"] not in set(cancels) or
                            any(f.get("order_id") == o["order_id"] for f in failed)]
                    self.ctx.standing[ticker] = kept
                if failed:
                    # CANCEL FAILURE (review F1/C7): the order may still rest OR
                    # may have just filled — either way our view is fiction.
                    # NO creates. Force the guarded cycle to reconcile.
                    self.ctx.built_mono = 0.0
                    self.cycle_req.set()
                    _log({"ev": "hot_cancel_fail", "ticker": ticker,
                          "failed": len(failed), "ok": ok_c})
                    return
            for cr in creates:
                self._coid += 1
                r = self.client.create_quote(
                    ticker, cr["side"], cr["price_dollars"], cr["count"],
                    client_order_id=f"ws-{time.time_ns()}-{self._coid}-{cr['side']}")
                # dual-shape response parse (review F4): nested or top-level
                o = r.get("order") if isinstance(r, dict) and isinstance(r.get("order"), dict) \
                    else (r if isinstance(r, dict) else {})
                oid = o.get("order_id")
                if not oid:
                    # the order MAY exist server-side — never invent a placeholder
                    # id; invalidate and let the cold standing read reconcile.
                    self.ctx.built_mono = 0.0
                    self.cycle_req.set()
                    _log({"ev": "hot_create_no_id", "ticker": ticker})
                    return
                with self.std_lock:
                    self.ctx.standing.setdefault(ticker, []).append(
                        {"order_id": oid, "side": cr["side"],
                         "price_dollars": cr["price_dollars"], "count": cr["count"]})
                ok_n += 1
            if delta_cost > 0:
                with self.std_lock:
                    self.ctx.headroom_total = max(0.0, self.ctx.headroom_total - delta_cost)
                    self.ctx.headroom_mkt[ticker] = max(
                        0.0, self.ctx.headroom_mkt.get(ticker, 0.0) - delta_cost)
        except Exception as e:
            _log({"ev": "hot_write_error", "ticker": ticker, "err": repr(e)[:160]})
            self.ctx.built_mono = 0.0                 # view now uncertain -> force cold rebuild
            self.cycle_req.set()
            return
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
                   "fills_unconfirmed", "stop_file", "cold_running", "notional_cap"):
            self.cycle_req.set()                      # fall back to a full guarded cycle

    def on_fill(self, msg):
        _log({"ev": "fill", "ticker": msg.get("market_ticker", ""),
              "count": msg.get("count")})
        self.ctx.built_mono = 0.0                     # inventory changed: ctx is stale NOW
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
        self.ctx.built_mono = 0.0
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
                triggered = self.cycle_req.is_set() and not stopped
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
