"""Unit tests for scripts/find_copyable_traders.py pure core (review-hardened).

Locks the honesty-critical behaviors confirmed by the 2026-07-09 two-agent
adversarial review: qualification never sees P2 AND requires a P1 significance
bar (noise traders rejected), lucky-vs-skilled separation, normalization dedupe,
verdict states incl. FAIL-TERMINAL vs INCONCLUSIVE and the robustness-split
veto, and NaN-safe JSON.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime

import scripts.find_copyable_traders as fc

SPLIT = datetime(2026, 2, 1)


def _ents(n, when, mu, tag, seed, cat="sports"):
    rng = random.Random(seed)
    return [{"_ts": when, "marketId": f"m{tag}{i}", "cat": cat,
             "edge": rng.gauss(mu, 0.05)} for i in range(n)]


def test_qualification_uses_p1_only_and_has_significance_bar():
    # Monster P2, thin P1 → out (selection blind to P2).
    ents = _ents(5, datetime(2026, 1, 10), 0.3, "a", 1) + _ents(80, datetime(2026, 5, 1), 0.3, "b", 2)
    assert fc.qualify_and_judge(ents, SPLIT, 25, 0.90, 300, 7) is None
    # Plenty of P1 markets but ~zero P1 edge → rejected by the P1 bootstrap bar
    # (the raw edge>0 rule admitted ~half of all noise traders — review fix).
    noise = _ents(60, datetime(2026, 1, 10), 0.003, "c", 3) + _ents(60, datetime(2026, 5, 1), 0.0, "d", 4)
    assert fc.qualify_and_judge(noise, SPLIT, 25, 0.90, 300, 7) is None


def test_lucky_regresses_skilled_survives():
    lucky = _ents(40, datetime(2026, 1, 10), 0.10, "e", 5) + _ents(40, datetime(2026, 5, 1), 0.0, "f", 6)
    skilled = _ents(40, datetime(2026, 1, 10), 0.10, "g", 7) + _ents(40, datetime(2026, 5, 1), 0.08, "h", 8)
    ql = fc.qualify_and_judge(lucky, SPLIT, 25, 0.90, 500, 7)
    qs = fc.qualify_and_judge(skilled, SPLIT, 25, 0.90, 500, 7)
    assert ql is not None and ql["p2_p"] < 0.95
    assert qs is not None and qs["p2_p"] > 0.95


def test_min_p1_counts_distinct_markets_not_entries():
    # 30 entries all in 3 markets → 3 clusters → must NOT qualify (review fix).
    ents = []
    for i in range(30):
        ents.append({"_ts": datetime(2026, 1, 10), "marketId": f"M{i % 3}",
                     "cat": "sports", "edge": 0.1 + 0.001 * i})
    assert fc.qualify_and_judge(ents, SPLIT, 25, 0.90, 300, 7) is None


def test_normalize_activity_cross_window_dedupe():
    # Time-windowed pagination re-fetches boundary rows; a persistent `seen`
    # set must drop them across calls (the 3500-offset-cap workaround).
    row = {"type": "TRADE", "conditionId": "0xA", "asset": "T1", "side": "BUY",
           "price": "0.5", "size": "10", "timestamp": 100, "transactionHash": "0xh"}
    seen: set = set()
    first = fc.normalize_activity([row], seen)
    second = fc.normalize_activity([row], seen)   # boundary re-fetch
    assert len(first) == 1 and len(second) == 0


def test_normalize_activity_dedupes_and_filters():
    raw = [{"type": "TRADE", "conditionId": "0xA", "asset": "T1", "side": "buy",
            "price": "0.5", "size": "10", "timestamp": 1767225600,
            "transactionHash": "0xh"}] * 3
    raw += [{"type": "REDEEM"}, {"type": "TRADE"}]  # non-trade + missing market
    out = fc.normalize_activity(raw)
    assert len(out) == 1 and out[0]["side"] == "BUY" and out[0]["price"] == 0.5
    # same tx hash, different fill → kept (multi-fill txs share hashes)
    raw2 = [{"type": "TRADE", "conditionId": "0xA", "asset": "T1", "side": "BUY",
             "price": "0.5", "size": "10", "timestamp": 1, "transactionHash": "0xh"},
            {"type": "TRADE", "conditionId": "0xA", "asset": "T1", "side": "BUY",
             "price": "0.6", "size": "10", "timestamp": 1, "transactionHash": "0xh"}]
    assert len(fc.normalize_activity(raw2)) == 2


def test_first_buys_estimand():
    t0 = datetime(2026, 1, 1)
    trades = [
        {"side": "BUY", "marketId": "M1", "price": 0.5, "_ts": t0.replace(day=2)},
        {"side": "BUY", "marketId": "M1", "price": 0.6, "_ts": t0.replace(day=1)},
        {"side": "SELL", "marketId": "M1", "price": 0.7, "_ts": t0},
        {"side": "BUY", "marketId": "M2", "price": 0.99, "_ts": t0},
    ]
    fb = fc.first_buys(trades, 0.02, 0.98)
    assert len(fb) == 1 and fb[0]["price"] == 0.6


def test_label_outcome_matrix():
    mkt = {"resolution": "NO", "yes_token_id": "TY", "no_token_id": "TN"}
    assert fc.label_outcome("TN", "BUY", mkt) == 1.0
    assert fc.label_outcome("TY", "BUY", mkt) == 0.0
    assert fc.label_outcome("", "YES", mkt) == 0.0
    assert fc.label_outcome("TY", "BUY", None) is None


def test_boot_stats_bounds():
    rng = random.Random(0)
    p, mean, up = fc.boot_stats([rng.gauss(0.05, 0.02) for _ in range(40)], 800, 1)
    assert p > 0.95 and up > mean > 0
    p0, m0, u0 = fc.boot_stats([], 800, 2)
    assert p0 == 0.0 and math.isnan(m0)
    p1, m1, u1 = fc.boot_stats([0.1], 800, 3)
    assert p1 == 0.0 and m1 == u1 == 0.1  # degenerate: no evidence, no claim


def test_primary_verdict_states():
    base = {"p": 0.97, "edge": 0.03, "upper95": 0.05, "n_markets": 50,
            "n_traders": 8, "n_bets": 200}
    assert fc.primary_verdict(base, 0.95, 30, 5, 0.02, [0.01, 0.02])[0] == "PASS"
    # robustness veto: a split with non-positive pooled edge blocks PASS
    assert fc.primary_verdict(base, 0.95, 30, 5, 0.02, [0.01, -0.01])[0] == "INCONCLUSIVE"
    assert fc.primary_verdict(None, 0.95, 30, 5, 0.02, [])[0] == "NO-DATA"
    assert fc.primary_verdict({**base, "n_markets": 10}, 0.95, 30, 5, 0.02, [])[0] == "UNDERPOWERED"
    # FAIL is terminal only when even the optimistic bound is below costs
    assert fc.primary_verdict({**base, "p": 0.6, "upper95": 0.001},
                              0.95, 30, 5, 0.02, [])[0] == "FAIL-TERMINAL"
    assert fc.primary_verdict({**base, "p": 0.6, "upper95": 0.04},
                              0.95, 30, 5, 0.02, [])[0] == "INCONCLUSIVE"


def test_parse_ts_and_json_safety():
    assert fc.parse_ts("2026-01-01") == datetime(2026, 1, 1)
    assert fc.parse_ts("2026-01-01T00:00:00Z") == datetime(2026, 1, 1)  # tz-aware ok
    blob = json.dumps(fc.json_safe({"a": float("nan"), "b": [float("inf"), 1.0],
                                    "t": datetime(2026, 1, 1)}))
    parsed = json.loads(blob)
    assert parsed["a"] is None and parsed["b"][0] is None and parsed["b"][1] == 1.0


def test_profile_flags_uncopyable_prices():
    t0 = datetime(2026, 1, 1)
    trades = [{"price": 0.99, "size": 1000, "_ts": t0} for _ in range(9)] + \
             [{"price": 0.50, "size": 100, "_ts": t0}]
    prof = fc.profile_trader(trades)
    assert prof["pct_copyable_price"] == 0.1 and prof["median_price"] == 0.99


def test_should_refetch_deepens_only_truncated_below_cap():
    # truncated + higher cap + allowed → re-pull
    assert fc.should_refetch("truncated", 20000, 100000, True)
    # clean completion is never re-pulled, whatever the cap
    assert not fc.should_refetch("ok", 500, 100000, True)
    # cap not raised → nothing to gain
    assert not fc.should_refetch("truncated", 100000, 100000, True)
    # deepen disabled (e.g. --deepen vol and a PNL-only trader) → cache as-is
    assert not fc.should_refetch("truncated", 20000, 100000, False)


def test_market_maker_detection():
    from datetime import datetime, timedelta
    t0 = datetime(2026, 6, 1)
    # 500 trades in half a day = ~1000/day → bot
    fast = [{"timestamp": int((t0 + timedelta(minutes=i)).timestamp())}
            for i in range(500)]
    # 500 trades over 100 days = 5/day → human
    slow = [{"timestamp": int((t0 + timedelta(hours=i * 5)).timestamp())}
            for i in range(500)]
    assert fc.looks_like_market_maker(fast, 200.0)
    assert not fc.looks_like_market_maker(slow, 200.0)
    assert not fc.looks_like_market_maker(fast, 0)      # 0 disables
    assert not fc.looks_like_market_maker(fast[:50], 200.0)  # too little data

    fast_h = [{"_ts": t0 + timedelta(minutes=i)} for i in range(500)]
    slow_h = [{"_ts": t0 + timedelta(hours=i * 5)} for i in range(500)]
    assert fc.is_hft_history(fast_h, 200.0)
    assert not fc.is_hft_history(slow_h, 200.0)


def test_merge_gamma_cache_fills_holes_db_wins(tmp_path):
    import json as _json
    markets = {"0xdb": {"resolution": "NO"},
               "0xunres": {"resolution": None, "resolved_at": None,
                           "yes_token_id": "D1", "no_token_id": "D2",
                           "category": "Politics"}}
    blob = {"0xdb": {"resolution": "YES"},
            "0xunres": {"resolution": "YES", "yes_token_id": "G1",
                        "no_token_id": "G2", "category": "Politics",
                        "resolved_at": "2026-02-01T00:00:00"},
            "0xnew": {"resolution": "YES", "yes_token_id": "T1",
                      "no_token_id": "T2", "category": "Sports",
                      "resolved_at": "2026-01-05T00:00:00"},
            "0xopen": {"resolution": None}}
    p = tmp_path / "g.json"
    p.write_text(_json.dumps(blob))
    n = fc.merge_gamma_cache(
        markets, ["0xdb", "0xunres", "0xnew", "0xopen", "0xabsent"], str(p))
    assert n == 2
    assert markets["0xdb"]["resolution"] == "NO"        # DB label wins
    assert markets["0xunres"]["resolution"] == "YES"    # DB metadata row w/o
    assert markets["0xunres"]["yes_token_id"] == "G1"   #  label is a HOLE —
    assert markets["0xunres"]["resolved_at"] == "2026-02-01T00:00:00"  # filled
    assert markets["0xnew"]["yes_token_id"] == "T1"     # unknown key filled
    assert "0xopen" not in markets                      # unresolved never merged
    assert fc.merge_gamma_cache({}, ["k"], str(tmp_path / "missing.json")) == 0
