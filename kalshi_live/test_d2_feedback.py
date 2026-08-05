"""W3/D2 — follow-the-profit credit feedback in the capital-aware rank.

Pins the scale-plan B1 proof criteria and the safety contract:
  * defaults are a PROVABLE NO-OP (byte-identical shadow_rank output)
  * a filled-but-never-paid-and-due series ranks BELOW a comparable payer when ON
  * a zero-fill earner is NEVER deranked (its multiplier is >= 1)
  * the feedback feed is fail-OPEN: missing/corrupt/wrong-schema -> {} -> no-op
  * builder verdicts follow the stated rules (paid / never_paid_due / insufficient),
    with due-ness on the W10-established >72h-past-close margin
"""
import datetime as dt
import json

import kalshi_capital_rank as kcr
from kalshi_credit_feedback import build_feedback

NOW = 1754400000.0


def _rows():
    return [{"ticker": "KXPAYER-26AUG09-T1", "usd_day": 50.0},
            {"ticker": "KXNOPAY-26AUG09-T1", "usd_day": 50.0},
            {"ticker": "KXZERO-26AUG09-T1", "usd_day": 50.0}]


def _base_kwargs():
    return dict(scores={}, fill_costs={}, market_cap_usd=45.0, inv_hard_ct=50,
                now=NOW, calib=1.0)


def test_defaults_are_a_provable_noop():
    a = kcr.shadow_rank(_rows(), **_base_kwargs())
    b = kcr.shadow_rank(_rows(), **_base_kwargs(), credit_feedback=None,
                        d2_bonus=1.0, d2_neverpaid_mult=1.0)
    assert a == b


def test_empty_feedback_table_is_a_noop():
    a = kcr.shadow_rank(_rows(), **_base_kwargs())
    b = kcr.shadow_rank(_rows(), **_base_kwargs(), credit_feedback={},
                        d2_bonus=2.0, d2_neverpaid_mult=0.25)
    assert a == b


FEEDBACK = {"KXPAYER": {"verdict": "paid"},
            "KXNOPAY": {"verdict": "never_paid_due"},
            "KXZERO": {"verdict": "paid"}}       # zero-fill earner: paid via credits


def test_never_paid_due_ranks_below_comparable_payer():
    out = kcr.shadow_rank(_rows(), **_base_kwargs(), credit_feedback=FEEDBACK,
                          d2_bonus=1.5, d2_neverpaid_mult=0.5)
    order = [d["ticker"].split("-")[0] for d in out]
    assert order.index("KXNOPAY") > order.index("KXPAYER")
    nopay = next(d for d in out if d["ticker"].startswith("KXNOPAY"))
    payer = next(d for d in out if d["ticker"].startswith("KXPAYER"))
    assert nopay["d2"] == 0.5 and payer["d2"] == 1.5
    assert nopay["cap_score"] < payer["cap_score"]


def test_zero_fill_earner_is_never_deranked():
    off = kcr.shadow_rank(_rows(), **_base_kwargs())
    on = kcr.shadow_rank(_rows(), **_base_kwargs(), credit_feedback=FEEDBACK,
                         d2_bonus=1.5, d2_neverpaid_mult=0.5)
    z_off = next(d for d in off if d["ticker"].startswith("KXZERO"))
    z_on = next(d for d in on if d["ticker"].startswith("KXZERO"))
    assert z_on["cap_score"] >= z_off["cap_score"]
    assert z_on["d2"] >= 1.0


def test_unknown_series_multiplier_is_one():
    out = kcr.shadow_rank(_rows(), **_base_kwargs(),
                          credit_feedback={"KXOTHER": {"verdict": "paid"}},
                          d2_bonus=2.0, d2_neverpaid_mult=0.25)
    assert all(d["d2"] == 1.0 for d in out)


def test_load_credit_feedback_fail_open(tmp_path):
    assert kcr.load_credit_feedback(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert kcr.load_credit_feedback(str(bad)) == {}
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema": 99, "series": {"KXA": {}}}))
    assert kcr.load_credit_feedback(str(wrong)) == {}
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"schema": 1, "series": {"KXA": {"verdict": "paid"}}}))
    assert kcr.load_credit_feedback(str(good)) == {"KXA": {"verdict": "paid"}}


# ---------------------------------------------------------------- builder verdicts
def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_builder_verdict_rules():
    now = dt.datetime(2026, 8, 5, tzinfo=dt.timezone.utc)
    old = _iso(now - dt.timedelta(hours=100))    # settled >72h ago -> due
    fresh = _iso(now - dt.timedelta(hours=10))   # settled 10h ago -> NOT yet due
    credits = [{"reason": "Liquidity Incentive for event KXPAID-26AUG01",
                "amount_cents": 500, "created_at": old}]
    orders = [
        {"ticker": "KXPAID-26AUG01-T1", "fill_count_fp": "2.0"},
        {"ticker": "KXBAD-26AUG01-T1", "fill_count_fp": "3.0"},    # filled, due, no credit
        {"ticker": "KXWAIT-26AUG04-T1", "fill_count_fp": "1.0"},   # filled, NOT yet due
        {"ticker": "KXREST-26AUG01-T1", "fill_count_fp": "0.0"},   # zero-fill, no credit
        # THE REVIEW-F3 CASE: one fully-due unpaid event + one fresh event in the SAME
        # series. Per-event evidence must convict; the fresh event must not shield it.
        {"ticker": "KXMIX-26AUG01-T1", "fill_count_fp": "4.0"},
        {"ticker": "KXMIX-26AUG04-T1", "fill_count_fp": "4.0"},
    ]
    setts = [{"ticker": "KXPAID-26AUG01-T1", "settled_time": old},
             {"ticker": "KXBAD-26AUG01-T1", "settled_time": old},
             {"ticker": "KXWAIT-26AUG04-T1", "settled_time": fresh},
             {"ticker": "KXMIX-26AUG01-T1", "settled_time": old},
             {"ticker": "KXMIX-26AUG04-T1", "settled_time": fresh}]
    fb = build_feedback(credits, orders, setts, now)
    assert fb["KXPAID"]["verdict"] == "paid"
    assert fb["KXBAD"]["verdict"] == "never_paid_due"
    assert fb["KXWAIT"]["verdict"] == "insufficient"
    assert fb["KXREST"]["verdict"] == "insufficient"   # zero fills -> nothing to punish
    assert fb["KXMIX"]["verdict"] == "never_paid_due"  # due evidence convicts
    assert fb["KXMIX"]["due_filled_events"] == 1


def test_builder_unsettled_ticker_blocks_only_its_own_event():
    """A filled ticker absent from the settlements feed (voided/delisted) blocks ITS event
    from being evidence — it must not shield the series' other, fully-due events."""
    now = dt.datetime(2026, 8, 5, tzinfo=dt.timezone.utc)
    old = _iso(now - dt.timedelta(hours=100))
    orders = [{"ticker": "KXGONE-26AUG01-T1", "fill_count_fp": "2.0"},
              {"ticker": "KXGONE-26AUG02-T1", "fill_count_fp": "2.0"}]   # never settled
    setts = [{"ticker": "KXGONE-26AUG01-T1", "settled_time": old}]
    fb = build_feedback([], orders, setts, now)
    assert fb["KXGONE"]["verdict"] == "never_paid_due"
    assert fb["KXGONE"]["due_filled_events"] == 1
