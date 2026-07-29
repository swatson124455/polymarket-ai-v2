#!/usr/bin/env python3
"""DEFAULT-CONFIG pins for the 2026-07-27 operator risk rule (fixes 1+2).

Operator's rule (2026-07-27 19:48:56Z, normalized): flat => quote both sides or nothing;
holding => exit only; we can sell at a loss; one-sided is legal ONLY as an exit.

The 07-27 EOD handoff flagged that 18 tests were re-pointed at legacy_inventory_mode and the
DEFAULT configuration was left thinly covered — "A test_exit_only.py asserting the new default
does not exist yet and must be written." This is that file. Nothing here touches the flags:
every pin runs the code exactly as it ships, so a silent default flip fails loudly.

Run: python -m pytest test_exit_only.py -q  (from the probe dir)
"""
import importlib.util
import sys


def _load(n):
    s = importlib.util.spec_from_file_location(n, f"{n}.py")
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


q = _load("maker_kalshi_quoter")

M = {"target": 100, "end": "2099-01-01T00:00:00Z"}
_YL = [["0.50", "500"]]                # yes touch 0.50, deep
_NL = [["0.49", "500"]]                # no  touch 0.49, deep


def _quotes(inv=0.0, cost=0.0, yl=_YL, nl=_NL):
    return q.desired_quotes(M, yl, nl, q.utcnow(), inv=inv, cost=cost)


# ---- the defaults themselves are the contract -------------------------------------------------

def test_the_risk_rule_defaults_are_on():
    # A silent default flip (env drift, refactor) must fail a test, not a live session.
    assert q.EXIT_AT_TOUCH is True, "KALSHI_EXIT_AT_TOUCH must default ON"
    assert q.HOLDING_EXIT_ONLY is True, "KALSHI_HOLDING_EXIT_ONLY must default ON"
    assert q.STRAND_CROSS_S > 0, "KALSHI_STRAND_CROSS_S must default ON (fix 3)"


# ---- flat => both sides or nothing ------------------------------------------------------------

def test_flat_rests_both_sides():
    qs = _quotes(inv=0.0)
    sides = {x["side"] for x in qs}
    assert sides == {"yes", "no"}, f"flat must be two-sided, got {qs}"


def test_dust_below_tolerance_is_flat():
    # INV_TOLERANCE ct == "flat" (venue min order is 1 ct; sub-tolerance dust is untradable
    # noise, not a position). Both sides rest — this is the documented tolerance, not a leak.
    qs = _quotes(inv=q.INV_TOLERANCE - 0.5)
    assert {x["side"] for x in qs} == {"yes", "no"}


# ---- holding => exit only ---------------------------------------------------------------------

def test_holding_long_yes_rests_only_the_reducing_no():
    qs = _quotes(inv=8.0, cost=0.5)
    assert len(qs) == 1 and qs[0]["side"] == "no" and qs[0]["reason"] == "unwind", \
        f"holding must be exit-only, got {qs}"
    assert qs[0]["count"] <= 8, "an exit larger than |inv| would cross THROUGH flat"


def test_holding_long_no_rests_only_the_reducing_yes():
    qs = _quotes(inv=-8.0, cost=0.5)
    assert len(qs) == 1 and qs[0]["side"] == "yes" and qs[0]["reason"] == "unwind", \
        f"holding must be exit-only, got {qs}"
    assert qs[0]["count"] <= 8


def test_join_always_drill_respects_the_risk_rule():
    # Leak found in the 2026-07-28 adversarial review: the JOIN_ALWAYS drill switch returned
    # both sides BEFORE the holding=>exit-only check — the accumulating re-post defect, one env
    # var away. The risk rule outranks drills.
    import _pytest.monkeypatch as _mp
    mp = _mp.MonkeyPatch()
    try:
        mp.setattr(q, "JOIN_ALWAYS", True)
        qs = _quotes(inv=8.0, cost=0.5)
        assert len(qs) == 1 and qs[0]["side"] == "no" and qs[0]["reason"] == "unwind", \
            f"JOIN_ALWAYS while holding must still be exit-only, got {qs}"
        flat = _quotes(inv=0.0)
        assert {x["side"] for x in flat} == {"yes", "no"}, "flat drill join is unchanged"
    finally:
        mp.undo()


# ---- the exit rests AT the touch (fix 1) ------------------------------------------------------

def test_exit_rests_at_the_touch_not_the_loss_cap():
    # The 07-27 defect in one line: cost 0.364, MAX_UNWIND_LOSS 0.10 -> legacy cap
    # floor((1-0.364+0.10)*100)/100 = 0.73; the market's touch was 0.82. The legacy exit rested
    # 9c BEHIND the touch and never filled; 42 ct rode to settlement. Default: rest AT the touch.
    assert q._unwind_price(0.82, 0.364) == 0.82, \
        "EXIT_AT_TOUCH default must return the reference untouched"
    # end-to-end through desired_quotes: deep-underwater long-yes still quotes the NO touch
    qs = _quotes(inv=8.0, cost=0.82, yl=[["0.82", "500"]], nl=[["0.17", "500"]])
    assert len(qs) == 1 and qs[0]["side"] == "no" and qs[0]["price_dollars"] == 0.17, \
        f"the exit must rest at the touch whatever the loss, got {qs}"


def test_stop_flatten_offsets_price_at_the_touch_too():
    # _flatten_all prices its pass-1 maker offsets through _unwind_price as well — on 07-27 the
    # STOP path rested the NDQ offset at the capped 0.73 vs a 0.82 market (9c behind, unfillable)
    # and the escalation taker then paid 0.87. Fix 1's blast radius COVERS the STOP path; pin it.
    assert q._unwind_price(0.87, 0.60) == 0.87
