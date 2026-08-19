"""Pins for KALSHI_PIVOT_FAR_FIRST — concentrated-cliff selection ordering
(operator-ratified 2026-08-19, decision 3A intent).

The pivot's near-money-first within-series sort burns pool slots on mid-band-excluded
strikes in cliff mode. Flag=1 orders extreme strikes first; default 0 is byte-identical
legacy. Unparseable strikes sort LAST in both modes (a sign-flip putting prox=1e9
first would hand pool slots to garbage tickers).
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _prog(ticker, reward_cents=1000000):
    now = q.utcnow()
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "period_reward": reward_cents, "target_size_fp": "1000.00",
            "discount_factor_bps": 5000,
            "start_date": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "end_date": (now + timedelta(hours=96)).isoformat().replace("+00:00", "Z")}


LADDER = ["KXGAS-26AUG24-4.000",   # median -> near-money
          "KXGAS-26AUG24-3.800",   # most extreme low
          "KXGAS-26AUG24-4.240",   # most extreme high
          "KXGAS-26AUG24-4.040"]


def _sel(monkeypatch, far_first, coverage=3):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 0.0)
    monkeypatch.setattr(q, "PIVOT_SELECT", 1)
    monkeypatch.setattr(q, "ALLOC_KEY", 0)
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "PIVOT_COVERAGE", coverage)
    monkeypatch.setattr(q, "PIVOT_FAR_FIRST", far_first)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 3)
    picked = q.select_footprint([_prog(t) for t in LADDER], q.utcnow())
    return [r["ticker"] for r in picked]


def test_far_first_puts_extreme_strikes_in_the_coverage_floor(monkeypatch):
    got = _sel(monkeypatch, far_first=True)
    assert got[0] == "KXGAS-26AUG24-4.240" or got[0] == "KXGAS-26AUG24-3.800", got
    assert set(got[:2]) == {"KXGAS-26AUG24-4.240", "KXGAS-26AUG24-3.800"}, \
        "the two most extreme strikes must occupy the first coverage slots"


def test_default_is_near_money_first_legacy(monkeypatch):
    got = _sel(monkeypatch, far_first=False)
    # median of [3.800, 4.000, 4.040, 4.240] is ks[2] = 4.040 (upper-middle on even count)
    assert got[0] == "KXGAS-26AUG24-4.040", \
        "flag off must keep the legacy near-median-first order"


def test_shipped_default_is_off():
    if "KALSHI_PIVOT_FAR_FIRST" not in os.environ:
        assert q.PIVOT_FAR_FIRST is False


def test_unparseable_strike_sorts_last_in_both_modes(monkeypatch):
    ladder = LADDER + ["KXGAS-26AUG24-CLAUM"]        # no numeric strike
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 0.0)
    monkeypatch.setattr(q, "PIVOT_SELECT", 1)
    monkeypatch.setattr(q, "ALLOC_KEY", 0)
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "PIVOT_COVERAGE", 4)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 4)
    for flag in (False, True):
        monkeypatch.setattr(q, "PIVOT_FAR_FIRST", flag)
        got = [r["ticker"] for r in
               q.select_footprint([_prog(t) for t in ladder], q.utcnow())]
        assert got.index("KXGAS-26AUG24-CLAUM") == len(got) - 1, (flag, got)
