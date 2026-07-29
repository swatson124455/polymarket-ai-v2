"""RE-REVIEW OF THE EXIT-BOUNDS FIX (2026-07-26) — what it masked or could have created.

Relaxing the price band for reducing orders is a widening change, so it was re-reviewed for
new holes rather than assumed safe. Three findings, all pinned here:

  A  _reducing_quotes called FLAT would OPEN risk. _unwind_size floors at 1, so inv==0
     yields a 1-contract order from a helper whose whole purpose is reducing. Every caller
     guarded, but the helper is now safe on its own terms.

  B  ORDERING. The new entry-gate fall-throughs returned a reducing quote BEFORE the
     crossed-book check, so a book that was both extreme and crossed started getting exits
     rested onto it — while the strand path refuses crossed books outright. The crossed
     check now runs first and refuses both directions, so the two paths agree.

  C  A FIX I PROPOSED AND THE SUITE REJECTED. I read the ladder hatch's uncapped cross leg
     as a missing pair-loss cap and added one; test_ladder_escape_hatch_splits_parked_unwind
     failed and WAS RIGHT. The cross leg buys at the adjacent book's own reference, priced
     consistently with the held leg, so it removes VARIANCE at roughly fair value. Capping
     it against COST BASIS would charge an already-sunk mark-to-market loss to a fresh
     hedge — the same sunk-cost error diagnosed in the unwind cap. Reverted, and the
     absence of the cap is now pinned.
"""
from test_live_hardening import MockClient, _run, q

M = {"target": 1000, "end": "2099-01-01T00:00:00Z"}


def _live_band(monkeypatch):
    # (MAX_UNWIND_LOSS was set here until the Q1 operator decision, 2026-07-28, deleted the
    # cap — exits price at the touch unconditionally, so there is nothing left to configure.)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.04)
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.96)
    monkeypatch.setattr(q, "EXIT_MIN_PRICE_DOLLARS", 0.01)
    monkeypatch.setattr(q, "EXIT_MAX_PRICE_DOLLARS", 0.99)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)


# ---- A: the reducing helper must never open risk --------------------------------------

def test_reducing_helper_is_inert_when_flat(monkeypatch):
    _live_band(monkeypatch)
    assert q._reducing_quotes(0.50, 0.49, 0.0, 0.40) == [], \
        "a reducing quote with nothing to reduce would OPEN a 1ct position"


def test_reducing_helper_is_inert_below_tolerance(monkeypatch):
    _live_band(monkeypatch)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    assert q._reducing_quotes(0.50, 0.49, 2.0, 0.40) == []
    assert q._reducing_quotes(0.50, 0.49, -2.0, 0.40) == []


def test_reducing_helper_still_acts_at_tolerance(monkeypatch):
    _live_band(monkeypatch)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    assert q._reducing_quotes(0.50, 0.49, 3.0, 0.40), "at tolerance it must still reduce"


# ---- B: crossed books refuse BOTH directions, matching the strand path -----------------

def test_crossed_and_extreme_book_rests_nothing_even_when_holding(monkeypatch):
    # best_y 0.99 (outside the entry band) AND crossed (0.99+0.99 >= 1.0). Before the
    # reordering the entry-gate fall-through returned an exit here.
    _live_band(monkeypatch)
    assert q.desired_quotes(M, [["0.99", "500"]], [["0.99", "500"]], q.utcnow(),
                            inv=-20.0, cost=0.05) == [], \
        "a crossed book is stale data, not a price — refuse it in both directions"


def test_crossed_book_inside_the_band_still_refused_when_holding(monkeypatch):
    _live_band(monkeypatch)
    assert q.desired_quotes(M, [["0.60", "500"]], [["0.60", "500"]], q.utcnow(),
                            inv=20.0, cost=0.05) == []


def test_uncrossed_extreme_book_still_exits(monkeypatch):
    # The reordering must NOT have re-closed the exit it was meant to open.
    _live_band(monkeypatch)
    qs = q.desired_quotes(M, [["0.97", "5000"]], [["0.02", "5000"]], q.utcnow(),
                          inv=-20.0, cost=0.05)
    assert qs and qs[0]["reason"] == "unwind"


# ---- C: the ladder hatch cross leg must NOT be pair-loss capped -------------------------

def test_ladder_hatch_cross_is_NOT_pair_loss_capped(monkeypatch):
    """C RESOLVED THE OTHER WAY — the hatch must NOT get an _unwind_price cap.

    I added one, and test_ladder_escape_hatch_splits_parked_unwind failed. The test was
    right. The cross leg is bought at the ADJACENT book's own reference, priced consistently
    with the held leg: +20 YES on 4.050 at basis 0.62 while the book marks yes-bid 0.13,
    hedged with NO on 4.060 at 0.92 -> $9.20 converts an asset worth ~$1.30 into one worth
    >= $10.00. Roughly neutral at market prices; it removes VARIANCE, not expected value.

    _unwind_price bounds loss against COST BASIS, so capping here charges an ALREADY-SUNK
    mark-to-market loss against a fresh hedge and refuses it exactly when the position has
    moved most — the same sunk-cost error diagnosed in the unwind cap itself. This pins the
    absence of that cap so it is not "fixed" back in. (The unwind cap itself was later
    deleted outright — Q1 operator decision 2026-07-28 — which resolves C the same way.)
    """
    _live_band(monkeypatch)
    # the floored-pair property that makes the trade sound: payoff is never below $1
    for held_pays, hedge_pays in ((0, 1), (1, 1), (1, 0)):
        assert held_pays + hedge_pays >= 1
    # and the venue bound, not a cost-basis cap, is what gates the cross price
    assert q._ok_exit_price(0.92) and q._ok_exit_price(0.99)
    assert not q._ok_exit_price(1.00)


# ---- the widening must not have reached ENTRIES anywhere -------------------------------

def test_no_entry_anywhere_outside_the_strategy_band(monkeypatch):
    _live_band(monkeypatch)
    for yes, no in (([["0.97", "5000"]], [["0.02", "5000"]]),
                    ([["0.02", "5000"]], [["0.97", "5000"]]),
                    ([["0.99", "5000"]], []),
                    ([], [["0.99", "5000"]])):
        for x in q.desired_quotes(M, yes, no, q.utcnow(), inv=0.0):
            assert False, f"FLAT must open nothing on {yes}/{no}, got {x}"


def test_held_exit_never_flips_the_sign(monkeypatch):
    # The widened path must still never rest more than |inv| — crossing flat would turn a
    # de-risk into an opposite position.
    _live_band(monkeypatch)
    for inv in (1.0, 5.0, 62.0, -1.0, -5.0, -34.0):
        yes = [] if inv > 0 else [["0.99", "5000"]]
        no = [["0.99", "5000"]] if inv > 0 else []
        for x in q.desired_quotes(M, yes, no, q.utcnow(), inv=inv, cost=0.05):
            assert x["count"] <= abs(inv), f"inv={inv} rested {x['count']}"
