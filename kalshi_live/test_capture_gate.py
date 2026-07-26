"""Pin tests for KALSHI_CAPTURE_GATE (capture gate — auto-skip/reduce markets where OUR prospective
R4 reward capture is below a floor).

Problem the existing gates miss: the unqualifiable gate (desired_quotes) and the selection gate both
ask "can ANYONE earn here?" — is the BOOK two-sided at Target Size. Neither asks "can WE earn here?".
LIP reward is pro-rata by DF^N-weighted qualifying score, so against a DEEP rival book our own
resting size is a rounding error -> our reward ~= $0 while we still carry adverse-fill risk. Measured
live: KXTRUMPENDORSEMENTS-...-A15 holds capital with a two-sided book but our R4 qualifying share is
~0; saturated pools (KXFUNDRAISING) fill Target with rival depth. The old gates PASS both; the
capture gate SKIPS both.

MECHANISM: on a two-sided JOIN book, compute our PROSPECTIVE snapshot share if we rested our intended
join size AT reference (our_size scored at ref / (book qualifying score + our_size), R3 two-sided) x
the R1 pool (m['usd_day'] already on the footprint row). Below CAPTURE_MIN_USD_DAY the market is POOR
FOR US -> skip when FLAT, reduce-only when HOLDING. No extra API read (uses the in-cycle book).

Flag KALSHI_CAPTURE_GATE default 0 = provable no-op (today's exact behavior, byte-for-byte; telemetry
emitted only when the flag is on). Run: python -m pytest test_capture_gate.py -q (from the probe dir).

The four pins:
  T1 FLAG-OFF NO-OP        — flag 0 => identical quotes to the legacy path on a poor (deep-rival)
                             market; a full cycle emits no capture_* key.
  T2 THE FIX PIN           — a poor market (two-sided book, our prospective share ~0). Flag OFF opens
                             it full-size; flag ON SKIPS it (flat). FAILS on legacy, PASSES on fix.
  T3 NEVER BLOCKS EXITS    — holding inventory in a poor market, flag ON STILL rests the reducing
                             (unwind) quote at full |inv| size (de-risk never blocked/down-sized).
  T4 DOES NOT STARVE GAS   — a normal near-money gas book (share x pool >> floor). Flag ON keeps
                             quoting it full-size; identical to flag OFF.
Plus: _qualifying_score is pinned byte-equivalent to kalshi_market_scorecard.qualifying_share.
"""
from datetime import datetime, timezone

import pytest

# Reuse the live-hardening harness (module handle + run_once driver + mock client/config helpers).
from test_live_hardening import q, MockClient, _run, _cfg
import kalshi_market_scorecard as scorecard


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
# POOR market: two-sided JOIN book (both sides >> target -> not void, passes unqualifiable +
# selection), but a DEEP rival book (100k each side) so our 100-ct join is a rounding error ->
# prospective share ~0.001, capture = 50 * ~0.001 ~= $0.05/day << $5 floor. This is the Trump-A15
# / KXFUNDRAISING shape: the book can pay, but not US.
_YL_POOR = [["0.50", "100000"]]
_NL_POOR = [["0.49", "100000"]]
# GAS market: near-money SHALLOW qualifying set (600 at the touch, 500 one tick back = 1100 total,
# just clears target 1000). Our 100-ct join scores a real fraction (~0.10 share) of a thin book,
# so capture = 150 * ~0.105 ~= $15.8/day >> $5 floor -> the +EV gas lane is NOT starved.
_YL_GAS = [["0.50", "600"], ["0.49", "500"]]
_NL_GAS = [["0.49", "600"], ["0.48", "500"]]


def _mkt(usd_day, target=1000, df=None):
    m = {"ticker": "T", "target": target, "end": "2099-01-01T00:00:00Z", "usd_day": usd_day}
    if df is not None:
        m["df"] = df
    return m


def _cap_cfg(monkeypatch, *, on, floor=5.0):
    """Enable/disable the flag + fix the sizing dials so a full join = 100 ct."""
    monkeypatch.setattr(q, "CAPTURE_GATE", 1 if on else 0)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", floor)
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    monkeypatch.setattr(q, "STANDDOWN", 0)                 # isolate this gate
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "JOIN_SIZE", 100)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)


def _sides(qs):
    return {x["side"]: x for x in qs}


def _pg(book):
    """public_get stub: empty programs, the given orderbook for any market fetch."""
    def inner(path):
        if "incentive" in path:
            return {"incentive_programs": [], "next_cursor": ""}
        return {"orderbook_fp": book}
    return inner


# ---------------------------------------------------------------------------------------------
# T1 — FLAG-OFF NO-OP
# ---------------------------------------------------------------------------------------------
def test_flag_off_is_byte_for_byte_legacy_on_a_poor_market(monkeypatch):
    # Flag OFF: the capture gate never runs, so a poor (deep-rival) book opens the FULL two-sided
    # join exactly as legacy — df on the row is ignored entirely. This is the no-op proof at the
    # desired_quotes level.
    _cap_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=0.0))
    assert off["yes"]["count"] == 100 and off["no"]["count"] == 100      # full-size join, NOT skipped
    assert off["yes"]["price_dollars"] == 0.50 and off["no"]["price_dollars"] == 0.49
    # presence/absence of the df key must not change the flag-off result (inert additive key)
    with_df = q.desired_quotes(_mkt(usd_day=50.0, df=0.5), _YL_POOR, _NL_POOR, q.utcnow(), inv=0.0)
    without_df = q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=0.0)
    assert with_df == without_df


def test_flag_off_cycle_emits_no_capture_telemetry(monkeypatch, tmp_path):
    # A full run_once with the flag off must not emit any capture_* key (plan row byte-identical).
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "CAPTURE_GATE", 0)
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL_POOR, "no_dollars": _NL_POOR}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "POOR", "usd_day": 50.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "capture_gate" not in row and "capture_skipped_markets" not in row
    assert "gated_out" in row                                  # legacy telemetry intact
    assert len(c.created) == 2                                 # full-size book placed (unchanged)


# ---------------------------------------------------------------------------------------------
# T2 — THE FIX PIN (poor market: flag OFF opens it, flag ON skips it)
# ---------------------------------------------------------------------------------------------
def test_fix_pin_poor_market_flat(monkeypatch):
    # Flag OFF: legacy opens the full two-sided book (and would take the adverse fill for $0 reward).
    _cap_cfg(monkeypatch, on=False)
    off = q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=0.0)
    assert len(off) == 2 and _sides(off)["yes"]["count"] == 100     # opens, pure adverse-fill risk
    # Flag ON: same poor book -> our prospective capture is far below the floor -> SKIP entirely.
    _cap_cfg(monkeypatch, on=True)
    on = q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=0.0)
    assert on == []                                             # skipped when flat + poor-for-us


def test_fix_pin_via_cycle_skips_and_reports(monkeypatch, tmp_path):
    def _cycle(on):
        _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
        monkeypatch.setattr(q, "CAPTURE_GATE", 1 if on else 0)
        monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 5.0)
        monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
        monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL_POOR, "no_dollars": _NL_POOR}))
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": "POOR", "usd_day": 50.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])
        c = MockClient(mode="live", resting=[], positions=[])
        return _run(monkeypatch, c, str(tmp_path)), c

    off_row, off_c = _cycle(False)
    on_row, on_c = _cycle(True)
    assert "capture_gate" not in off_row                       # flag off: no telemetry
    assert len(off_c.created) == 2                             # legacy opens the poor market
    assert on_row.get("capture_gate") == 1
    assert on_row.get("capture_skipped_markets") == 1
    assert on_row.get("capture_floor_usd_day") == 5.0
    assert on_row.get("capture_min_pc_usd_day", 99.0) < 5.0    # the thin capture that tripped it
    assert len(on_c.created) == 0                              # poor market NOT opened under the gate
    assert on_row.get("quoted_markets") == 0


# ---------------------------------------------------------------------------------------------
# T3 — NEVER BLOCKS EXITS
# ---------------------------------------------------------------------------------------------
def test_capture_gate_never_blocks_exits(monkeypatch):
    # Long +40 ct on a POOR market. Capture gate ON must go REDUCE-ONLY: rest ONLY the reducing (NO)
    # side at full |inv| to unwind — de-risk is never blocked or down-sized.
    _cap_cfg(monkeypatch, on=True)
    out = q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=40.0)
    assert len(out) == 1                                       # reduce-only: reducing side only
    assert out[0]["side"] == "no" and out[0]["reason"] == "unwind"
    assert out[0]["count"] == 40                              # full |inv|, NOT down-sized by the gate
    # symmetry: short -40 ct -> reducing YES side at full |inv|
    out_short = q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=-40.0)
    assert len(out_short) == 1
    assert out_short[0]["side"] == "yes" and out_short[0]["reason"] == "unwind"
    assert out_short[0]["count"] == 40


def test_capture_gate_exit_matches_legacy_unwind_size(monkeypatch):
    # The reduce-only quote the gate rests must be sized identically to the legacy maker unwind
    # (flag OFF) on the SAME held position — the gate clones the proven reduce-only path, it does
    # not invent a smaller exit.
    _cap_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=40.0))
    _cap_cfg(monkeypatch, on=True)
    on = _sides(q.desired_quotes(_mkt(usd_day=50.0), _YL_POOR, _NL_POOR, q.utcnow(), inv=40.0))
    # INVARIANT CHANGED 2026-07-26 (hold-both-sides): the GATE path is a PURE unwind — it has
    # stopped quoting and only leaves, so it stays capped at |inv|. The flag-OFF path is a
    # TWO-SIDED quote whose reducing half is now ADD+|inv| so a double fill lands paired.
    # They are no longer equal by design. What must still hold is the safety property this
    # test was written for: the gate never invents a SMALLER exit than the position.
    assert on["no"]["count"] >= 40                           # covers |inv|, not down-sized
    assert off["no"]["count"] >= on["no"]["count"]           # two-sided half is the larger one
    assert on["no"]["price_dollars"] == off["no"]["price_dollars"]


# ---------------------------------------------------------------------------------------------
# T4 — DOES NOT STARVE GAS ON A NORMAL DAY
# ---------------------------------------------------------------------------------------------
def test_capture_gate_does_not_starve_gas(monkeypatch):
    # Near-money gas: our 100-ct join is a real ~10% share of a shallow qualifying set, so
    # capture = 150 * ~0.105 ~= $15.8/day >> $5 floor -> NO skip. Flag ON keeps quoting full size
    # (identical to flag OFF).
    _cap_cfg(monkeypatch, on=True, floor=5.0)
    on = _sides(q.desired_quotes(_mkt(usd_day=150.0), _YL_GAS, _NL_GAS, q.utcnow(), inv=0.0))
    assert on["yes"]["count"] == 100 and on["no"]["count"] == 100   # full-size join preserved
    _cap_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(usd_day=150.0), _YL_GAS, _NL_GAS, q.utcnow(), inv=0.0))
    assert {s: x["count"] for s, x in on.items()} == {s: x["count"] for s, x in off.items()}


def test_capture_gate_gas_via_cycle_keeps_quoting(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "CAPTURE_GATE", 1)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 5.0)
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    monkeypatch.setattr(q, "public_get", _pg({"yes_dollars": _YL_GAS, "no_dollars": _NL_GAS}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "GAS", "usd_day": 150.0, "target": 1000, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("capture_gate") == 1
    assert row.get("capture_skipped_markets") == 0            # gas NOT stood down
    assert "capture_min_pc_usd_day" not in row               # nothing tripped the floor
    assert len(c.created) == 2                                # full two-sided book placed


# ---------------------------------------------------------------------------------------------
# helper equivalence + prospective-share math
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bids,our_price,our_size,target,df", [
    ([(0.50, 600.0), (0.49, 500.0)], 0.50, 100.0, 1000, 0.5),   # gas: shallow qualifying set
    ([(0.50, 100000.0)], 0.50, 100.0, 1000, 0.5),               # poor: deep single level
    ([(0.60, 200.0), (0.55, 100.0)], None, 0.0, 1000, 0.5),     # book can't reach target
    ([(0.40, 800.0), (0.38, 400.0)], 0.38, 50.0, 1000, 0.5),    # our order at the qualifying floor
    ([], None, 0.0, 1000, 0.5),                                 # empty book
])
def test_qualifying_score_matches_scorecard(bids, our_price, our_size, target, df):
    # The local replica MUST be byte-equivalent to kalshi_market_scorecard.qualifying_share (the
    # reuse the design promises); the local copy exists only to keep the ledger's auth/env import
    # out of the live quoter's graph, not to diverge.
    assert q._qualifying_score(bids, our_price, our_size, target, df) == \
        scorecard.qualifying_share(bids, our_price, our_size, target, df)


def test_prospective_capture_poor_below_gas_above(monkeypatch):
    monkeypatch.setattr(q, "JOIN_SIZE", 100)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    yl_poor = [(0.50, 100000.0)]
    nl_poor = [(0.49, 100000.0)]
    pc_poor = q._prospective_capture(_mkt(usd_day=50.0), yl_poor, nl_poor, 0.50, 0.49, 1000)
    assert pc_poor < 5.0                                       # poor market: below the floor
    yl_gas = [(0.50, 600.0), (0.49, 500.0)]
    nl_gas = [(0.49, 600.0), (0.48, 500.0)]
    pc_gas = q._prospective_capture(_mkt(usd_day=150.0), yl_gas, nl_gas, 0.50, 0.49, 1000)
    assert pc_gas > 5.0                                        # gas market: above the floor
    # a side that can't reach target -> 0 capture (R3 two-sided requirement)
    pc_onesided = q._prospective_capture(_mkt(usd_day=150.0), yl_gas, [(0.49, 10.0)], 0.50, 0.49, 1000)
    assert pc_onesided == 0.0
