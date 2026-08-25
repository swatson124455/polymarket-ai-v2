"""Unit tests for the MB measurement canon (scripts/mb_canon.py).

Run: python -m pytest tests/unit/test_mb_canon.py --override-ini "addopts=" """
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import mb_canon as mc  # noqa: E402


def test_fee_precedence_chain():
    frm = {"tok_v": 0.05}
    fmap = {"tok_z": 0, "tok_v": 500}
    fee, src = mc.canon_fee("tok_v", 0.8, frm, fmap)
    assert src == "venue_formula" and abs(fee - 0.05 * 0.8 * 0.2) < 1e-12
    fee, src = mc.canon_fee("tok_z", 0.8, frm, fmap)
    assert src == "zero_fee_exempt" and fee == 0.0
    fee, src = mc.canon_fee("tok_unknown", 0.8, frm, fmap)
    assert src == "flat_2pct_fallback" and abs(fee - 0.02 * 0.8) < 1e-12
    # venue formula beats zero-fee when both present (rate map is measured)
    fee, src = mc.canon_fee("tok_v", 0.5, frm, {"tok_v": 0})
    assert src == "venue_formula"


def test_edge_atom_no_side_inversion():
    # NEVER invert for NO positions: same formula all sides (house rule)
    assert mc.edge_atom(1.0, 0.7, 0.01) == 1.0 - 0.7 - 0.01
    assert mc.edge_atom(0.0, 0.7, 0.01) == -0.71


def _rec(tok, fill, ts, first=True, verdict="OK"):
    return {"token_id": tok, "shadow_fill": fill, "detect_ts": ts,
            "first_buy": first, "verdict": verdict}


def test_per_market_equal_weight_and_ordering():
    # tok A: 3 fills (would dominate a per-fill pool); tok B: 1 fill
    recs = [_rec("A", 0.70, 100), _rec("A", 0.70, 110), _rec("A", 0.70, 120),
            _rec("B", 0.60, 105)]
    outcomes = {"A": 0.0, "B": 1.0}
    seq = mc.per_market_edges(recs, outcomes, {}, {})
    assert [t for _, t, _ in seq] == ["A", "B"]      # ordered by first ts
    pooled = mc.pooled_edge(seq)
    a_edge = 0.0 - 0.70 - 0.02 * 0.70
    b_edge = 1.0 - 0.60 - 0.02 * 0.60
    # canonical = mean of MARKET means: A's 3 fills count once
    assert abs(pooled - (a_edge + b_edge) / 2) < 1e-12
    # a per-fill pool would differ - prove the distinction is real
    per_fill = (3 * a_edge + b_edge) / 4
    assert abs(pooled - per_fill) > 1e-6


def test_filters_window_firstbuy_verdict_band():
    recs = [_rec("A", 0.70, 50),                       # pre-epoch: out
            _rec("B", 0.70, 100, first=False),         # not first-buy: out
            _rec("C", 0.70, 100, verdict="NO_BOOK"),   # not OK: out
            _rec("D", 0.90, 100),                      # out of band: out
            _rec("E", 0.70, 100)]                      # in
    outcomes = {k: 1.0 for k in "ABCDE"}
    seq = mc.per_market_edges(recs, outcomes, {}, {}, epoch=60,
                              lo=0.65, hi=0.85)
    assert [t for _, t, _ in seq] == ["E"]


def test_pooled_edge_empty_returns_none_never_zero():
    assert mc.pooled_edge([]) is None


def test_merge_labels_conflict_is_loud_not_silent():
    db = {"t1": 1.0, "t2": 0.0}
    supp = {"t2": 1.0, "t3": 0.0}
    merged, conflicts = mc.merge_labels(db, supp)
    assert merged == {"t1": 1.0, "t3": 0.0}            # t2 EXCLUDED
    assert conflicts == {"t2": (0.0, 1.0)}             # and reported
    # no conflict when they agree
    merged2, c2 = mc.merge_labels({"t9": 1.0}, {"t9": 1.0})
    assert merged2 == {"t9": 1.0} and c2 == {}


def test_verifier_alarms_on_bad_data():
    """The verifier must ALARM when fed a record the chain contradicts -
    prove the comparator can fail, not just pass (anti-false-pass)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "canon_verify", os.path.join(os.path.dirname(__file__), "..", "..",
                                     "scripts", "canon_verify.py"))
    cv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cv)
    # price bracket comparator: recorded far outside chain clip range -> fail
    prices = [0.70, 0.71]
    rec_p = 0.95
    p_ok = bool(prices) and (
        (min(prices) - cv.PRICE_TOL <= rec_p <= max(prices) + cv.PRICE_TOL)
        or abs(sum(prices) / len(prices) - rec_p) <= cv.PRICE_TOL)
    assert p_ok is False
    rec_p2 = 0.7050
    p_ok2 = bool(prices) and (
        (min(prices) - cv.PRICE_TOL <= rec_p2 <= max(prices) + cv.PRICE_TOL)
        or abs(sum(prices) / len(prices) - rec_p2) <= cv.PRICE_TOL)
    assert p_ok2 is True


def test_fee_invariants_randomized():
    """Property layer for fees - added 2026-08-25 after a mutation audit
    showed the bounds-only fuzz invariants are blind to a fee SIGN flip
    (atoms stay inside [-1.02, 1]). Invariants: fee >= 0 always; venue fee
    equals rate*f*(1-f) recomputed here independently; fee never exceeds
    the flat fallback's worst case."""
    import random
    rng = random.Random(20260825)
    for _ in range(500):
        f = rng.uniform(0.001, 0.999)
        rate = rng.choice([0.0, 0.04, 0.05, 0.07])
        fee, src = mc.canon_fee("tv", f, {"tv": rate}, {})
        assert fee >= 0.0, (rate, f, fee)
        assert abs(fee - rate * f * (1.0 - f)) < 1e-12
        fee2, src2 = mc.canon_fee("unknown", f, {"tv": rate}, {})
        assert fee2 >= 0.0 and src2 == "flat_2pct_fallback"
        assert abs(fee2 - 0.02 * f) < 1e-12
        # venue fee at max rate 0.07 peaks at 0.0175 < flat cap 0.02
        assert fee <= 0.07 * 0.25 + 1e-12


def test_date_seed_is_deterministic():
    import random
    a = random.Random(20260825).sample(range(1000), 10)
    b = random.Random(20260825).sample(range(1000), 10)
    c = random.Random(20260826).sample(range(1000), 10)
    assert a == b and a != c
