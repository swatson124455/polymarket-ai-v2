"""D1 ROOT FIX (Option C commit 1, operator-named 2026-08-02): the close-time pre-filter's
80-kept early stop made its read budget inert — ~3,300 rows/cycle reached the selector
unchecked and the post-selection belt killed ~70% of allocated slots (footprint p50 12/40,
2,277/2,277 cycles, selection review 2026-08-01). The scan now walks the whole list: cached
clocks filter exactly for free, paid reads stay budget-bounded, and only the cache-warming
remainder is kept fail-open and COUNTED (close_unchecked_tail -> 0 as the cache fills)."""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _prog(ticker, hours_to_close=4):
    now = q.utcnow()
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "period_reward": 1000000, "target_size_fp": "1000.00",
            "discount_factor_bps": 5000,
            "start_date": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "end_date": (now + timedelta(hours=hours_to_close)).isoformat().replace("+00:00", "Z")}


def _env(monkeypatch, cache):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "SERIES_DENY", ())
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 8.0)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 3)
    monkeypatch.setattr(q, "_close_cache_get", lambda t: cache.get(t))
    monkeypatch.setattr(q, "_close_cache_put", lambda t, v: cache.__setitem__(t, v))


def _close(days):
    return (q.utcnow() + timedelta(days=days)).isoformat()


def test_warm_cache_scans_everything_no_unchecked_tail(monkeypatch):
    # 30 rows, all clocks cached, half far-close. Old code stopped at 6 kept (=2xTOP) and
    # appended 20+ rows unchecked; now the whole list is filtered exactly at zero reads.
    cache = {}
    progs = []
    for i in range(30):
        t = f"KXW-{i:02d}"
        progs.append(_prog(t))
        cache[t] = _close(30) if i % 2 else _close(1)      # odd rows = far-close
    _env(monkeypatch, cache)
    monkeypatch.setattr(q, "public_get", lambda p: (_ for _ in ()).throw(
        AssertionError("warm cache must not pay for reads")))
    picked = q.select_footprint(progs, q.utcnow())
    assert "close_unchecked_tail" not in q.FP_DROPS
    assert q.FP_DROPS.get("drop_far_market_close_sel") == 15
    assert all(int(m["ticker"].split("-")[1]) % 2 == 0 for m in picked), \
        "no far-close row may reach the selector on a warm cache"


def test_cold_cache_budget_bounds_reads_and_counts_tail(monkeypatch):
    cache = {}
    progs = [_prog(f"KXC-{i:02d}") for i in range(30)]
    _env(monkeypatch, cache)
    monkeypatch.setattr(q, "READ_BUDGET_PER_CYCLE", 10)    # -> budget = max(40,5) = 40? no:
    # _budget = min(FOOTPRINT_TOP*4, max(40, READ_BUDGET//2)) = min(12, 40) = 12
    reads = []
    def paid(path):
        reads.append(path)
        return {"market": {"close_time": _close(1)}}
    monkeypatch.setattr(q, "public_get", paid)
    q.select_footprint(progs, q.utcnow())
    assert len(reads) == 12, "paid reads stop exactly at the budget"
    assert q.FP_DROPS.get("close_unchecked_tail") == 18, "remainder kept fail-open, counted"


def test_cache_warms_toward_zero_tail_across_cycles(monkeypatch):
    cache = {}
    progs = [_prog(f"KXZ-{i:02d}") for i in range(20)]
    _env(monkeypatch, cache)
    monkeypatch.setattr(q, "READ_BUDGET_PER_CYCLE", 24)    # budget = min(12, max(40,12))=12
    monkeypatch.setattr(q, "public_get", lambda p: {"market": {"close_time": _close(1)}})
    q.select_footprint(progs, q.utcnow())
    first_tail = q.FP_DROPS.get("close_unchecked_tail", 0)
    q.select_footprint(progs, q.utcnow())                  # cycle 2: 12 cached, 8 to pay
    assert q.FP_DROPS.get("close_unchecked_tail", 0) < first_tail or first_tail == 0
    q.select_footprint(progs, q.utcnow())                  # cycle 3: fully cached
    assert "close_unchecked_tail" not in q.FP_DROPS
