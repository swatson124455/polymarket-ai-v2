"""W11 replay harness — pure-function tests over synthetic data.

Pins the properties the harness's conclusions depend on:
  * credit timeline is sorted and per-series
  * validation sets derive by the stated rules (fills>0+no credit / credit+zero fills)
  * the feedback policy sees only credits with created_at <= cycle ts (NO LOOKAHEAD)
  * the recorded policy reproduces cap_score order
"""
import datetime as dt

from w11_replay import (credits_timeline, fills_by_series, make_policy_credit_feedback,
                        policy_recorded, validation_sets)

CREDITS = [
    {"reason": "Liquidity Incentive for event KXA-26JUL25", "amount_cents": 500,
     "created_at": "2026-07-26T06:00:00Z"},
    {"reason": "Liquidity Incentive for event KXB-26JUL30", "amount_cents": 150,
     "created_at": "2026-07-31T06:00:00Z"},
    {"reason": "Referral bonus", "amount_cents": 1500,
     "created_at": "2026-07-21T00:00:00Z"},
]
ORDERS = [
    {"ticker": "KXA-26JUL25-T1", "fill_count_fp": "3.0"},
    {"ticker": "KXC-26JUL28-T2", "fill_count_fp": "5.0"},   # filled, never credited
    {"ticker": "KXB-26JUL30-T9", "fill_count_fp": "0.0"},   # zero-fill, credited
]


def test_credits_timeline_sorted_and_parsed():
    tl = credits_timeline(CREDITS)
    assert [s for _, s, _ in tl] == ["KXA", "KXB"]      # referral has no event -> dropped
    assert tl[0][2] == 5.0 and tl[1][2] == 1.5
    assert tl[0][0] < tl[1][0]


def test_validation_sets_derived_by_rule():
    never_paid, zero_fill = validation_sets(CREDITS, ORDERS)
    assert never_paid == ["KXC"]
    assert zero_fill == ["KXB"]


def test_fills_by_series_sums_fp():
    f = fills_by_series(ORDERS)
    assert f["KXA"] == 3.0 and f["KXC"] == 5.0 and f["KXB"] == 0.0


COMPONENTS = [
    {"ticker": "KXA-26AUG01-T1", "cap_score": 0.5},
    {"ticker": "KXB-26AUG01-T1", "cap_score": 0.4},
    {"ticker": "KXC-26AUG01-T1", "cap_score": 0.45},
]


def test_recorded_policy_is_cap_score_order():
    assert policy_recorded(COMPONENTS, {}) == [
        "KXA-26AUG01-T1", "KXC-26AUG01-T1", "KXB-26AUG01-T1"]


def test_feedback_policy_no_lookahead_boundary():
    pol = make_policy_credit_feedback(bonus=2.0, never_paid_mult=0.5)
    fills = {"KXA": 1.0, "KXB": 0.0, "KXC": 5.0}
    # before KXB's credit exists: KXB is plain 0.4; KXC (filled, unpaid) halves to 0.225;
    # KXA credited doubles to 1.0
    ctx_early = {"credited_series_to_date": {"KXA"}, "fills_by_series": fills}
    assert pol(COMPONENTS, ctx_early) == [
        "KXA-26AUG01-T1", "KXB-26AUG01-T1", "KXC-26AUG01-T1"]
    # after KXB's credit posts it doubles too (0.8) but stays behind KXA (1.0)
    ctx_late = {"credited_series_to_date": {"KXA", "KXB"}, "fills_by_series": fills}
    assert pol(COMPONENTS, ctx_late) == [
        "KXA-26AUG01-T1", "KXB-26AUG01-T1", "KXC-26AUG01-T1"]


def test_zero_fill_earner_not_deranked():
    """The D2 proof criterion: a zero-fill series never has its score reduced."""
    pol = make_policy_credit_feedback(bonus=1.0, never_paid_mult=0.5)
    ctx = {"credited_series_to_date": set(), "fills_by_series": {"KXB": 0.0}}
    ranked = pol(COMPONENTS, ctx)
    assert ranked.index("KXB-26AUG01-T1") == 2      # unchanged from cap_score order
