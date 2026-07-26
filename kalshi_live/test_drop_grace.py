"""Pins for DROP HYSTERESIS (KALSHI_DROP_GRACE) — the root of the identical-churn cancels.

THE DEFECT: 17 of 478 zero-fill cancels on the clean slice were followed by a re-create at the SAME
price AND size. diff_orders cannot produce that — an exact (side, price, count) match survives. It
happens when a ticker leaves `desired` ENTIRELY for one cycle and returns: the diff tears its whole
book down, then rebuilds it. The benign cause is FOOTPRINT ROTATION — the pool-ordered top-N
shuffles and a market drops out for a cycle with nothing about it having changed.

THE SAFETY ARGUMENT, which is the whole design: grace covers "we didn't look at it", NEVER "we
looked and said no". A ticker rejected by a gate, the capital cap, the breaker or wind-down IS in
the footprint but not in desired — it gets no grace. Retaining through a decision would defeat the
cap or gate that made it.

  T1 ROTATED OUT           — absent from footprint => book retained VERBATIM, diff emits nothing.
  T2 REJECTED              — in footprint, not desired => NO grace, the cancel stands.
  T3 GRACE EXPIRES         — bounded; it cannot hold a book forever.
  T4 COUNTER RESETS        — a ticker that returns starts its grace budget fresh.
  T5 NOTHING TO PROTECT    — no standing orders => nothing to retain.
  T6 STILL WANTED          — already in desired => untouched (never duplicated).
  T7 FLAG OFF / ZERO       — provable no-op.
"""
from test_live_hardening import q


def _std(oid, side, price, count):
    return {"order_id": oid, "side": side, "price_dollars": price, "count": count}


def _standing():
    return {"ROT": [_std("o1", "yes", 0.50, 20), _std("o2", "no", 0.49, 20)]}


def test_rotated_out_ticker_keeps_its_book_and_the_diff_emits_nothing():
    standing = _standing()
    desired, grace = q.apply_drop_grace(standing, {}, footprint_tickers=set(),
                                        prev_grace={}, grace_cycles=2)
    assert grace == {"ROT": 1}
    cancels, creates = q.diff_orders(standing, desired)
    assert cancels == [] and creates == [], "retained verbatim -> no churn at all"


def test_a_market_we_looked_at_and_rejected_gets_no_grace():
    """THE SAFETY PIN. In the footprint but not in desired means a gate/cap/breaker said no.
    Retaining it would defeat whatever made that decision."""
    standing = _standing()
    desired, grace = q.apply_drop_grace(standing, {}, footprint_tickers={"ROT"},
                                        prev_grace={}, grace_cycles=2)
    assert grace == {} and desired == {}
    assert q.diff_orders(standing, desired)[0] == ["o1", "o2"], "the cancel must stand"


def test_grace_is_bounded_and_expires():
    standing = _standing()
    d1, g1 = q.apply_drop_grace(standing, {}, set(), {}, 2)
    assert g1 == {"ROT": 1} and "ROT" in d1
    d2, g2 = q.apply_drop_grace(standing, {}, set(), g1, 2)
    assert g2 == {"ROT": 2} and "ROT" in d2
    d3, g3 = q.apply_drop_grace(standing, {}, set(), g2, 2)
    assert g3 == {} and d3 == {}, "grace exhausted -> released to the diff"
    assert q.diff_orders(standing, d3)[0] == ["o1", "o2"]


def test_counter_resets_when_the_ticker_comes_back():
    """A returning ticker must not carry a stale count, or a market rotating in and out would
    burn its budget over unrelated cycles and be cancelled while still wanted."""
    standing = _standing()
    _, g1 = q.apply_drop_grace(standing, {}, set(), {}, 2)
    # it comes back into desired -> not written into new_grace -> counter cleared
    _, g2 = q.apply_drop_grace(standing, {"ROT": [{"side": "yes", "price_dollars": 0.50,
                                                   "count": 20}]}, set(), g1, 2)
    assert g2 == {}
    _, g3 = q.apply_drop_grace(standing, {}, set(), g2, 2)
    assert g3 == {"ROT": 1}, "budget starts fresh"


def test_nothing_to_protect_and_still_wanted_are_both_untouched():
    assert q.apply_drop_grace({}, {}, set(), {}, 2) == ({}, {})
    assert q.apply_drop_grace({"T": []}, {}, set(), {}, 2) == ({}, {})
    want = {"ROT": [{"side": "yes", "price_dollars": 0.50, "count": 20}]}
    d, g = q.apply_drop_grace(_standing(), want, set(), {}, 2)
    assert d == want and g == {}, "already wanted -> untouched, never duplicated"


def test_zero_grace_is_a_provable_no_op():
    standing = _standing()
    d, g = q.apply_drop_grace(standing, {}, set(), {}, 0)
    assert d == {} and g == {}
    assert q.diff_orders(standing, d)[0] == ["o1", "o2"], "legacy cancel behaviour"


def test_ships_off():
    assert q.DROP_GRACE == 0
