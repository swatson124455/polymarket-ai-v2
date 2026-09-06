"""Unit tests for the MB sizer (scripts/mb_sizer.py) - the pre-registered
sizing rule from the 2026-08-30 adversarial review (D1-D3/E4-E5).

Run: python -m pytest tests/unit/test_mb_sizer.py --override-ini "addopts=" """
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import band_tracker as bt  # noqa: E402  (the grader's e-process, injected)
import mb_sizer as ms  # noqa: E402

EV = bt.e_value
YM = bt.Y_MIN

STRONG = [0.30, 0.25, 0.35, 0.28, 0.32] * 8   # 40 mkts, mean +0.30
WEAK = [0.05, -0.04, 0.03, -0.02, 0.01, 0.02]  # tiny sample, tiny edge


def kw(**over):
    base = dict(bankroll=500.0, kelly_mult=0.25, concurrency=20,
                book_depth_usd=10_000.0, min_viable=1.0,
                e_value_fn=EV, y_min=YM)
    base.update(over)
    return base


# ---- D1/D2: LCB by e-process inversion -------------------------------------

def test_lcb_strong_sample_positive_and_below_mean():
    lcb = ms.lcb_edge(STRONG, EV, YM)
    mean = sum(STRONG) / len(STRONG)
    assert lcb is not None and 0.0 < lcb < mean


def test_lcb_unproven_sample_not_positive():
    # weak sample: e-value nowhere near 20 -> LCB must NOT be positive
    assert EV(WEAK) < 20.0
    lcb = ms.lcb_edge(WEAK, EV, YM)
    assert lcb is None or lcb <= 0.0


def test_lcb_consistent_with_e_process_duality():
    # at m just below the LCB the process must reject; just above, not
    lcb = ms.lcb_edge(STRONG, EV, YM)
    assert EV([y - (lcb - 1e-3) for y in STRONG]) >= 20.0
    assert EV([y - (lcb + 1e-3) for y in STRONG]) < 20.0


def test_lcb_monotone_in_data_shift():
    l1 = ms.lcb_edge(STRONG, EV, YM)
    l2 = ms.lcb_edge([y - 0.05 for y in STRONG], EV, YM)
    assert l2 is not None and abs((l1 - 0.05) - l2) < 1e-3


def test_lcb_empty_is_none_and_support_bound_respected():
    assert ms.lcb_edge([], EV, YM) is None
    # an edge AT the support bound: shifted search must not trip the
    # e-process assert (domain cap m_max = min(edges) - y_min)
    edges = [YM] + STRONG
    ms.lcb_edge(edges, EV, YM)  # must not raise


# ---- unproven trader sizes to exactly $0 (the load-bearing property) -------

def test_unproven_trader_gets_exactly_zero():
    r = ms.recommend_stake(WEAK, 0.15, 0.003, **kw())
    assert r["stake"] == 0.0 and "not demonstrated" in r["reason"] \
        or r["stake"] == 0.0 and "insufficient" in r["reason"]


def test_barely_negative_lcb_still_zero():
    # mutation audit 2026-08-30: weakening the gate to "lcb <= -0.05"
    # SURVIVED because no fixture had an LCB in (-0.05, 0]. Build one by
    # shifting a proven sample so its LCB lands just below zero.
    lcb0 = ms.lcb_edge(STRONG, EV, YM)
    edges = [y - (lcb0 + 0.02) for y in STRONG]
    lcb = ms.lcb_edge(edges, EV, YM)
    assert lcb is not None and -0.05 < lcb <= 0.0, lcb
    r = ms.recommend_stake(edges, 0.15, 0.003, **kw())
    assert r["stake"] == 0.0 and "not demonstrated" in r["reason"]


def test_proven_trader_gets_positive_stake():
    r = ms.recommend_stake(STRONG, 0.15, 0.003, **kw())
    assert r["stake"] > 0.0 and r["lcb"] > 0.0


# ---- E4: price-aware exact binary Kelly ------------------------------------

def test_kelly_formula_exact():
    r = ms.recommend_stake(STRONG, 0.40, 0.01, **kw())
    expect_k = min(1.0, r["lcb"] / (1.0 - 0.40 - 0.01))
    assert abs(r["k_full"] - expect_k) < 1e-12
    assert abs(r["raw_stake"] - 0.25 * expect_k * 500.0 / 20) < 1e-9


def test_no_upside_prices_size_zero():
    r = ms.recommend_stake(STRONG, 0.99, 0.02, **kw())
    assert r["stake"] == 0.0 and "no upside" in r["reason"]


# ---- D3: concurrency divides the bankroll ----------------------------------

def test_concurrency_scales_stake_down():
    r1 = ms.recommend_stake(STRONG, 0.15, 0.003, **kw(concurrency=10))
    r2 = ms.recommend_stake(STRONG, 0.15, 0.003, **kw(concurrency=100))
    assert abs(r1["raw_stake"] / r2["raw_stake"] - 10.0) < 1e-9


# ---- E5: rails downward only, NEVER clamp up -------------------------------

def test_book_depth_and_per_bet_caps_bind():
    r = ms.recommend_stake(STRONG, 0.15, 0.003,
                           **kw(bankroll=1e6, concurrency=1,
                                book_depth_usd=40.0))
    assert r["stake"] == 40.0 and "book_depth" in r["caps_applied"]
    r2 = ms.recommend_stake(STRONG, 0.15, 0.003,
                            **kw(bankroll=1e6, concurrency=1))
    assert r2["stake"] == ms.PER_BET_CAP_CANON \
        and "per_bet_cap" in r2["caps_applied"]


def test_below_min_viable_goes_to_zero_never_up():
    r = ms.recommend_stake(STRONG, 0.15, 0.003,
                           **kw(bankroll=5.0, concurrency=100,
                                min_viable=1.0))
    assert r["raw_stake"] > 0.0            # there WAS a positive intent
    assert r["stake"] == 0.0               # and it went DOWN to zero
    assert "below_min_viable_to_zero" in r["caps_applied"]
    assert "never clamp up" in r["reason"]


# ---- no smuggled risk constants --------------------------------------------

def test_risk_parameters_have_no_defaults():
    sig = inspect.signature(ms.recommend_stake)
    for name in ("bankroll", "kelly_mult", "concurrency",
                 "book_depth_usd", "min_viable", "e_value_fn", "y_min"):
        assert sig.parameters[name].default is inspect.Parameter.empty, name


def test_input_validation_rejects_garbage():
    import pytest
    for bad in (dict(bankroll=0.0), dict(kelly_mult=0.0),
                dict(kelly_mult=1.5), dict(concurrency=0),
                dict(book_depth_usd=-1.0), dict(min_viable=-0.5)):
        with pytest.raises(ValueError):
            ms.recommend_stake(STRONG, 0.5, 0.01, **kw(**bad))
    with pytest.raises(ValueError):
        ms.recommend_stake(STRONG, 1.2, 0.01, **kw())


def test_ruled_constants_match_grader():
    # E_BAR must equal the grader's ruled reject bar; cap must equal canon
    import cohort5_qualification as cq
    assert ms.E_BAR_RULED == cq.C1_E_REJECT
    assert ms.PER_BET_CAP_CANON == 300.0


# ---- GO-precondition rails (operator "build all 5", 2026-09-06) ----

def kw_lcb(**over):
    base = kw(**over)
    base.pop("e_value_fn"); base.pop("y_min")
    return base



def test_depth_frac_shrinks_usable_depth_downward_only():
    # proven LCB, big raw stake; depth 100 -> frac 0.25 binds at 25
    r_off = ms.recommend_stake_from_lcb(0.30, 0.5, 0.0, **kw_lcb(
        bankroll=20000.0, book_depth_usd=100.0))
    r_on = ms.recommend_stake_from_lcb(0.30, 0.5, 0.0, **kw_lcb(
        bankroll=20000.0, book_depth_usd=100.0), depth_frac=0.25)
    assert r_off["stake"] == 100.0 and "book_depth" in r_off["caps_applied"]
    assert r_on["stake"] == 25.0 and "book_depth" in r_on["caps_applied"]
    assert r_on["stake"] < r_off["stake"]  # strictly downward


def test_depth_frac_none_is_exact_parity_with_old_behavior():
    a = ms.recommend_stake_from_lcb(0.10, 0.5, 0.01, **kw_lcb())
    b = ms.recommend_stake_from_lcb(0.10, 0.5, 0.01, **kw_lcb(), depth_frac=None)
    assert a == b


def test_depth_frac_validation():
    import pytest
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            ms.recommend_stake_from_lcb(0.10, 0.5, 0.01, **kw_lcb(),
                                        depth_frac=bad)


def test_event_cap_scales_siblings_proportionally():
    props = [("a", "EV1", 200.0), ("b", "EV1", 100.0), ("c", "EV2", 50.0)]
    out = ms.cap_per_event(props, 150.0)
    by = {r["key"]: r for r in out}
    # EV1 total 300 > 150 -> scale by 0.5; EV2 total 50 <= 150 untouched
    assert abs(by["a"]["stake"] - 100.0) < 1e-9 and by["a"]["capped"]
    assert abs(by["b"]["stake"] - 50.0) < 1e-9 and by["b"]["capped"]
    assert by["c"]["stake"] == 50.0 and not by["c"]["capped"]
    assert abs(by["a"]["stake"] + by["b"]["stake"] - 150.0) < 1e-9


def test_event_cap_never_raises_a_stake():
    props = [("a", "EV1", 10.0), ("b", "EV1", 10.0)]
    out = ms.cap_per_event(props, 1000.0)
    assert all(r["stake"] == 10.0 and not r["capped"] for r in out)


def test_event_cap_ungrouped_passes_through_flagged():
    out = ms.cap_per_event([("a", None, 500.0)], 100.0)
    assert out[0]["stake"] == 500.0 and out[0]["event_id"] is None \
        and not out[0]["capped"] and out[0]["event_total"] is None


def test_event_cap_validation():
    import pytest
    with pytest.raises(ValueError):
        ms.cap_per_event([("a", "EV1", 1.0)], 0.0)
    with pytest.raises(ValueError):
        ms.cap_per_event([("a", "EV1", -1.0)], 100.0)
