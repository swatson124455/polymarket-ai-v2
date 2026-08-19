"""Pins for the MIN-RUNWAY ENTRY GATE (KALSHI_MIN_RUNWAY_H) — concentrated-cliff build
2026-08-19, implementing the 08-13 roadmap's LOCKED "window >= 49h" entry rule.

WHY: the per-program $1 cliff (canon 2026-08-18) makes remaining PROGRAM window the revenue
runway — a program entered with less runway than the cliff projection needs is guaranteed
dead weight (accrues sub-$1 -> pays $0) while carrying full fill risk. R1 hit this live:
candidates placeable at plan time were un-placeable by GO time (program end 03:59Z vs
market close a day later).
"""
from datetime import timedelta

from test_live_hardening import q


def _prog(ticker, hours_to_end, reward_cents=1000000):
    now = q.utcnow()
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "period_reward": reward_cents, "target_size_fp": "1000.00",
            "discount_factor_bps": 5000,
            "start_date": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "end_date": (now + timedelta(hours=hours_to_end)).isoformat().replace("+00:00", "Z")}


def _sel(monkeypatch, progs, runway_h):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)      # isolate the gate under test
    monkeypatch.setattr(q, "MIN_RUNWAY_H", runway_h)
    return {r["ticker"] for r in q.select_footprint(progs, q.utcnow())}


def test_short_runway_dropped_long_runway_kept(monkeypatch):
    progs = [_prog("SHORT-X", 24), _prog("LONG-X", 96)]
    got = _sel(monkeypatch, progs, runway_h=49.0)
    assert "LONG-X" in got
    assert "SHORT-X" not in got, "a 24h-runway program must not enter at a 49h gate"


def test_zero_disables_it(monkeypatch):
    """0 must mean OFF (today's exact behavior), not 'reject everything'."""
    got = _sel(monkeypatch, [_prog("SHORT-X", 24)], runway_h=0.0)
    assert got == {"SHORT-X"}


def test_shipped_default_is_off():
    assert q.MIN_RUNWAY_H == 0.0


def test_drop_is_counted_under_its_own_reason(monkeypatch):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 49.0)
    q.FP_DROPS.clear()
    q.select_footprint([_prog("SHORT-X", 24)], q.utcnow())
    assert q.FP_DROPS.get("drop_min_runway") == 1
    assert not q.FP_DROPS.get("drop_far_close")


def test_runs_after_far_close_never_swallows_it(monkeypatch):
    """A far market is attributed to the far-close cap, not to min-runway — the two horizon
    gates must stay separately attributable in telemetry."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 3.0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 49.0)
    q.FP_DROPS.clear()
    got = {r["ticker"] for r in q.select_footprint([_prog("FAR-X", 24 * 10)], q.utcnow())}
    assert got == set()
    assert q.FP_DROPS.get("drop_far_close") == 1
    assert not q.FP_DROPS.get("drop_min_runway")


def test_macro_designation_is_exempt(monkeypatch):
    """Operator macro designation overrides horizon prefs (D-C review #6) — the runway gate
    must honor the same exemption as the two gates beside it."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 49.0)
    monkeypatch.setattr(q, "MACRO_PROBE_TICKERS", {"SHORT-X"})
    got = {r["ticker"] for r in q.select_footprint([_prog("SHORT-X", 24)], q.utcnow())}
    assert got == {"SHORT-X"}
