#!/usr/bin/env python3
"""Pins for FIX 3 (operator-approved 2026-07-27): CROSS THE EXIT IF IT DOES NOT FILL.

WHY: the 07-27 live loss mechanism was a maker exit that rested (behind the touch, but the point
holds anywhere) and never filled while the market trended. The pre-close flatten only arms within
PRECLOSE_FLATTEN_MIN of market close; NOTHING bounded a strand's lifetime elsewhere, so 42 ct rode
from a -$0.59 touch exit to a -$15.29 settlement. _strand_cross bounds a naked residual in TIME:
after STRAND_CROSS_S seconds unfilled, one capped IOC pass per clock period fires at the touch.

Each pin fails if its specific guard is removed:
  1. NO-CROSS-BEFORE-THE-CLOCK   (a fresh strand only stamps the clock; nothing fires)
  2. CROSS-AFTER-THE-CLOCK       (expired clock + material naked -> capped IOC, cancel-first)
  3. FRESH-READ-CAPS-THE-CROSS   (position shrunk since the cycle snapshot -> cross the FRESH size,
                                  never the snapshot; a fill in flight must not be re-crossed)
  4. ONE-PASS-PER-PERIOD         (residual re-arms the clock -> no back-to-back book-walking; the
                                  07-27 STOP escalation walked DXY 0.52 -> 0.25 in 4 chained IOCs)
  5. DRY-RUN / TAKER_FLATTEN=0   (clock + telemetry only, never a taker)
  6. SUB-THRESHOLD LEFT RESTING  (below STOP_TAKER_MIN_CT the maker exit is the only exit)
  7. FLAT-CLEARS-THE-CLOCK       (a ticker that unwound passively forgets its stamp)
  8. BLIND-READ-FAILS-CLOSED     (positions unreadable inside the pass -> no cross)

Run: python -m pytest test_strand_cross.py -q  (from the probe dir)
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

_BOOK = {"orderbook_fp": {"yes_dollars": [["0.60", "500"]], "no_dollars": [["0.38", "500"]]}}


class MockClient:
    def __init__(self, mode="live", positions=None, resting=None, fill=True,
                 positions_raise=False):
        self.mode = mode
        self._positions = {p["ticker"]: float(p["position_fp"]) for p in (positions or [])}
        self._resting = list(resting or [])
        self._fill = fill
        self._positions_raise = positions_raise
        self.crosses = []
        self.cancelled = []
        self.created = []

    def get_positions(self):
        if self._positions_raise:
            raise RuntimeError("positions read 500")
        return {"market_positions": [
            {"ticker": t, "position_fp": str(p), "market_exposure_dollars": "1.0"}
            for t, p in self._positions.items()]}

    # _held_cost consumes the paginated shape in some builds; provide both spellings.
    def _get_paginated(self, path, key, params=None):
        return {key: self.get_positions()["market_positions"]}

    def get_orders(self, status="resting"):
        return {"orders": list(self._resting)}

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        self._resting = [o for o in self._resting if o.get("order_id") != oid]
        return {"ok": True}

    def create_quote(self, ticker, side, price, count, post_only=True, client_order_id=None):
        self.created.append({"ticker": ticker, "side": side, "price": price, "count": count})
        return {"order": {"order_id": client_order_id}}

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

    def total_crossed(self):
        return sum(x["count"] for x in self.crosses)


def _cfg(monkeypatch, strand_s=30.0, min_ct=5.0, inv_tol=3.0, taker=True):
    monkeypatch.setattr(q, "STRAND_CROSS_S", strand_s)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", min_ct)
    monkeypatch.setattr(q, "INV_TOLERANCE", inv_tol)
    monkeypatch.setattr(q, "TAKER_FLATTEN", taker)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)


def _held(client):
    """Match _held_cost's (total, by, costs) contract off the mock's positions."""
    by = dict(client._positions)
    return sum(abs(v) for v in by.values()), by, {t: 0.5 for t in by}


def _expired(now, s=60):
    return (now - timedelta(seconds=s)).isoformat()


# ---- 1. NO-CROSS-BEFORE-THE-CLOCK -------------------------------------------------------------

def test_fresh_strand_only_stamps_the_clock(monkeypatch):
    _cfg(monkeypatch)
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "20.0"}])
    monkeypatch.setattr(q, "_held_cost", _held)
    plan, state = {}, {}
    q._strand_cross(c, {"T1": 20.0}, {}, q.utcnow(), plan, state)
    assert c.crosses == [] and c.cancelled == []
    assert "T1" in state, "first sighting must stamp the clock"
    assert "strand_due" not in plan


# ---- 2. CROSS-AFTER-THE-CLOCK -----------------------------------------------------------------

def test_expired_clock_crosses_capped_and_cancel_first(monkeypatch):
    _cfg(monkeypatch)
    exit_order = {"order_id": "x1", "ticker": "T1", "side": "no"}
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "20.0"}], resting=[exit_order])
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert "x1" in c.cancelled, "fix 4 ordering: the resting exit is cancelled BEFORE the cross"
    assert c.total_crossed() <= 20, "capped at |naked| — never more"
    assert c.total_crossed() >= 1 and plan.get("strand_crossed_ct", 0) >= 1
    assert "T1" not in state, "confirmed flat clears the clock"


# ---- 3. FRESH-READ-CAPS-THE-CROSS -------------------------------------------------------------

def test_cross_is_capped_by_the_fresh_read_not_the_snapshot(monkeypatch):
    _cfg(monkeypatch)
    # cycle snapshot says 20; the venue (fresh read) says 8 — a fill landed in between.
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "8.0"}])
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert c.total_crossed() <= 8, "the FRESH position bounds the cross, never the cycle snapshot"


def test_fresh_read_below_threshold_stands_down(monkeypatch):
    _cfg(monkeypatch, min_ct=5.0)
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "2.0"}])   # venue: nearly unwound
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert c.crosses == [], "reduced below the taker threshold since the snapshot -> no cross"
    assert "T1" not in state


# ---- 4. ONE-PASS-PER-PERIOD -------------------------------------------------------------------

def test_residual_rearms_the_clock_no_backtoback_walking(monkeypatch):
    _cfg(monkeypatch)
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "20.0"}], fill=False)  # IOC no-fills
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    first_passes = len(c.crosses)
    assert first_passes <= 1, "tries=1: ONE touch-hit per firing, never a chained walk"
    assert state.get("T1") == now.isoformat(), "residual re-arms the clock (paces the next pass)"
    # immediately after, the clock is fresh -> a second call the same instant must NOT cross again
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert len(c.crosses) == first_passes, "no second pass inside the same period"


# ---- 5. DRY-RUN / TAKER_FLATTEN=0 -------------------------------------------------------------

def test_dry_run_never_takers(monkeypatch):
    _cfg(monkeypatch)
    c = MockClient(mode="dry_run", positions=[{"ticker": "T1", "position_fp": "20.0"}])
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert c.crosses == [] and c.cancelled == []
    assert plan.get("strand_due") == 1, "telemetry still reports what WOULD fire"


def test_taker_flatten_off_never_takers(monkeypatch):
    _cfg(monkeypatch, taker=False)
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "20.0"}])
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert c.crosses == [] and c.cancelled == []
    assert plan.get("strand_due") == 1


# ---- 6. SUB-THRESHOLD LEFT RESTING ------------------------------------------------------------

def test_subthreshold_strand_keeps_its_maker_exit_only(monkeypatch):
    _cfg(monkeypatch, min_ct=5.0, inv_tol=3.0)
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "4.0"}])
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 4.0}, {}, now, plan, state)
    assert c.crosses == [], "below STOP_TAKER_MIN_CT the maker exit is the only exit"
    assert "T1" in state, "still material vs INV_TOLERANCE -> the clock keeps running"


# ---- 7. FLAT-CLEARS-THE-CLOCK -----------------------------------------------------------------

def test_flat_ticker_forgets_its_stamp(monkeypatch):
    _cfg(monkeypatch)
    c = MockClient()
    monkeypatch.setattr(q, "_held_cost", _held)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 0.0}, {}, now, plan, state)   # unwound passively
    assert state == {}, "flat -> clock cleared (a re-strand later starts a FRESH clock)"
    assert c.crosses == []


# ---- 8. BLIND-READ-FAILS-CLOSED ---------------------------------------------------------------

def test_blind_fresh_read_never_crosses(monkeypatch):
    _cfg(monkeypatch)
    c = MockClient(positions=[{"ticker": "T1", "position_fp": "20.0"}], positions_raise=True)

    def _held_raises(client):
        raise RuntimeError("positions read 500")
    monkeypatch.setattr(q, "_held_cost", _held_raises)
    now = q.utcnow()
    plan, state = {}, {"T1": _expired(now)}
    q._strand_cross(c, {"T1": 20.0}, {}, now, plan, state)
    assert c.crosses == [] and plan.get("strand_read_failed") == 1
