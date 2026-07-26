"""ENTRY BANDS MUST NOT GATE EXITS (2026-07-26).

MIN/MAX_PRICE_DOLLARS (live 0.04/0.96) exist so we never OPEN at extreme prices. They were
also applied to REDUCING orders, which refuses to close a position exactly when it has moved
deep against us -- the reducing side of a position whose book sits at 0.99 IS a 0.99 bid.
Live: KXAAAGASW-26JUL27-4.080 logged "reducing side unpriceable" at 2026-07-26T15:31:46Z
even after MAX_UNWIND_LOSS was raised to 0.10, because 0.99 > 0.96.

Root fix: _ok_entry_price (strategy band) vs _ok_exit_price (VENUE bounds 0.01-0.99), and ONE
_reducing_quotes builder replacing five byte-identical copies inside desired_quotes.

The asymmetry these pin, in both directions:
  an EXIT at 0.99 must be PLACED      (was refused -> position rode to settlement)
  an ENTRY at 0.99 must still be REFUSED (relaxing that would be a far worse regression)
"""
from test_live_hardening import MockClient, _run, q

M = {"target": 1000, "end": "2099-01-01T00:00:00Z"}


def _live_band(monkeypatch, unwind_loss=0.10):
    """The production band: 0.04/0.96, with the live unwind loss cap."""
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.04)
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.96)
    monkeypatch.setattr(q, "EXIT_MIN_PRICE_DOLLARS", 0.01)
    monkeypatch.setattr(q, "EXIT_MAX_PRICE_DOLLARS", 0.99)
    monkeypatch.setattr(q, "MAX_UNWIND_LOSS", unwind_loss)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)


def _dq(**kw):
    kw.setdefault("now", q.utcnow())
    return q.desired_quotes(M, kw.pop("yes"), kw.pop("no"), kw.pop("now"), **kw)


# ---- the live 4.080 shape: long NO, exit is a YES bid at the 0.99 touch ---------------

def test_exit_at_099_is_placed_on_a_one_sided_book(monkeypatch):
    _live_band(monkeypatch)
    qs = _dq(yes=[["0.99", "5000"]], no=[], inv=-20.0, cost=0.05)
    assert qs, "a long-NO position must be exitable via a YES bid at the 0.99 touch"
    assert qs[0]["side"] == "yes"
    assert qs[0]["reason"] == "unwind"
    assert abs(qs[0]["price_dollars"] - 0.99) < 1e-9


def test_exit_at_099_is_placed_on_a_two_sided_extreme_book(monkeypatch):
    # Both sides present, sum < 1.0, but best_y is outside the ENTRY band.
    _live_band(monkeypatch)
    qs = _dq(yes=[["0.97", "5000"]], no=[["0.02", "5000"]], inv=-20.0, cost=0.05)
    assert qs and qs[0]["side"] == "yes" and qs[0]["reason"] == "unwind"
    assert qs[0]["price_dollars"] > 0.96, "must not be clamped to the entry band"


def test_long_yes_exits_via_no_bid_on_an_extreme_book(monkeypatch):
    _live_band(monkeypatch)
    qs = _dq(yes=[["0.02", "5000"]], no=[["0.97", "5000"]], inv=20.0, cost=0.05)
    assert qs and qs[0]["side"] == "no" and qs[0]["reason"] == "unwind"


# ---- the regression that would be worse: entries must NOT gain the relaxed band -------

def test_flat_never_opens_on_an_extreme_book(monkeypatch):
    _live_band(monkeypatch)
    assert _dq(yes=[["0.97", "5000"]], no=[["0.02", "5000"]], inv=0.0) == [], \
        "FLAT on a book outside the entry band must open nothing"


def test_flat_never_opens_on_a_one_sided_book(monkeypatch):
    _live_band(monkeypatch)
    assert _dq(yes=[["0.99", "5000"]], no=[], inv=0.0) == []


def test_entry_band_still_binds_on_a_normal_book(monkeypatch):
    # A healthy two-sided book inside the band still produces JOIN quotes, both sides.
    _live_band(monkeypatch)
    qs = _dq(yes=[["0.50", "5000"]], no=[["0.49", "5000"]], inv=0.0)
    assert {x["side"] for x in qs} == {"yes", "no"}
    for x in qs:
        assert x["reason"] != "unwind"
        assert 0.04 < x["price_dollars"] <= 0.96


# ---- the economic governor is MAX_UNWIND_LOSS, not the band --------------------------

def test_unwind_loss_cap_still_binds_after_the_relaxation(monkeypatch):
    # Long YES at 0.5426 (the live 4.140 basis) against a 0.99 NO bid: paying 0.99 to close
    # would cost 1.5326 for $1.00. The cap must pull the resting price down to 1-cost+MUL.
    _live_band(monkeypatch, unwind_loss=0.10)
    qs = _dq(yes=[], no=[["0.99", "5000"]], inv=62.0, cost=0.5426)
    assert qs and qs[0]["side"] == "no"
    assert qs[0]["price_dollars"] <= 1.0 - 0.5426 + 0.10 + 1e-9
    assert qs[0]["price_dollars"] < 0.99, "the cap, not the band, must be what binds"


def test_exit_below_the_entry_floor_is_allowed(monkeypatch):
    # Entry floor 0.04; a 0.02 exit is a legitimate venue price for a nearly worthless side.
    _live_band(monkeypatch)
    qs = _dq(yes=[], no=[["0.02", "5000"]], inv=20.0, cost=0.10)
    assert qs and abs(qs[0]["price_dollars"] - 0.02) < 1e-9


def test_exit_never_overshoots_flat(monkeypatch):
    _live_band(monkeypatch)
    qs = _dq(yes=[["0.99", "5000"]], no=[], inv=-9.0, cost=0.05)
    assert qs and qs[0]["count"] <= 9


def test_crossed_book_is_still_refused(monkeypatch):
    # PRESERVED: a crossed/stale book is a data anomaly and must not be quoted either way.
    _live_band(monkeypatch)
    assert _dq(yes=[["0.60", "500"]], no=[["0.60", "500"]], inv=20.0, cost=0.05) == []


# ---- the five copies are now one -----------------------------------------------------

def test_reducing_quotes_returns_empty_when_the_needed_side_is_absent(monkeypatch):
    # long YES needs the NO book; absent -> [] and NO exception (the callers used to pass
    # None straight into _unwind_price).
    _live_band(monkeypatch)
    assert q._reducing_quotes(0.50, None, 20.0, 0.40) == []
    assert q._reducing_quotes(None, 0.50, -20.0, 0.40) == []


def test_helpers_are_the_single_source_of_truth(monkeypatch):
    _live_band(monkeypatch)
    assert q._ok_entry_price(0.99) is False and q._ok_exit_price(0.99) is True
    assert q._ok_entry_price(0.02) is False and q._ok_exit_price(0.02) is True
    assert q._ok_exit_price(1.00) is False, "venue ceiling"
    assert q._ok_exit_price(0.00) is False, "venue floor"
    assert q._ok_exit_price(None) is False
    assert q._ok_entry_price(None) is False


# ---- STOP-flatten uses the same bounds ------------------------------------------------

def test_stop_flatten_rests_an_offset_at_099(monkeypatch):
    # _flatten_all is what runs while the STOP sentinel is present. It logged
    # "reducing side unpriceable" on exactly this shape.
    _live_band(monkeypatch)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", False)
    monkeypatch.setattr(q, "public_get",
                        lambda p: {"orderbook_fp": {"yes_dollars": [["0.99", "5000"]],
                                                    "no_dollars": []}})
    c = MockClient(mode="live", resting=[],
                   positions=[{"ticker": "KXAAAGASW-26JUL27-4.080", "position_fp": "-20.00",
                               "market_exposure_dollars": "1.00"}])
    q._flatten_all(c)
    assert c.created, "the STOP flatten must rest a reducing offset at the 0.99 touch"
    assert c.created[0]["side"] == "yes"
    assert abs(c.created[0]["price"] - 0.99) < 1e-9
