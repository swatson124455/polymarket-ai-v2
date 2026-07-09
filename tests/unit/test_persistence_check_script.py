"""Unit tests for scripts/check_trader_persistence.py (pure stats + SQL shape).

Locks the 2026-07-09 review fixes: the reworked slice_verdict contract (no
silently discarded underpowered-but-significant cutoffs; SIGNIFICANT-BUT-SMALL
as its own outcome), the 25/50/75 auto-cutoff quantiles as documented, the
estimand-faithful first-entry SQL (token filter AFTER selection, UNION ALL
equi-joins, LIMIT sentinel), and the permutation machinery's basic sanity.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

import scripts.check_trader_persistence as cp


# ── rank/correlation machinery ───────────────────────────────────────────────
def test_avg_ranks_ties_share_block_mean():
    assert cp._avg_ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_perfect_and_inverse():
    assert abs(cp.spearman([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-12
    assert abs(cp.spearman([1, 2, 3, 4], [40, 30, 20, 10]) + 1.0) < 1e-12
    assert math.isnan(cp.spearman([1.0], [2.0]))


def test_directional_lift_and_tercile_gap_edges():
    assert math.isnan(cp.directional_lift([1.0, 2.0], [0.1, 0.2]))  # no e1<=0 group
    lift = cp.directional_lift([1, 1, -1, -1], [1, 1, -1, 1])
    assert abs(lift - 0.5) < 1e-12  # P=1.0 vs P=0.5
    assert math.isnan(cp.tercile_gap([1, 2, 3], [1, 2, 3]))  # n<6
    gap = cp.tercile_gap([1, 2, 3, 4, 5, 6], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    assert abs(gap - (0.55 - 0.15)) < 1e-12


def test_perm_detects_signal_and_bounds_p():
    rng = random.Random(7)
    latent = [rng.gauss(0, 0.1) for _ in range(80)]
    e1 = [x + rng.gauss(0, 0.03) for x in latent]
    e2 = [0.7 * x + rng.gauss(0, 0.03) for x in latent]
    perm = cp._Perm(e1, e2, 400, seed=11)
    p = perm.pvalue("spearman")
    assert perm.obs["spearman"] > 0.5
    assert 0.0 < p <= 1.0 and p < 0.05


# ── period pairing ───────────────────────────────────────────────────────────
def test_pair_periods_requires_min_events_both_sides():
    t0, t1 = datetime(2026, 1, 1), datetime(2026, 6, 1)
    ents = ([("A", t0, 0.2)] * 3 + [("A", t1, 0.15)] * 3
            + [("B", t0, -0.1)] * 3 + [("B", t1, -0.08)] * 3
            + [("C", t0, 0.05)])
    e1, e2, n = cp.pair_periods(ents, datetime(2026, 3, 1), min_events=3)
    assert n == 2
    assert abs(sorted(e1)[1] - 0.2) < 1e-12 and abs(sorted(e2)[1] - 0.15) < 1e-12


def test_auto_cutoffs_are_interior_quartiles_for_n3():
    # 2026-07-09 doc fix: (i+1)/(n+1) → 25/50/75 for n=3 (NOT 33/50/67).
    times = [datetime(2026, 1, 1) + timedelta(hours=i) for i in range(100)]
    cuts = cp.auto_cutoffs(times, 3)
    assert cuts == [times[25], times[50], times[75]]
    assert cp.auto_cutoffs([], 3) == []


# ── slice_verdict contract (reworked 2026-07-09) ─────────────────────────────
def _c(n, sp, p):
    return {"n": n, "sp": sp, "sp_p": p}


def test_verdict_persistent_needs_all_powered_cutoffs():
    v, _ = cp.slice_verdict([_c(50, 0.3, 0.01), _c(50, 0.3, 0.01)], 0.05, 0.10, 20)
    assert v == "PERSISTENT"
    # 2-of-3 significant is MIXED, matching the printed criterion text.
    v, _ = cp.slice_verdict(
        [_c(50, 0.3, 0.01), _c(50, 0.3, 0.01), _c(50, 0.0, 0.9)], 0.05, 0.10, 20)
    assert v == "MIXED"


def test_verdict_significant_but_small_is_not_null():
    v, note = cp.slice_verdict([_c(50, 0.05, 0.01), _c(50, 0.3, 0.01)], 0.05, 0.10, 20)
    assert v == "SIGNIFICANT-BUT-SMALL"
    assert "rho" in note


def test_verdict_null_only_when_everything_is_quiet():
    v, _ = cp.slice_verdict([_c(50, 0.0, 0.8), _c(50, 0.0, 0.7)], 0.05, 0.10, 20)
    assert v == "NULL"
    # An underpowered-but-significant cutoff blocks NULL (was silently discarded).
    v, note = cp.slice_verdict(
        [_c(50, 0.0, 0.8), _c(50, 0.0, 0.7), _c(5, 0.5, 0.01)], 0.05, 0.10, 20)
    assert v == "MIXED"
    assert "significant" in note


def test_verdict_underpowered_reports_excluded_significance():
    v, note = cp.slice_verdict([_c(5, 0.5, 0.01)], 0.05, 0.10, 20)
    assert v == "UNDERPOWERED"
    assert "significant" in note


# ── SQL shape (2026-07-09: UNION ALL equi-joins, token filter after firsts,
#    LIMIT sentinel, optional time bounds) ────────────────────────────────────
def test_trades_sql_shape():
    bare = cp._TRADES_SQL.format(since_t="", until_t="")
    assert "UNION ALL" in bare
    assert "LIMIT :cap1" in bare
    assert "DISTINCT ON (user_address, mkey)" in bare
    # the OR-join must be gone (planner-hostile) …
    assert "OR CAST(m.id AS TEXT)" not in bare
    # … and token mappability must NOT filter rows before first-entry selection
    where_blocks = bare.split("firsts AS")[0]
    assert "token_id IN" not in where_blocks
    bound = cp._TRADES_SQL.format(since_t="AND t.timestamp >= :since",
                                  until_t="AND t.timestamp < :until")
    assert bound.count(":since") == 2 and bound.count(":until") == 2  # both branches


def test_rejected_sql_shape():
    bare = cp._REJECTED_SQL.format(stage_r="", since_r="", until_r="")
    assert "LIMIT :cap1" in bare
    assert "DISTINCT ON (r.trader_address, r.market_id)" in bare
    bound = cp._REJECTED_SQL.format(stage_r="AND r.rejection_stage = :stage",
                                    since_r="AND r.event_time >= :since",
                                    until_r="AND r.event_time < :until")
    assert ":stage" in bound and ":since" in bound and ":until" in bound
