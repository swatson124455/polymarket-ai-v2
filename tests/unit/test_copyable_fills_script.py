"""Unit tests for scripts/backtest_copyable_fills.py pure core.

The decision-bearing pieces: fill edge charges the ask (below the whale-price
ceiling), the paired spread tax is positive, verdict gating separates NO-BOOK
(unverifiable) from FILL-KILLED (real edge eaten), and the reused audited
coarse model actually crosses the spread on a BUY.
"""
from __future__ import annotations

import scripts.backtest_copyable_fills as cf


def test_fill_edge_below_ceiling():
    ef = cf.fill_edge(1.0, 0.66, 0.02)
    ec = cf.ceiling_edge(1.0, 0.60, 0.02)
    assert ef < ec
    assert abs(ec - (1.0 - 0.60 - 0.012)) < 1e-9
    assert abs(ef - (1.0 - 0.66 - 0.0132)) < 1e-9


def test_spread_tax_paired_positive():
    rows = [("M", cf.fill_edge(1.0, 0.66, 0.02), cf.ceiling_edge(1.0, 0.60, 0.02)),
            ("N", cf.fill_edge(0.0, 0.34, 0.02), cf.ceiling_edge(0.0, 0.30, 0.02))]
    assert cf.spread_tax(rows) > 0
    import math
    assert math.isnan(cf.spread_tax([]))


def test_trader_verdict_matrix():
    assert cf.trader_verdict(0.99, 40, 0.60, 0.95, 30, 0.40) == "SURVIVES"
    assert cf.trader_verdict(0.99, 40, 0.10, 0.95, 30, 0.40) == "NO-BOOK"
    assert cf.trader_verdict(0.99, 5, 0.60, 0.95, 30, 0.40) == "THIN"
    assert cf.trader_verdict(0.50, 40, 0.60, 0.95, 30, 0.40) == "FILL-KILLED"
    # coverage is checked before market count — no-book is the more basic failure
    assert cf.trader_verdict(0.99, 5, 0.10, 0.95, 30, 0.40) == "NO-BOOK"


def test_coarse_model_crosses_spread():
    coarse_fill = cf._import_coarse_fill()
    row = {"mid_price": 0.50, "best_ask": 0.52, "best_bid": 0.48,
           "ask_depth_1pct": 1000, "ask_depth_5pct": 5000,
           "bid_depth_1pct": 1000, "bid_depth_5pct": 5000}
    fr = coarse_fill(row, "BUY", 50.0)
    assert fr is not None and fr.price >= 0.52          # never fills below the ask
    # partial fill when size exceeds even the 5% bucket
    thin = {**row, "ask_depth_1pct": 10, "ask_depth_5pct": 20}
    fr2 = coarse_fill(thin, "BUY", 1000.0)
    assert fr2 is not None and fr2.fill_fraction < 1.0


def test_load_addresses_only_primary(tmp_path):
    blob = {"qualified": {
        "0xVOL": {"src": "VOL", "truncated": False},
        "0xPNL": {"src": "PNL", "truncated": False},
        "0xVOLtrunc": {"src": "PNL+VOL", "truncated": True},
    }}
    p = tmp_path / "cop.json"
    p.write_text(__import__("json").dumps(blob))

    class A:
        traders = ""
        from_json = str(p)
        only_primary = True
    assert cf.load_addresses(A()) == ["0xVOL"]
    A.only_primary = False
    assert set(cf.load_addresses(A())) == {"0xVOL", "0xPNL", "0xVOLtrunc"}
