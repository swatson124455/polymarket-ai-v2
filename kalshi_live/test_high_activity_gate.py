"""HIGH-ACTIVITY FIRST GATE (operator-named 2026-08-02, coarse v1 — review later): markets
with venue 24h volume above KALSHI_MAX_VOL24H_CT contracts never reach the selector.
Measured basis for the live threshold (random n=160 of 5,448 active liquidity programs,
API read 2026-08-02): p50=0, p75~107, p90~995, p99~27,568 ct/24h — 1,000 ct ~= top decile.
Contract: 0 = provable no-op; unknown volume fails OPEN; volume rides the close-time read
(no extra read cost on a miss) and ages out at VOL24_TTL_S."""
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


def _env(monkeypatch, vols, cap=1000.0):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "SERIES_DENY", ())
    monkeypatch.setattr(q, "SCORE_RANK", 0)
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 8.0)
    monkeypatch.setattr(q, "FOOTPRINT_TOP", 10)
    monkeypatch.setattr(q, "MAX_VOL24H_CT", cap)
    monkeypatch.setattr(q, "_CLOSE_TIME_CACHE", {})
    monkeypatch.setattr(q, "_VOL24_CACHE", {})
    close = (q.utcnow() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(q, "public_get", lambda p: {"market": {
        "close_time": close,
        "volume_24h_fp": str(vols.get(p.rsplit("/", 1)[-1], 0.0))}})


def test_hot_market_gated_quiet_market_kept(monkeypatch):
    _env(monkeypatch, {"KXHOT-01": 20000.0, "KXQUIET-01": 40.0})
    picked = q.select_footprint([_prog("KXHOT-01"), _prog("KXQUIET-01")], q.utcnow())
    assert [m["ticker"] for m in picked] == ["KXQUIET-01"]
    assert q.FP_DROPS.get("drop_high_activity") == 1


def test_gate_off_is_noop(monkeypatch):
    _env(monkeypatch, {"KXHOT-01": 20000.0}, cap=0.0)
    picked = q.select_footprint([_prog("KXHOT-01")], q.utcnow())
    assert [m["ticker"] for m in picked] == ["KXHOT-01"]
    assert "drop_high_activity" not in q.FP_DROPS


def test_boundary_is_exclusive(monkeypatch):
    _env(monkeypatch, {"KXEDGE-01": 1000.0})       # exactly at cap -> kept (> gates)
    picked = q.select_footprint([_prog("KXEDGE-01")], q.utcnow())
    assert [m["ticker"] for m in picked] == ["KXEDGE-01"]


def test_unknown_volume_fails_open(monkeypatch):
    # volume cached but close cached too, then TTL expires only for volume while the
    # budget is exhausted -> row keeps flowing (fail-open, same doctrine as the clock)
    _env(monkeypatch, {"KXANY-01": 40.0})
    q.select_footprint([_prog("KXANY-01")], q.utcnow())          # warm both caches
    monkeypatch.setattr(q, "READ_BUDGET_PER_CYCLE", 0)           # no paid reads now
    monkeypatch.setattr(q, "VOL24_TTL_S", -1.0)                  # volume expired
    picked = q.select_footprint([_prog("KXANY-01")], q.utcnow())
    assert picked, "expired volume with no read budget must fail open"


def test_ttl_refresh_sees_new_activity(monkeypatch):
    vols = {"KXWARM-01": 40.0}
    _env(monkeypatch, vols)
    assert q.select_footprint([_prog("KXWARM-01")], q.utcnow())
    vols["KXWARM-01"] = 50000.0                                  # market gets hot
    monkeypatch.setattr(q, "VOL24_TTL_S", -1.0)                  # force refresh
    picked = q.select_footprint([_prog("KXWARM-01")], q.utcnow())
    assert picked == [] and q.FP_DROPS.get("drop_high_activity") == 1


def test_knob_hot_reloadable():
    import inspect
    assert '"KALSHI_MAX_VOL24H_CT": ("MAX_VOL24H_CT", float)' in \
        inspect.getsource(q._refresh_safety_knobs)
