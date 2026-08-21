"""Pins for the _rest_maker_offset response-shape fix (2026-08-21 incident).

Venue evidence: 49 mk-rerest exits RESTED on the live book (order history, read
2026-08-21T14:46:50Z) while the caller printed "re-rest FAILED — no working exit"
every cycle. Root cause: the live V2 create response carries the order at top
level; the old parse (`else {}`) lost the id and returned None — a false naked-
position alarm. create_order_v2 raises on a GTC order that fails to rest, so a
non-raising create means the exit IS resting.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

BOOK = {"orderbook_fp": {"yes_dollars": [["0.90", "400"], ["0.98", "2000"]],
                         "no_dollars": []}}         # the incident's exact shape


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def create_quote(self, ticker, side, price, cnt, post_only=True, client_order_id=None):
        self.calls.append((ticker, side, price, cnt, post_only))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def _run(monkeypatch, resp):
    monkeypatch.setattr(q, "public_get", lambda path: BOOK)
    c = _Client(resp)
    # pos < 0 (long NO / short yes): exit = rest a YES bid — the incident's position
    r = q._rest_maker_offset(c, "KXTEST-01", -10.0, 0.01, "rerest")
    return r, c


def test_top_level_shape_is_success(monkeypatch):
    """The live V2 shape (order fields at top level) must be treated as RESTED."""
    r, c = _run(monkeypatch, {"order_id": "abc-123", "status": "resting"})
    assert r == "abc-123"
    assert c.calls, "an exit order must have been placed"


def test_wrapped_shape_still_works(monkeypatch):
    r, _ = _run(monkeypatch, {"order": {"order_id": "wrapped-1", "status": "resting"}})
    assert r == "wrapped-1"


def test_unparseable_but_nonraising_is_still_success(monkeypatch):
    """create didn't raise -> the order rests (client contract). A parse miss must
    never be turned back into a false 'no working exit' alarm."""
    r, _ = _run(monkeypatch, {"unexpected": "shape"})
    assert r, "must return truthy even when no id can be parsed"


def test_create_raise_is_a_real_failure(monkeypatch):
    r, _ = _run(monkeypatch, RuntimeError("order not resting: status=rejected"))
    assert r is None


def test_empty_exit_side_book_prices_off_present_side(monkeypatch):
    """Incident book: NO side empty, YES bids present — the exit (a YES bid for a
    short-yes position) must still be priceable off the present side."""
    r, c = _run(monkeypatch, {"order_id": "x", "status": "resting"})
    assert r == "x"
    _, side, price, _, post_only = c.calls[0]
    assert side == "yes" and post_only
    assert 0.01 <= price <= 0.99
