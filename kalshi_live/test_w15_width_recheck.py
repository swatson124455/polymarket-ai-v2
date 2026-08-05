"""W15 — the width gate is CONTINUOUS for unfilled resting quotes (pin, no code change).

Investigated after the 2026-08-05 loss: the selection gate at quoter:2636-2644 runs every
cycle whenever the market is ~flat, so a book that was tight at entry and widens later has
its resting (unfilled) accumulating quotes returned as [] — which the standing diff then
cancels. The exposure that remains is only (a) the intra-cycle window and (b) the fill
instant itself (a taker lifting a resting quote precedes any width signal) — neither is
closable by width polling; size (W4/W7) is the mitigation for those.

This file pins the continuous property explicitly so a future refactor that turns the
gate into an entry-only check fails loudly.
"""
import maker_kalshi_quoter as q


def _mkt():
    return {"target": 100, "end": "2199-01-01T00:00:00Z", "ticker": "KXW15-26AUG10-T1",
            "usd_day": 100.0, "life_min": 1440.0}


def test_widened_book_pulls_resting_unfilled_quotes(monkeypatch):
    monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 8)
    # we are RESTING both sides (own != empty) with ZERO fills -> inv still flat.
    own = {"yes": 25.0, "no": 25.0}
    # book has widened to 20 ticks around our (now stale) quotes
    yl = [["0.40", "500"]]
    nl = [["0.40", "500"]]
    quotes = q.desired_quotes(_mkt(), yl, nl, q.utcnow(), own=own, inv=0.0)
    assert quotes == [], ("a widened book must return NO desired quotes while flat — "
                          "the standing diff cancels the stale resting orders")


def test_widened_book_keeps_the_reducing_side_when_holding(monkeypatch):
    monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 8)
    # HOLDING inventory: the gate must NOT strand us — de-risk is never blocked.
    quotes = q.desired_quotes(_mkt(), [["0.40", "500"]], [["0.40", "500"]],
                              q.utcnow(), own={"yes": 0.0, "no": 0.0}, inv=25.0)
    assert quotes, "holding inventory in a wide book must still rest a reducing quote"
    assert all(o.get("reason") == "unwind" for o in quotes)
