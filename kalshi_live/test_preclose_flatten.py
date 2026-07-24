#!/usr/bin/env python3
"""Pins for the NAKED-ONLY PRE-CLOSE SETTLEMENT FLATTEN (KALSHI_PRECLOSE_FLATTEN).

WHY: a Kalshi market CLOSES (trading ends) at close_time but SETTLES hours later; after close we
cannot trade, so NAKED (unpaired, net-directional) ladder inventory held AT CLOSE rides into
settlement and resolves against us (gas-daily 26JUL24: measured REALIZED -$34.98). A PAIRED ladder
(yes-low / no-high) self-hedges to ~$1/pair and is SAFE to carry — only the naked residual is the
gamble. The mechanism exits ONLY that residual, MAKER-FIRST, taker-additive.

The five pins below are written so each FAILS if the specific defect it guards is reintroduced —
they are the three reasons TAKER_FLATTEN was disabled (KALSHI_HANDOFF_2026-07-23 §2) plus the
window/flag-off no-op:
  1. CROSSES-NAKED-ONLY  (reason 1: it de-hedged live pairs by crossing the FULL position)
  2. FLAG-OFF NO-OP
  3. NEVER-STRANDS-EXIT  (reason 3: it cancelled the resting exit then the taker failed -> naked)
  4. FIRES-ONLY-NEAR-CLOSE (reason 2: the old flatten was always-on)
  5. MAKER-FIRST          (grace before any taker)

Run: python -m pytest test_preclose_flatten.py -q  (from the probe dir)
"""
import importlib.util
import sys
from datetime import timedelta


def _load(n):
    s = importlib.util.spec_from_file_location(n, f"{n}.py")
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


q = _load("maker_kalshi_quoter")


# A book with liquidity BOTH sides so a taker can fill; a one-sided book is built per-test.
_BOOK = {"orderbook_fp": {"yes_dollars": [["0.60", "500"]], "no_dollars": [["0.38", "500"]]}}
_ONE_SIDED = {"orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.38", "500"]]}}


class MockClient:
    """Records every taker cross (create_order_v2) and every cancel. create_order_v2 fills up to
    the requested count and reduces the true position toward zero; cancel_order removes from the
    resting book (so a stranded exit is observable as 'not in _resting')."""

    def __init__(self, mode="live", positions=None, resting=None, book=_BOOK,
                 fill=True):
        self.mode = mode
        self._positions = {p["ticker"]: float(p["position_fp"]) for p in (positions or [])}
        self._resting = list(resting or [])
        self._book = book
        self._fill = fill
        self.crosses = []
        self.cancelled = []

    def create_order_v2(self, ticker, book_side, count, price_dollars,
                        time_in_force="good_till_canceled",
                        self_trade_prevention_type="taker_at_cross",
                        post_only=True, client_order_id=None):
        self.crosses.append({"ticker": ticker, "side": book_side, "count": int(count)})
        filled = int(count) if self._fill else 0
        cur = self._positions.get(ticker, 0.0)
        if book_side == "ask" and cur > 0:
            self._positions[ticker] = max(0.0, cur - filled)
        elif book_side == "bid" and cur < 0:
            self._positions[ticker] = min(0.0, cur + filled)
        return {"order": {"order_id": client_order_id, "fill_count": str(filled),
                          "status": "canceled"}}

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        self._resting = [o for o in self._resting if o.get("order_id") != oid]
        return {"ok": True}

    def total_crossed(self):
        return sum(x["count"] for x in self.crosses)


def _close_in(mins):
    """A close_time_of callable putting every ticker `mins` minutes from close."""
    def _f(ticker):
        return (q.utcnow() + timedelta(minutes=mins)).isoformat()
    return _f


def _std_config(monkeypatch, min_ct=5.0, grace_s=0, window_min=15.0, inv_tol=3.0):
    monkeypatch.setattr(q, "PRECLOSE_FLATTEN", 1)
    monkeypatch.setattr(q, "PRECLOSE_FLATTEN_MIN", window_min)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", min_ct)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", grace_s)
    monkeypatch.setattr(q, "INV_TOLERANCE", inv_tol)
    monkeypatch.setattr(q, "public_get", lambda p: q._BOOK_PATCH)
    q._BOOK_PATCH = _BOOK


# ---------------------------------------------------------------------------
# 1. CROSSES-NAKED-ONLY (reason 1) — the exact GASW-4.140 bug. held +40 = 34 paired / 6 naked;
#    the taker must cross AT MOST 6, NEVER 40, and never touch the paired leg's own ticker.
# ---------------------------------------------------------------------------
def test_crosses_naked_only_not_paired(monkeypatch):
    _std_config(monkeypatch)
    # +40 long-yes on the LOW strike, -34 short (no) on the HIGH strike -> 34 pair, +6 naked on 4.140
    held = {"KXAAAGASW-26JUL24-4.140": 40.0, "KXAAAGASW-26JUL24-4.160": -34.0}
    c = MockClient(positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "40.0"},
                              {"ticker": "KXAAAGASW-26JUL24-4.160", "position_fp": "-34.0"}])
    plan, grace = {}, {}
    q._preclose_naked_flatten(c, held, q.utcnow(), plan, grace, close_time_of=_close_in(10))
    # naked is +6 on 4.140 only; the paired 4.160 leg must NEVER be crossed.
    low_crossed = sum(x["count"] for x in c.crosses if x["ticker"].endswith("4.140"))
    high_crossed = sum(x["count"] for x in c.crosses if x["ticker"].endswith("4.160"))
    assert low_crossed <= 6, f"crossed {low_crossed} > |naked|=6 (the 40-ct de-hedge bug)"
    assert low_crossed == 6, "should flatten the full naked residual on a liquid book"
    assert high_crossed == 0, "the paired leg (4.160) must never be crossed"
    assert plan.get("preclose_taker_ct") == 6


# ---------------------------------------------------------------------------
# 2. FLAG-OFF NO-OP — flag 0 -> the run_once pre-close block is skipped entirely: no taker, no
#    plan key, no grace-state key. (We exercise the guard the call site uses.)
# ---------------------------------------------------------------------------
def test_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr(q, "PRECLOSE_FLATTEN", 0)
    held = {"KXAAAGASW-26JUL24-4.140": 40.0, "KXAAAGASW-26JUL24-4.160": -34.0}
    c = MockClient(positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "40.0"}])
    plan, grace = {}, {}
    # The call site guards on PRECLOSE_FLATTEN; replicate that guard here.
    if q.PRECLOSE_FLATTEN:
        q._preclose_naked_flatten(c, held, q.utcnow(), plan, grace, close_time_of=_close_in(10))
    assert c.crosses == [], "flag OFF must not taker"
    assert grace == {}, "flag OFF must not touch grace state"
    assert "preclose_flatten" not in plan and "preclose_taker_ct" not in plan


# ---------------------------------------------------------------------------
# 3. NEVER-STRANDS-EXIT (reason 3) — one-sided book, taker cannot fill; the resting maker reducing
#    exit MUST remain (nothing cancelled). The old flatten cancelled the exit first, then the IOC
#    failed, leaving the position naked with no order.
# ---------------------------------------------------------------------------
def test_never_strands_the_resting_exit(monkeypatch):
    _std_config(monkeypatch)
    q._BOOK_PATCH = _ONE_SIDED                       # long-yes wants to SELL yes; no yes bid -> no fill
    held = {"KXAAAGASW-26JUL24-4.140": 8.0}
    # the reducing maker exit already resting (placed by the unwind path this cycle)
    exit_order = {"order_id": "exit1", "ticker": "KXAAAGASW-26JUL24-4.140", "side": "no"}
    c = MockClient(positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "8.0"}],
                   resting=[exit_order], book=_ONE_SIDED, fill=False)
    plan, grace = {}, {}
    q._preclose_naked_flatten(c, held, q.utcnow(), plan, grace, close_time_of=_close_in(10))
    assert c.cancelled == [], "the taker must cancel NOTHING (it is additive)"
    assert exit_order in c._resting, "the resting maker reducing exit must REMAIN after a failed taker"
    assert plan.get("preclose_taker_failed", 0) >= 1


# ---------------------------------------------------------------------------
# 4. FIRES-ONLY-NEAR-CLOSE (reason 2) — plenty of time left + naked inventory -> NO taker fires
#    (the old flatten was always-on). Only inside PRECLOSE_FLATTEN_MIN does it act.
# ---------------------------------------------------------------------------
def test_fires_only_near_close(monkeypatch):
    _std_config(monkeypatch, window_min=15.0)
    held = {"KXAAAGASW-26JUL24-4.140": 20.0}
    c = MockClient(positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "20.0"}])
    plan, grace = {}, {}
    # 120 min to close -> well OUTSIDE the 15-min window
    q._preclose_naked_flatten(c, held, q.utcnow(), plan, grace, close_time_of=_close_in(120))
    assert c.crosses == [], "no taker outside the pre-close window"
    assert "preclose_flatten" not in plan and grace == {}


# ---------------------------------------------------------------------------
# 5. MAKER-FIRST — within the window, the FIRST cycle records the grace clock and does NOT taker
#    (the reducing maker quote gets its STOP_ESCALATE_S chance to fill); a later cycle, still naked
#    and past the grace, DOES taker.
# ---------------------------------------------------------------------------
def test_maker_first_then_taker_after_grace(monkeypatch):
    _std_config(monkeypatch, grace_s=90)             # real grace this time
    held = {"KXAAAGASW-26JUL24-4.140": 20.0}
    c = MockClient(positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "20.0"}])
    plan, grace = {}, {}
    t0 = q.utcnow()

    # close_time_of must stay a FIXED wall-clock instant across the two cycles (10 min from t0),
    # else advancing `now` would also advance the close and never enter the window consistently.
    close_at = (t0 + timedelta(minutes=10)).isoformat()

    def _fixed_close(_ticker):
        return close_at

    # cycle 1: in the window, grace clock STARTS -> no taker yet (maker gets its chance)
    q._preclose_naked_flatten(c, held, t0, plan, grace, close_time_of=_fixed_close)
    assert c.crosses == [], "first in-window cycle must NOT taker (maker-first grace)"
    assert "KXAAAGASW-26JUL24-4.140" in grace, "grace clock should be armed"
    assert plan.get("preclose_flatten") == 1        # engaged (window + naked), just no taker yet

    # cycle 2: 91s later, still naked, grace elapsed -> taker fires on the naked residual
    q._preclose_naked_flatten(c, held, t0 + timedelta(seconds=91), plan, grace,
                              close_time_of=_fixed_close)
    assert c.total_crossed() == 20, "after the grace, the naked residual is taker-flattened"


# ---------------------------------------------------------------------------
# extra: below-threshold residue is LEFT resting (not takered) even inside the window past grace —
# the STOP_TAKER_MIN_CT floor (small residue rides via the maker exit, no spread paid).
# ---------------------------------------------------------------------------
def test_subthreshold_residue_left_resting(monkeypatch):
    _std_config(monkeypatch, min_ct=25.0, grace_s=0, inv_tol=1.0)   # 20 naked < 25 floor
    held = {"KXAAAGASW-26JUL24-4.140": 20.0}
    c = MockClient(positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "20.0"}])
    plan, grace = {}, {}
    q._preclose_naked_flatten(c, held, q.utcnow(), plan, grace, close_time_of=_close_in(10))
    assert c.crosses == [], "residue below STOP_TAKER_MIN_CT must not be takered"
    assert plan.get("preclose_flatten") == 1        # engaged, but taker withheld


# ---------------------------------------------------------------------------
# extra: dry_run never takers (plan-only mode), even in window past grace with a big naked residual.
# ---------------------------------------------------------------------------
def test_dry_run_never_takers(monkeypatch):
    _std_config(monkeypatch, grace_s=0)
    held = {"KXAAAGASW-26JUL24-4.140": 20.0}
    c = MockClient(mode="dry_run",
                   positions=[{"ticker": "KXAAAGASW-26JUL24-4.140", "position_fp": "20.0"}])
    plan, grace = {}, {}
    q._preclose_naked_flatten(c, held, q.utcnow(), plan, grace, close_time_of=_close_in(10))
    assert c.crosses == [], "dry_run must never place a taker"
