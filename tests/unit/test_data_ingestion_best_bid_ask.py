"""_best_bid_ask must read the BEST levels from raw CLOB /book order.

Regression test for the strategy-2 midpoint bug (found 2026-07-14): the raw
feed sorts bids ASCENDING / asks DESCENDING, so the old ``bids[0]/asks[0]``
midpoint was ~(0.001+0.999)/2 = 0.5 regardless of the real price, and the
``0 <= mid <= 1`` guard happily wrote it into market_prices.
"""
import pytest

from base_engine.data.data_ingestion import _best_bid_ask


# Real feed shape, verified live 2026-07-14 (Argentina WC token).
RAW_BIDS = [{"price": "0.001", "size": "1338810"},
            {"price": "0.10", "size": "500"},
            {"price": "0.174", "size": "36803"}]   # best bid LAST
RAW_ASKS = [{"price": "0.999", "size": "19406355"},
            {"price": "0.30", "size": "200"},
            {"price": "0.175", "size": "41527"}]   # best ask LAST


def test_raw_feed_order_gives_true_touch():
    bb, ba = _best_bid_ask(RAW_BIDS, RAW_ASKS)
    assert bb == pytest.approx(0.174)
    assert ba == pytest.approx(0.175)
    # the midpoint the caller computes is the real one, not the phantom 0.5
    assert (bb + ba) / 2 == pytest.approx(0.1745)


def test_order_independent():
    assert _best_bid_ask(list(reversed(RAW_BIDS)), list(reversed(RAW_ASKS))) == \
        _best_bid_ask(RAW_BIDS, RAW_ASKS)


def test_single_level_sides_no_size_key():
    """Matches the existing strategy-2 mock fixture shape (no 'size' keys)."""
    bb, ba = _best_bid_ask([{"price": "0.65"}], [{"price": "0.67"}])
    assert (bb, ba) == (pytest.approx(0.65), pytest.approx(0.67))


def test_junk_levels_ignored():
    bids = [{"price": "abc"}, {"price": None}, {"price": "0.20"}, "garbage"]
    asks = [{"price": "1.5"}, {"price": "0"}, {"price": "0.22"}]
    assert _best_bid_ask(bids, asks) == (pytest.approx(0.20), pytest.approx(0.22))


def test_one_sided_or_empty_returns_none():
    assert _best_bid_ask([], RAW_ASKS) is None
    assert _best_bid_ask(RAW_BIDS, []) is None
    assert _best_bid_ask(None, None) is None
    assert _best_bid_ask([{"price": "abc"}], RAW_ASKS) is None
