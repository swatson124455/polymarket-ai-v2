"""Unit tests for mirror_v3/sizing.py — the A+D sizing rule as decided
(docs/MB_SIZING_OPTIONS.md decision record: tiers 2x/4x, cap 1.5x,
no malus, cold-start lock at <20 observations)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mirror_v3 import sizing as sz  # noqa: E402

A = "0xabc0000000000000000000000000000000000001"


def test_multiplier_tiers_as_decided():
    # median 100, 20 obs — the operator-set boundaries: 2x -> 1.25, 4x -> 1.5
    assert sz.conviction_multiplier(100.0, 100.0, 20) == (1.0, 1.0)
    assert sz.conviction_multiplier(199.0, 100.0, 20)[0] == 1.0
    assert sz.conviction_multiplier(200.0, 100.0, 20) == (1.25, 2.0)
    assert sz.conviction_multiplier(399.0, 100.0, 20)[0] == 1.25
    assert sz.conviction_multiplier(400.0, 100.0, 20) == (1.5, 4.0)
    assert sz.conviction_multiplier(40_000.0, 100.0, 20)[0] == 1.5  # hard cap
    # no malus below median
    assert sz.conviction_multiplier(1.0, 100.0, 20)[0] == 1.0


def test_multiplier_cold_start_locked():
    assert sz.conviction_multiplier(500.0, 100.0, 19) == (1.0, None)
    assert sz.conviction_multiplier(500.0, None, 0) == (1.0, None)
    assert sz.conviction_multiplier(500.0, 0.0, 50) == (1.0, None)


def test_trailing_medians_window_and_stats():
    tm = sz.TrailingMedians(window=3)
    tm.seed(A, [10.0, 20.0, 30.0, 40.0])       # window keeps last 3
    med, n = tm.stats(A)
    assert (med, n) == (30.0, 3)
    tm.observe(A, 1000.0)                       # rolls the window
    med, n = tm.stats(A)
    assert n == 3 and med == 40.0
    assert tm.stats("0xunknown") == (None, 0)
    tm.observe(A, -5.0)                         # non-positive ignored
    assert tm.stats(A)[1] == 3


def test_seed_from_cache_same_tx_aggregated(tmp_path):
    trades = [
        # one wager split into two fills in the same tx: 60 + 40 = 100
        {"side": "BUY", "timestamp": 1, "size": 100, "price": 0.6,
         "transactionHash": "0xT1"},
        {"side": "BUY", "timestamp": 1, "size": 100, "price": 0.4,
         "transactionHash": "0xT1"},
        # separate wagers
        {"side": "BUY", "timestamp": 2, "size": 50, "price": 0.5,
         "transactionHash": "0xT2"},
        {"side": "SELL", "timestamp": 3, "size": 999, "price": 0.5,
         "transactionHash": "0xT3"},          # sells never count
        {"side": "BUY", "timestamp": 4, "size": 10, "price": 0.5},  # no tx
    ]
    (tmp_path / f"{A}.json").write_text(json.dumps({"trades": trades}))
    tm = sz.seed_medians_from_cache(str(tmp_path), [A])
    med, n = tm.stats(A)
    assert n == 3                                # T1(agg), T2, no-tx row
    assert med == 25.0                           # wagers: 100, 25, 5
    # missing cache file -> cold start, not an error
    tm2 = sz.seed_medians_from_cache(str(tmp_path), ["0xnope"])
    assert tm2.stats("0xnope") == (None, 0)


def test_merge_same_tx_signals():
    s1 = {"tx": "0xT", "trader": A, "token_id": "7", "side": "BUY",
          "whale_price": 0.60, "whale_size_usd": 60.0}   # 100 tokens
    s2 = {"tx": "0xT", "trader": A, "token_id": "7", "side": "BUY",
          "whale_price": 0.70, "whale_size_usd": 70.0}   # 100 tokens
    other = {"tx": "0xU", "trader": A, "token_id": "7", "side": "BUY",
             "whale_price": 0.5, "whale_size_usd": 5.0}
    out = sz.merge_same_tx([s1, s2, other])
    assert len(out) == 2
    m = out[0]
    assert m["whale_size_usd"] == 130.0
    assert abs(m["whale_price"] - 0.65) < 1e-9   # volume-weighted
    assert out[1]["whale_size_usd"] == 5.0
