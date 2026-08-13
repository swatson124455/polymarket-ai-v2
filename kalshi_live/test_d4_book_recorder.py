"""Tests for the D4 recorder's pure parsing/row-shaping functions.

Sample orderbook_fp payload follows the venue shape verified across the lane
(quoter _touch/_levels, D2 census reads): {"yes_dollars": [[price,size]..],
"no_dollars": [[price,size]..]}, both sides BIDS in their own basis, values as
strings; yes_ask = 1 - best_no_bid.
"""
import kalshi_d4_book_recorder as d4


# Captured-shape sample: yes bids 0.40..0.43, no bids 0.53..0.56 (yes asks 0.44..0.47)
SAMPLE_OB = {
    "yes_dollars": [["0.40", "250"], ["0.41", "120"], ["0.42", "80"],
                    ["0.43", "55"], ["0.37", "500"], ["0.39", "60"]],
    "no_dollars": [["0.53", "90"], ["0.54", "110"], ["0.55", "40"],
                   ["0.56", "300"], ["0.50", "999"]],
}


# ---------------- parse_levels ----------------

def test_parse_levels_strings_to_floats():
    lv = d4.parse_levels([["0.43", "55"], ["0.40", "250"]])
    assert lv == [(0.43, 55.0), (0.40, 250.0)]


def test_parse_levels_skips_malformed_and_zero_size():
    lv = d4.parse_levels([["x", "5"], ["0.10"], None, ["0.20", "0"], ["0.30", "7"]])
    assert lv == [(0.30, 7.0)]


def test_parse_levels_empty():
    assert d4.parse_levels(None) == []
    assert d4.parse_levels([]) == []


# ---------------- book_row ----------------

def test_book_row_touch_spread_mid():
    r = d4.book_row("2026-08-13T12:00:00Z", "KXTEST-1", SAMPLE_OB)
    assert r["ticker"] == "KXTEST-1"
    assert r["yes_bid"] == 0.43            # best yes bid
    assert r["yes_ask"] == 0.44            # 1 - best no bid (0.56)
    assert r["spread_c"] == 1.0
    assert r["mid"] == 0.435


def test_book_row_depth_within_3_ticks():
    r = d4.book_row("t", "T", SAMPLE_OB)
    # bids within [0.40, 0.43], sorted best-first; 0.37 and 0.39 excluded
    assert r["bid_depth"] == [[0.43, 55.0], [0.42, 80.0], [0.41, 120.0], [0.40, 250.0]]
    # asks (yes basis) within [0.44, 0.47], sorted best-first; 0.50 (no bid) excluded
    assert r["ask_depth"] == [[0.44, 300.0], [0.45, 40.0], [0.46, 110.0], [0.47, 90.0]]


def test_book_row_empty_side_gives_nulls():
    r = d4.book_row("t", "T", {"yes_dollars": [["0.43", "10"]], "no_dollars": []})
    assert r["yes_bid"] == 0.43
    assert r["yes_ask"] is None
    assert r["spread_c"] is None and r["mid"] is None
    assert r["bid_depth"] == [[0.43, 10.0]] and r["ask_depth"] == []


def test_book_row_fully_empty_book():
    r = d4.book_row("t", "T", {})
    assert r["yes_bid"] is None and r["yes_ask"] is None
    assert r["bid_depth"] == [] and r["ask_depth"] == []


# ---------------- trades watermark dedup ----------------

def _tr(tid, iso):
    return {"trade_id": tid, "created_time": iso, "count": 5, "yes_price": 43}


def test_dedupe_first_capture_takes_all_sorted():
    trades = [_tr("b", "2026-08-13T10:00:05Z"), _tr("a", "2026-08-13T10:00:01Z")]
    new, wm = d4.dedupe_trades(trades, None)
    assert [t["trade_id"] for t in new] == ["a", "b"]
    assert wm["last_ts"] == d4.trade_ts(trades[0])
    assert wm["ids"] == ["b"]


def test_dedupe_drops_already_seen():
    t1 = _tr("a", "2026-08-13T10:00:01Z")
    t2 = _tr("b", "2026-08-13T10:00:05Z")
    _, wm = d4.dedupe_trades([t1, t2], None)
    # refetch returns the same rows plus one new
    t3 = _tr("c", "2026-08-13T10:00:09Z")
    new, wm2 = d4.dedupe_trades([t1, t2, t3], wm)
    assert [t["trade_id"] for t in new] == ["c"]
    assert wm2["ids"] == ["c"]


def test_dedupe_boundary_second_same_ts_different_id():
    t1 = _tr("a", "2026-08-13T10:00:05Z")
    _, wm = d4.dedupe_trades([t1], None)
    t2 = _tr("b", "2026-08-13T10:00:05Z")     # same second, unseen id
    new, wm2 = d4.dedupe_trades([t1, t2], wm)
    assert [t["trade_id"] for t in new] == ["b"]
    assert sorted(wm2["ids"]) == ["a", "b"]   # boundary ids accumulate
    # third pass: nothing new
    new3, wm3 = d4.dedupe_trades([t1, t2], wm2)
    assert new3 == [] and wm3 == wm2


def test_dedupe_intra_batch_duplicate_ids():
    t1 = _tr("a", "2026-08-13T10:00:05Z")
    new, _ = d4.dedupe_trades([t1, dict(t1)], None)
    assert len(new) == 1


def test_dedupe_empty_batch_keeps_watermark():
    wm_in = {"last_ts": 100, "ids": ["x"]}
    new, wm = d4.dedupe_trades([], wm_in)
    assert new == [] and wm == {"last_ts": 100, "ids": ["x"]}


# ---------------- ticker cap ----------------

def test_cap_tickers_under_cap():
    kept, dropped = d4.cap_tickers(["A", "B"])
    assert kept == ["A", "B"] and dropped == 0


def test_cap_tickers_over_cap_first_40():
    raw = [f"T{i}" for i in range(45)]
    kept, dropped = d4.cap_tickers(raw)
    assert kept == raw[:40] and dropped == 5


def test_cap_tickers_filters_non_strings():
    kept, dropped = d4.cap_tickers(["A", None, 3, "", "B"])
    assert kept == ["A", "B"] and dropped == 0
