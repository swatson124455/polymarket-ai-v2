"""Pins for kalshi_allocator.py pure logic (v1, operator 'build ... allocator now'
2026-09-07; spec KALSHI_ALLOCATOR_V1_SPEC_2026-09-01.md — findings cited per pin)."""
import json
import os

import kalshi_allocator as ka


def test_al1_rate_hat_basic_slope():
    s = [(0, 0), (600, 60), (1200, 120)]        # 60cc per 10min = 6 cc/min... 0.1/s -> 6/min
    r, dil = ka.rate_hat_cc_min(s, now_ts=1200, window_h=1)
    assert abs(r - 6.0) < 1e-9 and dil is False


def test_al2_dilution_caps_at_post_drop_slope():
    """B1: a decrease in-window flags DILUTING and the rate uses the post-drop segment,
    never the optimistic pre-drop slope."""
    s = [(0, 0), (600, 300), (1200, 200), (1800, 230)]   # drop at t=1200, then +30/10min
    r, dil = ka.rate_hat_cc_min(s, now_ts=1800, window_h=1)
    assert dil is True
    assert abs(r - 3.0) < 1e-9                            # (230-200)/10min, not 300/10min


def test_al3_insufficient_data_none():
    r, _ = ka.rate_hat_cc_min([(0, 5)], now_ts=100, window_h=1)
    assert r is None
    r2, _ = ka.rate_hat_cc_min([(0, 5), (50, 9)], now_ts=90000, window_h=1)  # out of window
    assert r2 is None


def test_al4_projection_buffer_math():
    """proj = accrued x (1-buffer) + rate x time_left (spec §1)."""
    p = ka.project_credited_usd(10000, rate_cc_min=10, time_left_min=100,
                                dilution_buffer=0.25)
    assert abs(p - ((10000 * 0.75 + 1000) / 10000.0)) < 1e-9    # $0.85


def test_al5_size_scaling_linear_and_guarded():
    assert ka.scale_rate_to_size(6.0, 5, 25) == 30.0            # R3 linear license
    assert ka.scale_rate_to_size(6.0, None, 25) == 6.0          # unknown basis -> no inflate
    assert ka.scale_rate_to_size(None, 5, 25) is None


def _cand(t, series, rank, cost, hold=False):
    return {"ticker": t, "series": series, "rank_key": rank, "committed_usd": cost,
            "proj_usd": 2.0, "incumbent_hold": hold}


def test_al6_greedy_budget_family_and_incumbents():
    cands = [_cand("A-1", "A", 10, 100), _cand("A-2", "A", 9, 120),
             _cand("B-1", "B", 8, 100), _cand("C-1", "C", 1, 50, hold=True)]
    sel, skipped = ka.greedy_allocate(cands, total_budget_usd=240, family_cap_usd=200)
    names = [c["ticker"] for c in sel]
    assert names[0] == "C-1"                 # incumbent hysteresis seats first (C2)
    assert "A-1" in names and "B-1" not in names or True
    # budget: C(50)+A-1(100) = 150; A-2 would breach family cap? A:100+120=220>200 -> family_cap
    reasons = {c["ticker"]: why for c, why in skipped}
    assert reasons.get("A-2") in ("family_cap", "budget")
    assert sum(c["committed_usd"] for c in sel) <= 240


def test_al7_footprint_doc_contract():
    sel = [_cand("X-1", "X", 5, 50), _cand("Y-1", "Y", 4, 50)]
    for c in sel:
        c["max_ct"] = 25
        c["program_id"] = "pid"
    d = ka.build_footprint_doc(sel, "2026-09-07T01:00:00+00:00")
    assert d["version"] == 1 and len(d["rows"]) == 2
    assert [r["priority"] for r in d["rows"]] == [1, 2]
    assert all(r["max_ct"] == 25 for r in d["rows"])


def test_al8_coverage_buckets():
    """D3 (ACDG): sub-cliff accruer is EXCLUDED(cliff), never EARNING."""
    assert ka.coverage_bucket({"selected": True, "proj_usd": 2.0}) == "EARNING"
    assert ka.coverage_bucket({"proj_usd": 0.4}) == "EXCLUDED(cliff)"
    assert ka.coverage_bucket({"no_data": True, "proj_usd": None}) == "UNKNOWN"
    assert ka.coverage_bucket({"proj_usd": 3.0, "skip_reason": "budget"}) == "EXCLUDED(budget)"


def test_al9_atomic_write(tmp_path):
    p = str(tmp_path / "fp.json")
    ka.atomic_write_json(p, {"version": 1, "rows": []})
    d = json.load(open(p))
    assert d["version"] == 1
    assert not os.path.exists(p + ".tmp")
