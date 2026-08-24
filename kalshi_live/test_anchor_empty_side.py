"""Pins for KALSHI_ANCHOR_EMPTY_SIDE (operator-named 2026-08-24).

Measured blocker it attacks: 65% of 5,592 checks on 08-24 failed gate_one_sided_book —
the safe extreme markets have no resting cheap side, so pairing (required for reward
scoring) is impossible. The anchor creates the missing side at ANCHOR_PRICE.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402

M = {"target": 100, "end": "2099-01-01T00:00:00Z", "ticker": "KXTEST-01"}


def _run(yl, nl, inv=0.0, flag=True, anchor=0.01):
    stats = {}
    old = (q.ANCHOR_EMPTY_SIDE, q.ANCHOR_PRICE, q.MIN_PRICE_DOLLARS, q.MAX_PRICE_DOLLARS)
    # the mode's LIVE price band (live.env 0.003/0.995) — code defaults (0.01/0.97)
    # refuse both the 0.98 rich ref and the 1c anchor, same as the earlier band pins
    q.ANCHOR_EMPTY_SIDE, q.ANCHOR_PRICE = flag, anchor
    q.MIN_PRICE_DOLLARS, q.MAX_PRICE_DOLLARS = 0.003, 0.995
    try:
        quotes = q.desired_quotes(M, yl, nl, q.utcnow(), inv=inv, stats=stats)
    finally:
        (q.ANCHOR_EMPTY_SIDE, q.ANCHOR_PRICE,
         q.MIN_PRICE_DOLLARS, q.MAX_PRICE_DOLLARS) = old
    return quotes, stats


def test_flag_off_is_byte_identical_gate():
    quotes, stats = _run([["0.98", "500"]], [], flag=False)
    assert quotes == [] and stats.get("gate_one_sided_book") == 1


def test_anchor_pairs_an_extreme_one_sided_book():
    # yes bids exist at 0.98 (extreme), NO side empty -> anchor the NO side at 1c
    quotes, stats = _run([["0.98", "500"]], [])
    assert stats.get("anchor_paired") == 1
    sides = {o["side"]: o for o in quotes}
    assert sides["yes"]["price_dollars"] == 0.98
    assert sides["no"]["price_dollars"] == 0.01
    assert all(o["count"] >= 1 for o in quotes)


def test_anchor_pairs_the_mirror_shape():
    # NO bids exist at 0.97 (yes-terms 0.03 = extreme), YES side empty -> anchor YES at 1c
    quotes, stats = _run([], [["0.97", "500"]])
    assert stats.get("anchor_paired") == 1
    sides = {o["side"]: o for o in quotes}
    assert sides["no"]["price_dollars"] == 0.97
    assert sides["yes"]["price_dollars"] == 0.01


def test_mid_range_one_sided_book_stays_refused():
    # present ref 0.45 in yes-terms = the near-strike class -> anchor must NOT fire
    quotes, stats = _run([["0.45", "500"]], [])
    assert quotes == [] and stats.get("gate_one_sided_book") == 1
    assert "anchor_paired" not in stats


def test_modal_099_touch_book_anchors_one_tick_inside():
    """THE modal live shape (245/245 refusals on 08-24): touch 0.99, empty other side.
    The pair must FIT by stepping the present side to 0.98 — never refuse, never cross."""
    quotes, stats = _run([["0.99", "500"]], [])
    assert stats.get("anchor_paired") == 1
    sides = {o["side"]: o for o in quotes}
    assert sides["yes"]["price_dollars"] == 0.98
    assert sides["no"]["price_dollars"] == 0.01
    assert sides["yes"]["price_dollars"] + sides["no"]["price_dollars"] < 1.0


def test_present_side_never_steps_up():
    # touch at 0.95 fits as-is with a 1c anchor -> join stays AT the touch
    quotes, stats = _run([["0.95", "500"]], [])
    assert stats.get("anchor_paired") == 1
    assert {o["side"]: o["price_dollars"] for o in quotes}["yes"] == 0.95


def test_holding_inventory_unchanged_reducing_path():
    quotes, stats = _run([["0.98", "500"]], [], inv=-10.0)
    assert "anchor_paired" not in stats and "gate_one_sided_book" not in stats
    assert quotes and all(o.get("reason") == "unwind" for o in quotes)


def test_both_sides_empty_never_anchors():
    quotes, stats = _run([], [])
    assert quotes == [] and "anchor_paired" not in stats


def test_shipped_default_is_off():
    if "KALSHI_ANCHOR_EMPTY_SIDE" not in os.environ:
        assert q.ANCHOR_EMPTY_SIDE is False
