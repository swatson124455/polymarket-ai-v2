"""Pins for the MIN-RUNWAY ENTRY GATE (KALSHI_MIN_RUNWAY_H) — concentrated-cliff build
2026-08-19, implementing the 08-13 roadmap's LOCKED "window >= 49h" ENTRY rule.

WHY: the per-program $1 cliff (canon 2026-08-18) makes remaining PROGRAM window the revenue
runway — a program ENTERED with less runway than the cliff projection needs is guaranteed
dead weight (accrues sub-$1 -> pays $0) while carrying full fill risk. R1 hit this live.

WHY THE QUOTE PATH AND NOT A FOOTPRINT DROP (section review, 2026-08-19): a footprint drop
evicts markets we are RESTING in the moment runway decays under the bar, forfeiting the
final 49h of accrual in every window. Entry-only semantics: flat + no resting orders ->
refuse; resting or holding -> fall through to the ordinary late-life/wind-down cutoffs.
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

YL = [["0.50", "500"]]
NL = [["0.49", "500"]]


def _m(hours_to_end):
    return {"target": 100, "ticker": "KXTEST-01",
            "end": (q.utcnow() + timedelta(hours=hours_to_end)
                    ).isoformat().replace("+00:00", "Z")}


def _run(hours_to_end, runway_h=49.0, inv=0.0, own=None):
    stats = {}
    old = q.MIN_RUNWAY_H
    q.MIN_RUNWAY_H = runway_h
    try:
        quotes = q.desired_quotes(_m(hours_to_end), YL, NL, q.utcnow(),
                                  inv=inv, own=own, stats=stats)
    finally:
        q.MIN_RUNWAY_H = old
    return quotes, stats


def test_short_runway_refused_flat():
    quotes, stats = _run(24)
    assert quotes == [] and stats.get("gate_min_runway") == 1


def test_long_runway_passes_the_gate():
    _, stats = _run(96)
    assert "gate_min_runway" not in stats


def test_zero_disables_it():
    """0 must mean OFF (today's exact behavior), not 'reject everything'."""
    _, stats = _run(24, runway_h=0.0)
    assert "gate_min_runway" not in stats


def test_shipped_default_is_off():
    if "KALSHI_MIN_RUNWAY_H" not in os.environ:
        assert q.MIN_RUNWAY_H == 0.0


def test_resting_orders_keep_their_market():
    """The accrual-tail pin: a market we are resting in is NOT evicted at end-49h."""
    quotes, stats = _run(24, own={"yes": 8.0, "no": 8.0})
    assert "gate_min_runway" not in stats
    assert quotes, "resting presence must continue to the ordinary wind-down, not stop here"


def test_held_inventory_falls_through_to_reducing_paths():
    quotes, stats = _run(24, inv=-10.0)
    assert "gate_min_runway" not in stats, "counter fires only on the priceless [] path"
    assert quotes, "held inventory must still get an exit order"


def test_wind_down_still_outranks_it():
    """A market inside the wind-down cutoff is attributed to wind-down, not min-runway —
    the safety gate stays first and separately attributable."""
    quotes, stats = _run(0.1)                        # ~6 minutes to end
    assert quotes == []
    assert stats.get("gate_wind_down_flat") == 1
    assert "gate_min_runway" not in stats
