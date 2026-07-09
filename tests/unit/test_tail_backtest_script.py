"""Unit tests for scripts/backtest_tail_leaderboard.py (pure math + SQL shape).

The script is a read-only VPS analysis tool; its DB path cannot run here.
These tests lock the decision-bearing pure functions — the 2026-07-09 review
found the go/no-go line itself (per-slice coverage gating, lag-fill validity,
edge units) was where the bugs lived, so each confirmed fix gets a regression
test: strictly-after-print fills, fee-scaled probability-point edge, paired
tailability tax, per-slice verdict gating, and the LIMIT-sentinel SQL shape.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import scripts.backtest_tail_leaderboard as tb


# ── outcome mapping ──────────────────────────────────────────────────────────
def test_outcome_yes_token_win_and_loss():
    base = {"token_id": "T1", "yes_token_id": "T1", "no_token_id": "T2", "side": "YES"}
    assert tb._outcome({**base, "resolution": "YES"}) == 1.0
    assert tb._outcome({**base, "resolution": "NO"}) == 0.0


def test_outcome_no_token_and_side_fallback_and_unmappable():
    no_tok = {"token_id": "T2", "yes_token_id": "T1", "no_token_id": "T2",
              "side": "NO", "resolution": "NO"}
    assert tb._outcome(no_tok) == 1.0
    fallback = {"token_id": "TX", "yes_token_id": None, "no_token_id": None,
                "side": "NO", "resolution": "YES"}
    assert tb._outcome(fallback) == 0.0
    assert tb._outcome({"token_id": "TX", "side": "", "resolution": "YES",
                        "yes_token_id": None, "no_token_id": None}) is None
    assert tb._outcome({"token_id": "T1", "side": "YES", "resolution": None,
                        "yes_token_id": "T1", "no_token_id": "T2"}) is None


# ── units (2026-07-09 fix: fee scales by price in pts; ret/$ separate) ───────
def test_edge_points_scales_fee_by_price():
    assert abs(tb.edge_points(1.0, 0.60, 0.02) - (1.0 - 0.60 - 0.012)) < 1e-12
    assert abs(tb.edge_points(0.0, 0.30, 0.02) - (-0.30 - 0.006)) < 1e-12


def test_ret_per_dollar_fixed_notional_view():
    assert abs(tb.ret_per_dollar(1.0, 0.50, 0.02) - (1.0 - 0.02)) < 1e-12
    assert abs(tb.ret_per_dollar(0.0, 0.50, 0.02) - (-1.0 - 0.02)) < 1e-12


# ── lag-fill validity (2026-07-09 fix: strictly after the whale's print) ─────
def test_fill_is_lagged_rejects_at_or_before_print():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    assert not tb.fill_is_lagged(t0, t0)                       # the print itself
    assert not tb.fill_is_lagged(t0 - timedelta(seconds=3), t0)  # pre-impact
    assert tb.fill_is_lagged(t0 + timedelta(microseconds=1), t0)


# ── clustering + bootstrap ───────────────────────────────────────────────────
def test_per_market_edges_clusters_legs():
    me = tb.per_market_edges([("M", 0.1), ("M", 0.3), ("N", -0.2)])
    assert sorted(round(x, 9) for x in me) == [-0.2, 0.2]


def test_boot_p_edge_signal_null_degenerate():
    import random
    rng = random.Random(0)
    p_pos, _ = tb.boot_p_edge([rng.gauss(0.05, 0.02) for _ in range(40)], 800, 1)
    p_null, _ = tb.boot_p_edge([rng.gauss(0.0, 0.05) for _ in range(40)], 800, 2)
    assert p_pos > 0.95
    assert 0.25 < p_null < 0.75
    assert tb.boot_p_edge([0.1], 800, 3)[0] == 0.0            # n<2 → no evidence
    assert tb.boot_p_edge([0.1, 0.1, 0.1], 800, 4)[0] == 0.0  # zero variance
    assert tb.boot_p_edge([], 800, 5)[0] == 0.0


# ── paired tailability tax (2026-07-09 fix: same covered rows, both prices) ──
def test_paired_tax_same_signal_set():
    tax = tb.paired_tax([("M", 0.32, 0.38), ("N", 0.10, 0.20)])
    assert abs(tax - 0.08) < 1e-12


def test_paired_tax_empty_is_nan():
    import math
    assert math.isnan(tb.paired_tax([]))


# ── per-slice verdict gating (2026-07-09 fix: slice rho, not corpus rho) ─────
def test_verdict_for_matrix():
    assert tb.verdict_for(10, 0.99, 50, 0.60, 0.95, 30, 0.40) == "PASS*"
    assert tb.verdict_for(10, 0.99, 50, 0.10, 0.95, 30, 0.40) == "low-cov"
    assert tb.verdict_for(10, 0.99, 5, 0.60, 0.95, 30, 0.40) == "thin"
    assert tb.verdict_for(0, 0.99, 50, 1.00, 0.95, 30, 0.40) == "ceiling"
    assert tb.verdict_for(10, 0.50, 50, 0.60, 0.95, 30, 0.40) == "fail"


def test_verdict_low_cov_checked_before_thin():
    # A slice failing both gates reports coverage first (the more fundamental
    # data problem); ordering is part of the printed contract.
    assert tb.verdict_for(10, 0.99, 5, 0.10, 0.95, 30, 0.40) == "low-cov"


# ── category bucketing ───────────────────────────────────────────────────────
def test_bucket_category():
    assert tb.bucket_category("Crypto") == "crypto"
    assert tb.bucket_category("NBA") == "sports"
    assert tb.bucket_category("CS2 Majors") == "esports"
    assert tb.bucket_category("US Election") == "politics"
    assert tb.bucket_category("") == "unknown"
    assert tb.bucket_category(None) == "unknown"
    assert tb.bucket_category("Weather") == "other"


# ── SQL shape (LIMIT sentinel + optional predicates format cleanly) ──────────
def test_signals_sql_formats_with_and_without_bounds():
    bare = tb._SIGNALS_SQL.format(stage="", since="", until="")
    assert "LIMIT :cap1" in bare
    assert "DISTINCT ON (r.trader_address, r.market_id)" in bare
    assert "rejection_stage" in bare  # stage mix is SELECTed for the report
    bound = tb._SIGNALS_SQL.format(
        stage="AND r.rejection_stage = :stage",
        since="AND r.event_time >= :since",
        until="AND r.event_time < :until",
    )
    assert ":stage" in bound and ":since" in bound and ":until" in bound
