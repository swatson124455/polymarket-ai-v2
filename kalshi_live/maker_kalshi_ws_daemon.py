#!/usr/bin/env python3
"""KALSHI WS MAKER DAEMON — event-driven requoting on top of the UNCHANGED quoter.

Replaces the 2-minute systemd timer with a long-lived process. Additive-only:
maker_kalshi_quoter.py is imported, NEVER modified — every guard (loss meter,
breakers, ladder pairing, caps, STOP, netev/capture gates) runs verbatim.

TWO STAGES:

  STAGE A (always on): WS-TRIGGERED FULL CYCLES.
    The Kalshi orderbook websocket watches the tickers we quote. When a book
    genuinely moves (best changed >= 1 tick) — or one of OUR fills arrives —
    the daemon immediately runs M.run_once(): the complete guarded cycle,
    byte-identical logic to the timer. A steady heartbeat cycle runs every
    WS_COLD_S regardless. Reaction time ~= run_once REST latency (~2-4s),
    down from a 0-120s timer wait. ZERO new order logic.

  STAGE B (KALSHI_WS_HOT=1, DEFAULT 0 — DO NOT ARM WITHOUT ADVERSARIAL
  REVIEW): SURGICAL ~200ms REPRICE.
    Between cycles, on a debounced book move, recompute desired_quotes() for
    just that ticker from the WS mirror and REPRICE-ONLY:
      - cancel+create the SAME side with the SAME count at the new price, or
      - cancel-only (side must be pulled).
    A create without a matching same-side cancel is FORBIDDEN in the hot
    path, so gross committed capital can only fall or stay — expansion always
    waits for the fully-guarded cold cycle. Preconditions before ANY hot
    write (all must hold, else the event silently falls through to Stage A):
      fresh cold cycle (< WS_STALE_S) · mirror clean (no seq gap) · no STOP ·
      no foreign writer on the plan file (another quoter instance active) ·
      hot context built · token bucket has budget · client is not dry_run.

SAFETY INHERITED, NOT REIMPLEMENTED: client defaults dry_run; live mode
requires KALSHI_LIVE_ARMED; all quotes post-only (never cross); run_once
holds the flock so a stray timer cycle can never overlap a daemon cycle.
DEPLOY NOTE: disable the 2-min timer when enabling this service —
  sudo systemctl disable --now polymarket-maker-kalshi-live.timer
(see deploy_ws_daemon.md). The daemon is NOT deployed by default; its
systemd service does not exist until an operator creates it.
"""
import asyncio
import glob
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as M                      # noqa: E402  (UNCHANGED quoter)
from kalshi_ws_feed import Feed                      # noqa: E402

# ---- knobs (env-overridable; defaults are the safe ones) ----
WS_COLD_S = M._envi("KALSHI_WS_COLD_S", 60)           # heartbeat full cycle
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
    """(path, size) of today's plan file — foreign-writer tripwire."""
    path = os.path.join(M.DATA_DIR, f"plans-{M.utcnow().strftime('%Y%m%d')}.jsonl")
    try:
        return path, os.path.getsize(path)
    except OSError:
        return path, 0


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
    Everything the hot path needs to call desired_quotes() honestly — plus the
    breaker recomputed the same way run_once computes it. stale() => unusable."""

    def __init__(self):
        self.built_mono = 0.0
        self.by_ticker = {}                           # t -> dict(m, own, inv, ev, cost)
        self.standing = {}                            # t -> [order dicts] (live view)
        self.breaker = False
        self.plan_sig = ("", 0)

    def stale(self):
        return (time.monotonic() - self.built_mono) > WS_STALE_S

    def build(self, client, programs):
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
        self.breaker = (risk - min(h[1] for h in hist) > M.BREAKER_HELD_GROWTH_USD
                        or risk > M.HELD_MAX_USD)
        self.by_ticker = {
            m["ticker"]: {"m": m, "own": own.get(m["ticker"]),
                          "inv": naked_by.get(m["ticker"], 0.0),
                          "ev": M.event_delta_for(ev_delta, m["ticker"]),
                          "cost": cost_by.get(m["ticker"], 0.0)}
            for m in footprint}
        self.standing = standing
        self.plan_sig = _plan_sig()
        self.built_mono = time.monotonic()
        return set(self.by_ticker) | set(standing)


def hot_reprice_ops(desired_t, standing_t):
    """REPRICE-ONLY diff for one ticker.
    Returns (cancels:[order_id], creates:[{side,price_dollars,count}]).
    Rules: same-side same-count price change -> cancel+create; standing side
    absent from desired -> cancel-only; desired side absent from standing or
    count mismatch -> NO ACTION (expansion/resize is cold-cycle work)."""
    cancels, creates = [], []
    by_side_std = {}
    for o in standing_t or []:
        by_side_std.setdefault(o["side"], []).append(o)
    by_side_des = {}
    for q in desired_t or []:
        by_side_des.setdefault(q["side"], []).append(q)
    for side, std in by_side_std.items():
        des = by_side_des.get(side, [])
        if not des:
            cancels.extend(o["order_id"] for o in std)     # pull the side (shrink-only)
            continue
        if len(std) == 1 and len(des) == 1:
            o, q = std[0], des[0]
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
        self.hot_disabled_reason = None

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
                tickers = self.ctx.build(self.client, self._programs())
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
        if os.path.exists(M.STOP_FILE):
            return "stop_file"
        if self.ctx.stale():
            return "ctx_stale"
        if mirror.dirty:
            return "mirror_dirty"
        if ticker not in self.ctx.by_ticker:
            return "not_in_ctx"
        p, sz = _plan_sig()
        if (p, sz) != self.ctx.plan_sig:
            self.hot_disabled_reason = "foreign_writer"
            return "foreign_writer"                   # another quoter ran: ctx is fiction
        mono = time.monotonic()
        if (mono - self.last_hot_mono.get(ticker, 0.0)) * 1000 < WS_HOT_DEBOUNCE_MS:
            return "debounce"
        return None

    def hot_event(self, ticker, mirror, t_event_mono):
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
        if self.ctx.breaker:                          # reduce-only: keep only unwind quotes
            q = [x for x in q if x.get("reason") == "unwind"]
        cancels, creates = hot_reprice_ops(q, self.ctx.standing.get(ticker))
        if not cancels and not creates:
            return "no_op"
        if not self.bucket.take(len(cancels) + len(creates)):
            return "budget"
        self.last_hot_mono[ticker] = time.monotonic()
        ok_c = ok_n = 0
        try:
            if cancels:
                self.client.batch_cancel(cancels)
                ok_c = len(cancels)
                std = [o for o in self.ctx.standing.get(ticker, [])
                       if o["order_id"] not in set(cancels)]
                self.ctx.standing[ticker] = std
            for i, cr in enumerate(creates):
                r = self.client.create_quote(
                    ticker, cr["side"], cr["price_dollars"], cr["count"],
                    client_order_id=f"ws-{int(time.time())}-{i}-{cr['side']}")
                oid = ((r.get("order") or {}).get("order_id")) if isinstance(r, dict) else None
                self.ctx.standing.setdefault(ticker, []).append(
                    {"order_id": oid or f"ws-unknown-{i}", "side": cr["side"],
                     "price_dollars": cr["price_dollars"], "count": cr["count"]})
                ok_n += 1
        except Exception as e:
            _log({"ev": "hot_write_error", "ticker": ticker, "err": repr(e)[:160]})
            self.ctx.built_mono = 0.0                 # view now uncertain -> force cold rebuild
            self.cycle_req.set()
            return "write_error"
        finally:
            self.ctx.plan_sig = _plan_sig()           # our own writes never trip the tripwire
        ms = round((time.monotonic() - t_event_mono) * 1000)
        _log({"ev": "hot_reprice", "ticker": ticker, "cancels": ok_c, "creates": ok_n,
              "reaction_ms": ms})
        return "acted"

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
                   "quote_error", "budget", "foreign_writer", "dry_run"):
            self.cycle_req.set()                      # fall back to a full guarded cycle

    def on_fill(self, msg):
        _log({"ev": "fill", "ticker": msg.get("market_ticker", ""),
              "count": msg.get("count")})
        self.ctx.built_mono = 0.0                     # inventory changed: ctx is stale NOW
        self.cycle_req.set()                          # full guard pass immediately

    async def main(self):
        _log({"ev": "daemon_start", "mode": self.client.mode, "hot": bool(WS_HOT),
              "cold_s": WS_COLD_S})
        tickers = self.cold_cycle() or set()
        watch = sorted(tickers)[:40] or ["KXB200MON-26JUL31-6.960"]
        feed = Feed(watch, on_book=self.on_book, on_fill=self.on_fill,
                    want_fills=self.client.mode != "dry_run")
        stop_ev = asyncio.Event()
        feed_task = asyncio.create_task(feed.run(stop_event=stop_ev))
        try:
            while not self.stopping.is_set():
                triggered = self.cycle_req.is_set()
                due = (time.monotonic() - self.last_cycle_mono) >= WS_COLD_S
                gap_ok = (time.monotonic() - self.last_cycle_mono) >= WS_CYCLE_MIN_GAP_S
                if (triggered and gap_ok) or due:
                    self.cycle_req.clear()
                    new = await asyncio.to_thread(self.cold_cycle)
                    if new and sorted(new)[:40] != watch:
                        watch = sorted(new)[:40]      # footprint changed: resubscribe
                        stop_ev.set()
                        await feed_task
                        stop_ev = asyncio.Event()
                        feed = Feed(watch, on_book=self.on_book, on_fill=self.on_fill,
                                    want_fills=self.client.mode != "dry_run")
                        feed_task = asyncio.create_task(feed.run(stop_event=stop_ev))
                await asyncio.sleep(0.2)
        finally:
            stop_ev.set()
            try:
                await asyncio.wait_for(feed_task, timeout=10)
            except (asyncio.TimeoutError, Exception):
                feed_task.cancel()
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
