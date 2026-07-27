"""Pin tests for KALSHI_STANDDOWN (stand-down — open LESS when reward does not justify fill loss).

Problem: the bot has no "should I even be playing right now?" brain. It farms LIP mechanically, so
on a day when the flagship temp reward (~91% of reward income) is DARK it churns thin gas and every
gas fill is a small adverse-selection loss with no reward to cover it (measured ~-$9 on 07-23). Our
realized edge BEFORE rewards is NEGATIVE (fingerprint ~-$0.011/ct on GAS, worse on temp); rewards
are the ONLY thing that makes the book +EV. When a market's reward density is too thin to justify
the expected fill loss, the stand-down OPENS LESS there (sizes the accumulating side to the floor /
skips a thin activate) so a dead day costs ~$0 instead of bleeding.

MECHANISM: a per-market reward-density gate keyed on the R1-normalized LIP $/day already carried on
each footprint row (m['usd_day']), R3-discounted on a one-sided/void book. No extra API read.

Flag KALSHI_STANDDOWN default 0 = provable no-op (today's exact behavior, byte-for-byte; telemetry
emitted only when the flag is on). Run: python -m pytest test_standdown.py -q  (from the probe dir).

The four pins:
  T1 FLAG-OFF NO-OP        — flag 0 => identical quotes to the legacy path on a thin-reward regime,
                             usd_day ignored entirely, and a full cycle emits no standdown_* key.
  T2 THE FIX PIN           — a thin-reward regime (usd_day below the floor). Flag OFF opens FULL size
                             (would take the adverse fill); flag ON opens MIN size => materially less
                             fill exposure. FAILS on legacy (no gate), PASSES on the fix.
  T3 NEVER BLOCKS EXITS    — holding inventory, flag ON STILL rests the reducing (unwind) quote at
                             full |inv| size (de-risk is never blocked or down-sized).
  T4 DOES NOT FORFEIT GAS  — a normal reward-present gas book (reward clearly beats cost). Flag ON
                             keeps quoting at full size (no false stand-down); identical to flag OFF.
"""
from datetime import datetime, timezone

import pytest

# Reuse the live-hardening harness (module handle + run_once driver + mock client/config helpers).
from test_live_hardening import q, MockClient, _run, _cfg
from test_live_hardening import legacy_inventory_mode  # noqa: E402


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
# A deep external book, both sides >> target -> JOIN branch, symmetric, in-bounds, sum<1.
_YL = [["0.50", "9999"]]
_NL = [["0.49", "9999"]]


def _mkt(usd_day=None, target=1000):
    """A footprint-row market. usd_day omitted entirely when None (models a legacy row / a row
    without the reward number) so we can prove the flag-off path never reads it."""
    m = {"target": target, "end": "2099-01-01T00:00:00Z"}
    if usd_day is not None:
        m["usd_day"] = usd_day
    return m


def _std_cfg(monkeypatch, *, on, floor=20.0, void_mult=0.5):
    """Enable/disable the flag + fix the sizing dials so a full join = 100 ct and the floor = 2 ct
    (JOIN_SIZE monkeypatched at runtime bypasses the import-time INV_HARD clamp)."""
    monkeypatch.setattr(q, "STANDDOWN", 1 if on else 0)
    monkeypatch.setattr(q, "STANDDOWN_MIN_USD_DAY", floor)
    monkeypatch.setattr(q, "STANDDOWN_VOID_MULT", void_mult)
    monkeypatch.setattr(q, "INV_SOFT_CT", 30.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 80.0)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "JOIN_SIZE", 100)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 250.0)


def _sides(qs):
    return {x["side"]: x for x in qs}


# ---------------------------------------------------------------------------------------------
# T1 — FLAG-OFF NO-OP
# ---------------------------------------------------------------------------------------------
def test_flag_off_is_byte_for_byte_legacy_on_a_thin_regime(monkeypatch):
    _std_cfg(monkeypatch, on=False)
    # thin reward (usd_day=5, well below the floor) vs a row with NO usd_day at all: with the flag
    # OFF the two must be IDENTICAL — the stand-down code never reads usd_day, so it cannot change
    # the quotes. This is the flag-off no-op proof at the desired_quotes level.
    thin = q.desired_quotes(_mkt(usd_day=5.0), _YL, _NL, q.utcnow(), inv=0.0)
    legacy = q.desired_quotes(_mkt(usd_day=None), _YL, _NL, q.utcnow(), inv=0.0)
    assert thin == legacy
    sizes = {x["side"]: x["count"] for x in thin}
    assert sizes["yes"] == 100 and sizes["no"] == 100          # full-size join, NOT stood down


def test_flag_off_cycle_emits_no_standdown_telemetry(monkeypatch, tmp_path):
    # A full run_once with the flag off must not emit any standdown_* key (plan row byte-identical).
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "GAS", "usd_day": 5.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "standdown" not in row and "standdown_markets" not in row
    assert "gated_out" in row                                  # legacy telemetry intact
    assert len(c.created) == 2                                 # full-size book placed (unchanged)


# ---------------------------------------------------------------------------------------------
# T2 — THE FIX PIN (thin reward -> open MIN size)
# ---------------------------------------------------------------------------------------------
def test_fix_pin_thin_reward_opens_min_size(monkeypatch):
    # Flag OFF: legacy opens the full-size book (and would take the adverse fill at that size).
    _std_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(usd_day=5.0), _YL, _NL, q.utcnow(), inv=0.0))
    assert off["yes"]["count"] == 100 and off["no"]["count"] == 100      # full-size adverse fill
    # Flag ON: the same thin-reward book opens at the FLOOR only -> materially less fill exposure.
    _std_cfg(monkeypatch, on=True)
    on = _sides(q.desired_quotes(_mkt(usd_day=5.0), _YL, _NL, q.utcnow(), inv=0.0))
    assert on["yes"]["count"] == 2 and on["no"]["count"] == 2            # floor size only
    # price stays AT reference so the snapshot still qualifies + still earns the thin reward
    assert on["yes"]["price_dollars"] == 0.50 and on["no"]["price_dollars"] == 0.49
    assert on["yes"]["count"] < off["yes"]["count"]                      # strictly less than legacy
    assert on["no"]["count"] < off["no"]["count"]


def test_fix_pin_via_cycle_slashes_committed_capital(monkeypatch, tmp_path):
    def _cycle(on):
        _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
        monkeypatch.setattr(q, "STANDDOWN", 1 if on else 0)
        monkeypatch.setattr(q, "STANDDOWN_MIN_USD_DAY", 20.0)
        monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": "GAS", "usd_day": 5.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
        c = MockClient(mode="live", resting=[], positions=[])
        return _run(monkeypatch, c, str(tmp_path)), c

    off_row, off_c = _cycle(False)
    on_row, on_c = _cycle(True)
    assert "standdown" not in off_row                          # flag off: no telemetry
    assert on_row.get("standdown") == 1 and on_row.get("standdown_markets") == 1
    assert on_row.get("standdown_min_rho_usd_day") == 5.0      # the number that drove the stand-down
    # both sides still placed (market stays qualifying) but at a fraction of the notional
    assert len(off_c.created) == 2 and len(on_c.created) == 2
    assert on_row.get("est_capital_usd", 0) < off_row.get("est_capital_usd", 0)
    assert sum(o["count"] for o in on_c.created) <= 4          # 2 sides * MIN_QUOTE_CT
    assert sum(o["count"] for o in on_c.created) < sum(o["count"] for o in off_c.created)


# ---------------------------------------------------------------------------------------------
# T3 — NEVER BLOCKS EXITS
# ---------------------------------------------------------------------------------------------
def test_standdown_never_blocks_exits(monkeypatch):
    legacy_inventory_mode(monkeypatch)
    # Long +40 ct on a thin-reward market. Stand-down ON must STILL rest the reducing (NO) side at
    # full |inv| size to unwind — de-risk is never blocked or down-sized.
    _std_cfg(monkeypatch, on=True)
    on = _sides(q.desired_quotes(_mkt(usd_day=5.0), _YL, _NL, q.utcnow(), inv=40.0))
    assert "no" in on and on["no"]["reason"] == "unwind"
    # >= |inv| since 2026-07-26: the reducing half is ADD+|inv| (ADD=2 under stand-down -> 42),
    # so de-risk is still never blocked or down-sized — it is slightly LARGER, which is what
    # makes a double fill land exactly paired.
    assert on["no"]["count"] >= 40                             # covers |inv|, not floored by stand-down
    assert on["yes"]["count"] <= 2                             # the ACCUMULATING side is what shrinks
    assert 40 + on["yes"]["count"] - on["no"]["count"] == 0    # double fill lands PAIRED
    # Exit sizing is no longer IDENTICAL across the flag, by design: the reducing half depends
    # on the adding half, and stand-down shrinks the adding half. Both must still cover |inv|.
    _std_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(usd_day=5.0), _YL, _NL, q.utcnow(), inv=40.0))
    assert off["no"]["count"] >= 40 and on["no"]["count"] >= 40


def test_standdown_skips_thin_activate_but_never_a_held_exit(monkeypatch):
    _std_cfg(monkeypatch, on=True)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 100000.0)
    thin_y = [["0.50", "10"]]                                  # depth 10 << target 1000 -> void/activate
    thin_n = [["0.49", "10"]]
    # thin reward void: eff = 5 * 0.5 = 2.5 < 20 -> do NOT commit activate depth (skip entirely)
    assert q.desired_quotes(_mkt(usd_day=5.0, target=1000), thin_y, thin_n, q.utcnow(), inv=0.0) == []
    # rich reward void: eff = 150 * 0.5 = 75 >= 20 -> activate normally (non-empty)
    assert q.desired_quotes(_mkt(usd_day=150.0, target=1000), thin_y, thin_n, q.utcnow(), inv=0.0)
    # held inventory on the SAME thin void market still rests its reducing side (exit never gated)
    out = q.desired_quotes(_mkt(usd_day=5.0, target=1000), thin_y, thin_n, q.utcnow(), inv=50.0)
    assert len(out) == 1 and out[0]["reason"] == "unwind"


# ---------------------------------------------------------------------------------------------
# T4 — DOES NOT FORFEIT GAS ON A NORMAL DAY
# ---------------------------------------------------------------------------------------------
def test_standdown_does_not_forfeit_gas_on_a_normal_day(monkeypatch):
    # Live gas: usd_day 150 >> floor 20 -> reward clearly beats the fingerprint adverse cost, so NO
    # stand-down. Flag ON must keep quoting at full size (identical to flag OFF).
    _std_cfg(monkeypatch, on=True, floor=20.0)
    on = _sides(q.desired_quotes(_mkt(usd_day=150.0), _YL, _NL, q.utcnow(), inv=0.0))
    assert on["yes"]["count"] == 100 and on["no"]["count"] == 100        # full-size join preserved
    _std_cfg(monkeypatch, on=False)
    off = _sides(q.desired_quotes(_mkt(usd_day=150.0), _YL, _NL, q.utcnow(), inv=0.0))
    assert {s: x["count"] for s, x in on.items()} == {s: x["count"] for s, x in off.items()}


# ---------------------------------------------------------------------------------------------
# helper: R3 discount + floor semantics
# ---------------------------------------------------------------------------------------------
def test_standdown_market_helper_r3_and_floor(monkeypatch):
    monkeypatch.setattr(q, "STANDDOWN_MIN_USD_DAY", 20.0)
    monkeypatch.setattr(q, "STANDDOWN_VOID_MULT", 0.5)
    assert q._standdown_market({"usd_day": 5.0}, False)[0] is True       # 5 < 20 -> stand down
    assert q._standdown_market({"usd_day": 150.0}, False)[0] is False    # 150 >= 20 -> keep
    assert q._standdown_market({"usd_day": 50.0}, True)[0] is False      # 50*0.5 = 25 >= 20 -> keep
    assert q._standdown_market({"usd_day": 30.0}, True)[0] is True       # 30*0.5 = 15 < 20 -> stand down
    assert q._standdown_market({}, False)[0] is True                     # missing usd_day -> conservative
    # eff_rho is reported for telemetry
    assert q._standdown_market({"usd_day": 40.0}, True)[1] == 20.0       # 40 * 0.5
