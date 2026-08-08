"""FAILING-BEFORE pins for M-3 — w0b payout-timing classification basis.

MECHANISM: credits are per EVENT, but an event holds many strikes with DIFFERENT close times.
The old rule measured every credit against close_MAX (the LATEST close in the event), so an
ordinary close+1 payment for an EARLY-closing strike printed as BEFORE_CLOSE with a large
negative delta — and that tag is the sole basis for the "payment is NOT gated on close" canon
which the 2026-08-07T15:00Z APRPOTUS checkpoint is read through.

Before the fix `classify` does not exist and the inline rule tags case 1 below BEFORE_CLOSE.
"""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
w0b = pytest.importorskip("w0b_payout_timing")

T = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)  # noqa: E731


def test_multi_market_credit_between_closes_is_ambiguous_not_before_close():
    """THE DEFECT: strike A closes 08-02, strike B closes 08-20, credit lands 08-03.

    Against close_max (08-20) the old code printed BEFORE_CLOSE -408h — reading as proof the
    venue pays before close. It is equally consistent with a plain close+1 for strike A."""
    tag, d_h, basis = w0b.classify(T("2026-08-03T06:00"), T("2026-08-02T14:00"),
                                   T("2026-08-20T14:00"), 2)
    assert tag == "AMBIGUOUS", "a credit between the first and last close proves nothing"
    assert basis == "multi-ambiguous"


def test_single_market_event_is_watertight_before_close():
    tag, d_h, basis = w0b.classify(T("2026-08-02T06:00"), T("2026-08-15T14:00"),
                                   T("2026-08-15T14:00"), 1)
    assert (tag, basis) == ("BEFORE_CLOSE", "single")
    assert d_h < 0


def test_multi_market_credit_before_every_close_is_watertight():
    tag, _, basis = w0b.classify(T("2026-08-01T06:00"), T("2026-08-02T14:00"),
                                 T("2026-08-20T14:00"), 3)
    assert (tag, basis) == ("BEFORE_CLOSE", "multi-min")


@pytest.mark.parametrize("hours,expected", [(1, "CLOSE_WINDOW"), (47, "CLOSE_WINDOW"),
                                            (49, "LATE")])
def test_post_close_bands_unchanged(hours, expected):
    close = T("2026-08-02T14:00")
    tag, _, _ = w0b.classify(close + dt.timedelta(hours=hours), close, close, 1)
    assert tag == expected
