"""Pins for the FAR-CLOSE CAP (KALSHI_MAX_DAYS_TO_CLOSE) — operator directive 2026-07-25.

WHY: reward is size x TIME PRESENT, and measured presence collapses with market length (venue
order history, unpruned slice, n=20 markets): under 24h median 16.6%, 4-14d median 10.0%,
14d+ median 0.02% (max 1.15%, n=5). A market we are absent from ~all the time cannot pay, yet it
still ties up capital and carries fill risk for its whole life.

WHY IT IS STRUCTURAL AND NOT LEFT TO THE PRESENCE GATE: with no calibration table the presence
factor defaults to 1.0, so the expected-credit estimate multiplies the daily pool by the FULL
remaining window — making a 30-day market look BETTER than a 1-day one. The presence gate alone
would therefore wave through exactly the markets this cap exists to stop.
"""
from datetime import timedelta

from test_live_hardening import q


def _prog(ticker, hours_to_close, reward_cents=1000000):
    now = q.utcnow()
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "period_reward": reward_cents, "target_size_fp": "1000.00",
            "discount_factor_bps": 5000,
            "start_date": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "end_date": (now + timedelta(hours=hours_to_close)).isoformat().replace("+00:00", "Z")}


def _sel(monkeypatch, progs, cap=3.0):
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", cap)
    return {r["ticker"] for r in q.select_footprint(progs, q.utcnow())}


def test_markets_beyond_the_horizon_are_dropped(monkeypatch):
    progs = [_prog("SOON-X", 12), _prog("EDGE-X", 24 * 2), _prog("FAR-X", 24 * 10)]
    got = _sel(monkeypatch, progs, cap=3.0)
    assert "SOON-X" in got and "EDGE-X" in got
    assert "FAR-X" not in got, "a 10-day market must not enter the footprint"


def test_boundary_is_inclusive_below_and_exclusive_above(monkeypatch):
    inside = _sel(monkeypatch, [_prog("A", 24 * 3 - 1)], cap=3.0)
    outside = _sel(monkeypatch, [_prog("B", 24 * 3 + 1)], cap=3.0)
    assert inside == {"A"} and outside == set()


def test_cap_of_zero_disables_it(monkeypatch):
    """0 must mean OFF, not 'reject everything' — otherwise a mis-set env silently halts quoting."""
    got = _sel(monkeypatch, [_prog("FAR-X", 24 * 30)], cap=0.0)
    assert got == {"FAR-X"}


def test_shipped_default_is_three_days():
    assert q.MAX_DAYS_TO_CLOSE == 3.0


def test_drop_is_counted_under_its_own_reason(monkeypatch):
    """The drop must be attributable — folding it into an existing counter would make a quiet
    universe collapse indistinguishable from the allowlist or the late-life gate."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 3.0)
    q.FP_DROPS.clear()
    q.select_footprint([_prog("FAR-X", 24 * 10)], q.utcnow())
    assert q.FP_DROPS.get("drop_far_close") == 1
    assert not q.FP_DROPS.get("drop_late_life")


def test_far_cap_does_not_swallow_the_late_life_gate(monkeypatch):
    """A market ending in MINUTES is still refused by the late-life gate, not silently accepted
    just because it is comfortably inside the far-close horizon."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 3.0)
    monkeypatch.setattr(q, "WIND_DOWN_MIN", 20.0)
    q.FP_DROPS.clear()
    got = {r["ticker"] for r in q.select_footprint(
        [_prog("ENDING-X", 0.1)], q.utcnow())}          # ~6 minutes left
    assert got == set()
    assert q.FP_DROPS.get("drop_late_life") == 1
    assert not q.FP_DROPS.get("drop_far_close")
