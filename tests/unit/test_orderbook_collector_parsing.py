"""parse_book_metrics must read the BEST levels regardless of feed sort order.

Regression test for the worst-of-book bug (found 2026-07-14): the raw CLOB
/book response sorts bids ASCENDING and asks DESCENDING, so the old
``bids[0]/asks[0]`` access recorded the worst levels — every row of
orderbook_snapshots since 2026-04-13 has spread ≈ 0.998 and mid ≈ 0.5.
"""
import pytest

from scripts.orderbook_collector import _clean_levels, parse_book_metrics


# Shape of the real feed, verified live 2026-07-14 (Argentina WC token):
# bids ascending (worst first), asks descending (worst first).
RAW_FEED_BOOK = {
    "bids": [
        {"price": "0.001", "size": "1338810.4"},
        {"price": "0.10", "size": "500"},
        {"price": "0.174", "size": "36803.42"},   # best bid, LAST
    ],
    "asks": [
        {"price": "0.999", "size": "19406355.19"},
        {"price": "0.30", "size": "200"},
        {"price": "0.175", "size": "41527.77"},   # best ask, LAST
    ],
}


class TestBestLevelSelection:
    def test_raw_feed_order_reads_true_touch(self):
        m = parse_book_metrics(RAW_FEED_BOOK)
        assert m["best_bid"] == pytest.approx(0.174)
        assert m["best_ask"] == pytest.approx(0.175)
        assert m["spread"] == pytest.approx(0.001)
        assert m["mid_price"] == pytest.approx(0.1745)

    def test_order_independent(self):
        """Same book pre-sorted best-first must give identical metrics."""
        sorted_book = {
            "bids": list(reversed(RAW_FEED_BOOK["bids"])),
            "asks": list(reversed(RAW_FEED_BOOK["asks"])),
        }
        assert parse_book_metrics(sorted_book) == parse_book_metrics(RAW_FEED_BOOK)

    def test_old_behavior_would_fail_this(self):
        """The worst levels (what the old code stored) are NOT the answer."""
        m = parse_book_metrics(RAW_FEED_BOOK)
        assert m["best_bid"] != pytest.approx(0.001)
        assert m["best_ask"] != pytest.approx(0.999)
        assert m["spread"] < 0.15  # 43.8M poisoned rows all had ~0.998


class TestPartialAndEmptyBooks:
    def test_one_sided_book_bids_only(self):
        m = parse_book_metrics({"bids": RAW_FEED_BOOK["bids"], "asks": []})
        assert m["best_bid"] == pytest.approx(0.174)
        assert m["best_ask"] is None
        assert m["spread"] is None
        assert m["mid_price"] is None

    def test_empty_book_returns_none(self):
        assert parse_book_metrics({"bids": [], "asks": []}) is None
        assert parse_book_metrics({}) is None
        assert parse_book_metrics(None) is None

    def test_all_junk_returns_none(self):
        junk = {"bids": [{"price": "abc", "size": "1"}, {"price": "1.5", "size": "9"}],
                "asks": [{"price": "0.5", "size": "0"}, {"size": "3"}]}
        assert parse_book_metrics(junk) is None

    def test_junk_level_does_not_poison_snapshot(self):
        """One malformed level is dropped; the rest of the book still parses.
        (Old code raised inside the depth loop and dropped the whole row.)"""
        book = {
            "bids": [{"price": "0.10", "size": "50"}, {"price": None, "size": "1"}],
            "asks": [{"price": "0.12", "size": "60"}],
        }
        m = parse_book_metrics(book)
        assert m["best_bid"] == pytest.approx(0.10)
        assert m["best_ask"] == pytest.approx(0.12)


class TestDepthAndImbalance:
    def test_depth_uses_true_mid(self):
        """Depth-within-pct must bracket the real mid, not the phantom 0.5."""
        book = {
            "bids": [{"price": "0.20", "size": "100"}, {"price": "0.19", "size": "40"}],
            "asks": [{"price": "0.21", "size": "70"}, {"price": "0.50", "size": "999"}],
        }
        m = parse_book_metrics(book)
        assert m["mid_price"] == pytest.approx(0.205)
        # 5% of mid = ±0.01025 → includes 0.20 bid & 0.21 ask, excludes 0.19 & 0.50
        assert m["bid_depth_5pct"] == pytest.approx(100)
        assert m["ask_depth_5pct"] == pytest.approx(70)

    def test_imbalance_top5_is_best5(self):
        """With raw ascending bids, the old [:5] summed the WORST five bids.
        Construct a book where that flips the imbalance sign."""
        book = {
            # ascending feed order: 6 tiny bids then 5 huge best bids
            "bids": [{"price": f"0.0{i+1}", "size": "1"} for i in range(6)]
            + [{"price": p, "size": "1000"} for p in ("0.30", "0.31", "0.32", "0.33", "0.34")],
            # descending feed order: worst ask first
            "asks": [{"price": "0.99", "size": "1"},
                     {"price": "0.36", "size": "100"}],
        }
        m = parse_book_metrics(book)
        # best-5 bids = 5000 vs best-5 asks = 101 → strongly positive
        assert m["imbalance"] > 0.9

    def test_clean_levels_sort_direction(self):
        lv = [{"price": "0.2", "size": "1"}, {"price": "0.4", "size": "1"}, {"price": "0.3", "size": "1"}]
        assert [x["price"] for x in _clean_levels(lv, best_first_desc=True)] == [0.4, 0.3, 0.2]
        assert [x["price"] for x in _clean_levels(lv, best_first_desc=False)] == [0.2, 0.3, 0.4]
