"""Offline tests for the post_only cross-block probe's pure logic (book parsing,
crossing-price derivation, verdict table). No network; loads via importlib."""
import importlib.util
import pathlib
import sys

_S = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _S / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


po = _load("verify_kalshi_postonly")


# ---------------- best_levels: BEST IS LAST (canon) ----------------

def test_best_levels_takes_last_level_not_first():
    ob = {"orderbook_fp": {"yes_dollars": [["0.10", "5"], ["0.30", "7"]],
                           "no_dollars": [["0.20", "3"], ["0.55", "9"]]}}
    assert po.best_levels(ob) == (0.30, 0.55)


def test_best_levels_empty_sides_return_none():
    assert po.best_levels({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}) == (None, None)
    assert po.best_levels({}) == (None, None)
    assert po.best_levels(None) == (None, None)


def test_best_levels_skips_zero_size_and_malformed_rows():
    ob = {"orderbook_fp": {"yes_dollars": [["0.10", "5"], ["0.40", "0"], ["bad"]],
                           "no_dollars": [["0.25", "2"], [None, None]]}}
    # 0.40 has size 0 and the malformed row is skipped -> best valid is 0.10
    assert po.best_levels(ob) == (0.10, 0.25)


def test_best_levels_rejects_out_of_range_prices():
    ob = {"orderbook_fp": {"yes_dollars": [["0.30", "5"], ["1.00", "5"]],
                           "no_dollars": [["0.00", "5"]]}}
    yb, nb = po.best_levels(ob)
    assert yb == 0.30      # 1.00 is not a valid resting price
    assert nb is None      # 0.00 likewise


# ---------------- crossing price: a yes ask IS 1 - best_no_bid ----------------

def test_crossing_bid_price_is_complement_of_best_no_bid():
    assert po.crossing_bid_price(0.40) == 0.60


def test_crossing_bid_price_none_when_no_opposing_side():
    assert po.crossing_bid_price(None) is None


def test_crossing_bid_price_clamps_nonsense():
    assert po.crossing_bid_price(0.001) is None    # -> yes_ask 0.999 > 0.99
    assert po.crossing_bid_price(1.0) is None      # -> yes_ask 0.0


# ---------------- verdict table ----------------

def test_pass_requires_control_rested_and_test_rejected():
    v, m = po.classify(control_rested=True, test_rejected=True,
                       test_filled=False, external=True)
    assert v == "PASS"
    assert "EXTERNAL" in m


def test_self_cross_pass_is_labelled_weaker():
    v, m = po.classify(control_rested=True, test_rejected=True,
                       test_filled=False, external=False)
    assert v == "PASS"
    assert "STP" in m and "weaker" in m


def test_fill_is_critical_even_if_also_marked_rejected():
    v, m = po.classify(control_rested=True, test_rejected=True,
                       test_filled=True, external=True)
    assert v == "FAIL-CRITICAL"
    assert "FILLED" in m


def test_control_failure_is_inconclusive_not_pass():
    # the whole point of the control arm: without it a rejection proves nothing
    for rejected in (True, False):
        v, _ = po.classify(control_rested=False, test_rejected=rejected,
                           test_filled=False, external=True)
        assert v == "INCONCLUSIVE"


def test_neither_rejected_nor_filled_is_fail():
    v, m = po.classify(control_rested=True, test_rejected=False,
                       test_filled=False, external=True)
    assert v == "FAIL"
    assert "unproven" in m
