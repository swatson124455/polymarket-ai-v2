"""Unit tests for scripts/find_copyable_traders.py pure core.

Locks the honesty-critical behaviors: qualification NEVER sees P2 (a trader
with a monster P2 but thin P1 must not qualify), lucky-vs-skilled separation,
estimand fidelity (first BUY per market before labelability), and the
pre-registered primary verdict states.
"""
from __future__ import annotations

import random
from datetime import datetime

import scripts.find_copyable_traders as fc


def _ents(n, when, mu, seed, cat="sports"):
    rng = random.Random(seed)
    return [{"_ts": when, "marketId": f"m{seed}_{i}", "cat": cat,
             "edge": rng.gauss(mu, 0.05)} for i in range(n)]


SPLIT = datetime(2026, 6, 1)


def test_qualification_uses_p1_only():
    # Monster P2, but only 5 P1 entries → must NOT qualify (selection blind to P2).
    ents = _ents(5, datetime(2026, 5, 1), 0.3, 1) + _ents(80, datetime(2026, 7, 1), 0.3, 2)
    assert fc.qualify_and_judge(ents, SPLIT, 25, 300, 7) is None
    # Negative P1 edge with huge P1 count → also out.
    ents = _ents(60, datetime(2026, 5, 1), -0.05, 3) + _ents(60, datetime(2026, 7, 1), 0.3, 4)
    assert fc.qualify_and_judge(ents, SPLIT, 25, 300, 7) is None


def test_lucky_regresses_skilled_survives():
    lucky = _ents(40, datetime(2026, 5, 1), 0.10, 5) + _ents(40, datetime(2026, 7, 1), 0.0, 6)
    skilled = _ents(40, datetime(2026, 5, 1), 0.10, 7) + _ents(40, datetime(2026, 7, 1), 0.08, 8)
    ql = fc.qualify_and_judge(lucky, SPLIT, 25, 500, 7)
    qs = fc.qualify_and_judge(skilled, SPLIT, 25, 500, 7)
    assert ql is not None and ql["p2_p"] < 0.95      # qualified, but P2 exposes the luck
    assert qs is not None and qs["p2_p"] > 0.95      # repeatable edge survives


def test_first_buys_estimand():
    t0 = datetime(2026, 1, 1)
    trades = [
        {"side": "BUY", "marketId": "M1", "price": 0.5, "_ts": t0.replace(day=2)},
        {"side": "BUY", "marketId": "M1", "price": 0.6, "_ts": t0.replace(day=1)},
        {"side": "SELL", "marketId": "M1", "price": 0.7, "_ts": t0},
        {"side": "BUY", "marketId": "M2", "price": 0.99, "_ts": t0},   # dust bound
        {"side": "BUY", "marketId": "", "price": 0.5, "_ts": t0},      # no market
    ]
    fb = fc.first_buys(trades, 0.02, 0.98)
    assert len(fb) == 1 and fb[0]["marketId"] == "M1" and fb[0]["price"] == 0.6


def test_label_outcome_matrix():
    mkt = {"resolution": "NO", "yes_token_id": "TY", "no_token_id": "TN"}
    assert fc.label_outcome("TN", "BUY", mkt) == 1.0
    assert fc.label_outcome("TY", "BUY", mkt) == 0.0
    assert fc.label_outcome("", "YES", mkt) == 0.0     # side fallback
    assert fc.label_outcome("TY", "BUY", None) is None
    assert fc.label_outcome("TY", "BUY", {"resolution": None}) is None


def test_parse_ts_variants():
    assert fc.parse_ts(1767225600) == datetime(2026, 1, 1)
    assert fc.parse_ts("1767225600") == datetime(2026, 1, 1)
    assert fc.parse_ts("2026-01-01T00:00:00Z") == datetime(2026, 1, 1)
    assert fc.parse_ts("2026-01-01T01:00:00+01:00") == datetime(2026, 1, 1)
    assert fc.parse_ts("garbage") is None and fc.parse_ts(None) is None


def test_primary_verdict_states():
    base = {"p": 0.97, "edge": 0.03, "n_markets": 50, "n_traders": 8, "n_bets": 200}
    assert fc.primary_verdict({"cat:sports": base}, "cat:sports", 0.95, 30, 5)[0] == "PASS"
    assert fc.primary_verdict({}, "cat:sports", 0.95, 30, 5)[0] == "NO-DATA"
    assert fc.primary_verdict({"cat:sports": {**base, "n_markets": 10}},
                              "cat:sports", 0.95, 30, 5)[0] == "UNDERPOWERED"
    assert fc.primary_verdict({"cat:sports": {**base, "p": 0.80}},
                              "cat:sports", 0.95, 30, 5)[0] == "FAIL"


def test_profile_flags_uncopyable_prices():
    t0 = datetime(2026, 1, 1)
    trades = [{"price": 0.99, "size": 1000, "_ts": t0} for _ in range(9)] + \
             [{"price": 0.50, "size": 100, "_ts": t0}]
    prof = fc.profile_trader(trades)
    assert prof["pct_copyable_price"] == 0.1           # the 99c-favorite whale
    assert prof["median_price"] == 0.99
