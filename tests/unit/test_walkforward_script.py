"""Unit tests for scripts/walkforward_copy_traders.py pure core.

Locks the operator-agreed hire/fire/score contract (2026-07-10):
hire on lifetime record (markets + span + significance), fire only on
statistically convincing RECENT decay (past glory cannot shield bleed,
a cold month cannot convict a GOAT), grade only post-hire bets, review
grid locked to a data-independent anchor, and outcome knowledge gated
by resolved_at (no peeking at long-dated results).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import scripts.walkforward_copy_traders as wf


class Cfg:
    min_markets_hire = 25
    min_span_days = 60
    p_hire = 0.90
    fire_window_days = 90
    fire_min_markets = 10
    p_fire = 0.90
    n_boot_roster = 400


T0 = datetime(2025, 1, 1)


def rows(day0, n, mu, tag, seed, spread_days=60):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        ts = T0 + timedelta(days=day0 + (i * spread_days) // max(1, n))
        out.append({"_ts": ts, "known": ts, "marketId": f"m{tag}{i}",
                    "edge": rng.gauss(mu, 0.05), "cat": "sports"})
    return out


def test_goat_survives_mild_bad_month():
    goat = rows(0, 300, 0.08, "g", 1, 300) + rows(300, 12, -0.01, "gb", 2, 25)
    t = T0 + timedelta(days=330)
    assert wf.hire_ok(goat, t, Cfg, 7)
    assert not wf._fire(goat, t, Cfg, 7)


def test_bleeder_fired_despite_shiny_lifetime():
    bleeder = rows(0, 200, 0.10, "b", 3, 300) + rows(310, 45, -0.08, "bb", 4, 85)
    t = T0 + timedelta(days=400)
    assert wf.hire_ok(bleeder, t, Cfg, 8)      # lifetime alone would keep him
    assert wf._fire(bleeder, t, Cfg, 8)        # recent decay convicts anyway


def test_fire_needs_negative_mean_and_enough_recent_data():
    # positive recent mean can never fire, whatever the bootstrap says
    fine = rows(0, 300, 0.08, "f", 5, 300)
    assert not wf._fire(fine, T0 + timedelta(days=290), Cfg, 9)
    # fewer than fire_min_markets recent → cannot convict
    sparse = rows(0, 200, 0.10, "s", 6, 200) + rows(290, 5, -0.30, "sb", 7, 5)
    assert not wf._fire(sparse, T0 + timedelta(days=300), Cfg, 9)


def test_improver_hired_only_after_turnaround():
    imp = rows(0, 30, -0.05, "BAD", 10, 90) + rows(100, 60, 0.09, "GOOD", 11, 150)
    assert not wf.hire_ok(imp, T0 + timedelta(days=95), Cfg, 12)
    assert wf.hire_ok(imp, T0 + timedelta(days=260), Cfg, 12)


def test_hot_streak_blocked_by_span_rule():
    streak = rows(0, 30, 0.20, "hs", 13, 10)   # 30 great markets in 10 days
    assert not wf.hire_ok(streak, T0 + timedelta(days=20), Cfg, 14)


def test_walk_counts_only_post_hire_bets():
    imp = rows(0, 30, -0.05, "BAD", 15, 90) + rows(100, 60, 0.09, "GOOD", 16, 150)
    goat = rows(0, 300, 0.08, "g", 17, 300)
    dates = wf.review_dates(T0, T0 + timedelta(days=400), 30)
    res = wf.walk_forward({"I": imp, "G": goat}, dates, Cfg, 42)
    assert res["hires"] >= 2
    assert not any(b[0].startswith("mBAD") for b in res["bets"] if b[3] == "I")


def test_review_grid_locked_and_shiftable():
    d0 = wf.review_dates(T0, T0 + timedelta(days=100), 30)
    d1 = wf.review_dates(T0, T0 + timedelta(days=100), 30, shift_days=15)
    assert d0 and all((d - wf.GRID_ANCHOR).days % 30 == 0 for d in d0)
    assert d1 and all((d - wf.GRID_ANCHOR - timedelta(days=15)).days % 30 == 0 for d in d1)


def test_knowledge_time_gates_long_dated_outcomes():
    entry = T0
    late_resolution = T0 + timedelta(days=200)
    assert wf.known_time(entry, late_resolution) == late_resolution
    assert wf.known_time(entry, None) == entry
    # an unresolved-until-day-200 bet is NOT hire evidence at day 100
    e = {"_ts": entry, "known": late_resolution, "marketId": "mx",
         "edge": 0.5, "cat": "politics"}
    filler = rows(0, 30, 0.08, "k", 18, 90)
    at_100 = [x for x in filler + [e]
              if x["known"] <= T0 + timedelta(days=100)]
    assert e not in at_100


def test_interval_consistency():
    bets = [("a", 0.1, "sports", "T", 0), ("b", 0.2, "sports", "T", 0),
            ("c", -0.3, "sports", "T", 1)]
    pos, act = wf.interval_consistency(bets, 5)
    assert (pos, act) == (1, 2)


def test_gamma_mapping_via_backfill_script():
    import scripts.backfill_resolutions_gamma as bg
    m = {"closed": True, "conditionId": "0xa", "id": 5,
         "outcomePrices": '["0","1"]', "clobTokenIds": '["T1","T2"]',
         "closedTime": "2026-01-05T00:00:00Z", "category": "Esports"}
    r = bg.map_gamma_market(m)
    assert r["resolution"] == "NO" and r["no_token_id"] == "T2"
    assert bg.map_gamma_market({**m, "outcomePrices": '["0.7","0.3"]'}) is None
    assert bg.keys_of(m) == ["0xa", "5"]
