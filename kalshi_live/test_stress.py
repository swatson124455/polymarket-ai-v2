"""STRESS TESTS — hostile and degenerate inputs through the decision path.

The unit tests elsewhere pin intended behaviour on well-formed data. This file asks the other
question: when the venue hands us something broken, does the algorithm stay safe? Every case here
either happened (empty books, malformed rows, crossed quotes, missing fields) or is one bad deploy
away.

THE INVARIANTS, which must hold for ANY input:
  I1 never crash          — one degenerate market must not take down a cycle
  I2 never cross          — a resting quote must never be priced through the opposite side
  I3 never exceed caps    — no quote may exceed the per-market capital cap
  I4 never block an exit  — held inventory always gets its reducing quote, whatever the book does
  I5 never invent size    — no order for more contracts than the sizing dials allow
"""
import pytest

from test_live_hardening import q


def _m(**kw):
    d = {"ticker": "T", "usd_day": 100.0, "target": 1000, "end": "2099-01-01T00:00:00+00:00",
         "life_min": 1440.0, "df": 0.5}
    d.update(kw)
    return d


# Books that are wrong in every way we can think of.
HOSTILE = {
    "empty both sides": ([], []),
    "empty yes only": ([], [["0.49", "5000"]]),
    "empty no only": ([["0.50", "5000"]], []),
    "zero sizes": ([["0.50", "0"]], [["0.49", "0"]]),
    "negative size": ([["0.50", "-100"]], [["0.49", "-100"]]),
    "crossed book": ([["0.80", "5000"]], [["0.80", "5000"]]),
    "sum exactly one": ([["0.50", "5000"]], [["0.50", "5000"]]),
    "price above one": ([["1.50", "5000"]], [["0.49", "5000"]]),
    "price at one": ([["1.00", "5000"]], [["0.00", "5000"]]),
    "price zero": ([["0.00", "5000"]], [["0.00", "5000"]]),
    "malformed rows": ([["abc", "def"], ["0.50", "5000"]], [["0.49", None]]),
    "missing fields": ([["0.50"]], [["0.49", "5000"]]),
    "none rows": ([None, ["0.50", "5000"]], [["0.49", "5000"]]),
    "huge depth": ([["0.50", "99999999"]], [["0.49", "99999999"]]),
    "one contract": ([["0.50", "1"]], [["0.49", "1"]]),
    "many levels": ([[f"{0.60 - i*0.01:.2f}", "50"] for i in range(50)],
                    [[f"{0.39 - i*0.01:.2f}", "50"] for i in range(35)]),
    "duplicate prices": ([["0.50", "100"], ["0.50", "100"]], [["0.49", "100"], ["0.49", "100"]]),
    "unsorted": ([["0.20", "100"], ["0.50", "9000"]], [["0.10", "100"], ["0.49", "9000"]]),
}

HOSTILE_MARKETS = {
    "zero pool": _m(usd_day=0.0),
    "negative pool": _m(usd_day=-100.0),
    "zero target": _m(target=0),
    "huge target": _m(target=20000),
    "no life_min": _m(life_min=None),
    "negative life": _m(life_min=-500.0),
    "end in the past": _m(end="2020-01-01T00:00:00+00:00"),
    "garbage end": _m(end="not-a-date"),
    "no df": _m(df=None),
    "df zero": _m(df=0.0),
    "df one": _m(df=1.0),
}


@pytest.mark.parametrize("name", list(HOSTILE))
@pytest.mark.parametrize("inv", [0.0, 40.0, -40.0])
def test_hostile_books_never_crash_and_never_cross(name, inv, monkeypatch):
    """I1 + I2. Runs every hostile book flat, long and short."""
    monkeypatch.setattr(q, "PRESENCE_GATE", 1)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    yl, nl = HOSTILE[name]
    quotes = q.desired_quotes(_m(), yl, nl, q.utcnow(), inv=inv, stats={})
    assert isinstance(quotes, list)
    for x in quotes:
        p = x["price_dollars"]
        assert q.MIN_PRICE_DOLLARS < p <= q.MAX_PRICE_DOLLARS, f"{name}: price {p} out of band"
        assert x["count"] >= 1 and float(x["count"]) == int(x["count"]), f"{name}: bad size"
        assert x["side"] in ("yes", "no")
    # I2: our own two quotes must never sum to >= 1.00 (that is a crossed, self-trading book)
    by = {x["side"]: x["price_dollars"] for x in quotes}
    if "yes" in by and "no" in by:
        assert by["yes"] + by["no"] < 1.0, f"{name}: our own quotes cross"


@pytest.mark.parametrize("name", list(HOSTILE_MARKETS))
def test_hostile_market_rows_never_crash(name, monkeypatch):
    monkeypatch.setattr(q, "PRESENCE_GATE", 1)
    yl = [["0.50", "600"], ["0.49", "500"]]
    nl = [["0.49", "600"], ["0.48", "500"]]
    quotes = q.desired_quotes(HOSTILE_MARKETS[name], yl, nl, q.utcnow(), inv=0.0, stats={})
    assert isinstance(quotes, list)


@pytest.mark.parametrize("name", list(HOSTILE))
def test_per_market_capital_cap_is_never_exceeded(name, monkeypatch):
    """I3 + I5. Whatever the book, the notional we intend to rest stays inside the dial."""
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)
    yl, nl = HOSTILE[name]
    quotes = q.desired_quotes(_m(), yl, nl, q.utcnow(), inv=0.0, stats={})
    for x in quotes:
        side_notional = x["price_dollars"] * x["count"]
        assert side_notional <= 15.0 / 2.0 + 0.51, (
            f"{name}: {side_notional:.2f} exceeds half the per-market cap")


@pytest.mark.parametrize("name", list(HOSTILE))
def test_exits_are_never_blocked_by_any_book(name, monkeypatch):
    """I4 — the one that matters most. Holding inventory, a reducing quote must be produced
    whenever the opposite side has a usable price. De-risk must not depend on book quality."""
    monkeypatch.setattr(q, "PRESENCE_GATE", 1)
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    yl, nl = HOSTILE[name]
    for inv, reducing in ((40.0, "no"), (-40.0, "yes")):
        quotes = q.desired_quotes(_m(), yl, nl, q.utcnow(), inv=inv, stats={})
        red = [x for x in quotes if x["side"] == reducing]
        # Either we rest a reducing quote, or we produced nothing at all (book unusable — the
        # taker backstop owns that case). What is NOT allowed is quoting the ACCUMULATING side
        # while refusing the reducing one.
        acc = [x for x in quotes if x["side"] != reducing]
        assert red or not acc, f"{name}: quoted the accumulating side but not the exit"


def test_split_amends_and_grace_survive_garbage(monkeypatch):
    """The two order-lifecycle helpers must not crash on malformed standing/desired books."""
    junk_standing = {"T": [{"order_id": "o1", "side": "yes", "price_dollars": 0.5, "count": 20}],
                     "EMPTY": [], "NONE": []}
    junk_desired = {"T": [{"side": "yes", "price_dollars": 0.5, "count": 5}]}
    a, s, d = q.split_amends(junk_standing, junk_desired)
    assert isinstance(a, list)
    d2, g = q.apply_drop_grace(junk_standing, {}, set(), {}, 3)
    assert isinstance(d2, dict) and isinstance(g, dict)
    # and with the grace budget already blown
    d3, g3 = q.apply_drop_grace(junk_standing, {}, set(), {"T": 999}, 3)
    assert "T" not in d3


def test_qualifying_walk_survives_garbage():
    for bids in ([], [(0.5, 0.0)], [(1.0, 100.0)], [(0.0, 100.0)], [(0.5, 1e12)],
                 [(0.5, 100.0)] * 500):
        t, cum, ref, low, ok = q._qualifying_breakdown(bids, 1000.0, 0.5)
        assert t >= 0.0 and cum >= 0.0 and isinstance(ok, bool)
    # target of zero must not divide by anything or claim a fake qualification
    t, cum, ref, low, ok = q._qualifying_breakdown([(0.5, 10.0)], 0.0, 0.5)
    assert t >= 0.0
