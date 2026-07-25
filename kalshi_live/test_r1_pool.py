"""Regression pin for the R1 pool: usd_day = period_reward/10000, NOT divided by window length.

THE BUG (live until 2026-07-25, present in the deployed build): select_footprint computed
    usd_day = (period_reward / 10000) / days
`period_reward / 10000` is ALREADY the daily, per-market pool. Dividing by window length was
refuted by a blind audit: the raw value puts 147/147 active series inside Kalshi's documented
"$10-$1,000 per day per market" band, while the divided form put 56% BELOW the $10/day floor and
never reached the ceiling.

WHY IT MATTERED (it is a SELECTION bug, not a cosmetic one): usd_day orders the footprint, the
per-series rotation, cap_desired and bound_creates. Dividing by window length flattered short-window
programs (~24x for an hourly) and buried long-window ones by their length in days -- so the bot
systematically picked and kept the wrong markets under a capital cap.

Nothing in the suite pinned this, which is exactly why it survived. It is pinned now.
"""
from datetime import timedelta

from test_live_hardening import q


def _prog(ticker, reward_cents, hours):
    now = q.utcnow()
    return {"market_ticker": ticker, "incentive_type": "liquidity",
            "period_reward": reward_cents, "target_size_fp": "1000.00",
            "discount_factor_bps": 5000,
            "start_date": now.isoformat().replace("+00:00", "Z"),
            "end_date": (now + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")}


def test_usd_day_is_the_raw_daily_pool_regardless_of_window_length(monkeypatch):
    """Same period_reward, wildly different window lengths -> IDENTICAL usd_day."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())        # no allowlist filtering
    now = q.utcnow()
    # 1,000,000 / 10000 = $100.00/day per market
    progs = [_prog("HOURLY-X", 1000000, 1),              # ~1h window
             _prog("WEEKLY-X", 1000000, 24 * 7)]         # 7d window
    rows = {r["ticker"]: r for r in q.select_footprint(progs, now)}
    assert set(rows) == {"HOURLY-X", "WEEKLY-X"}, "both programs must survive selection"
    assert rows["HOURLY-X"]["usd_day"] == 100.0
    assert rows["WEEKLY-X"]["usd_day"] == 100.0
    # the bug made these differ by ~168x (24x up on the hourly, 7x down on the weekly)
    assert rows["HOURLY-X"]["usd_day"] == rows["WEEKLY-X"]["usd_day"]


def test_usd_day_lands_inside_kalshis_documented_10_to_1000_band(monkeypatch):
    """The check that caught the original error: Kalshi documents daily pools of $10-$1,000 per
    market. The raw form respects that band at both extremes; the divided form did not."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    now = q.utcnow()
    progs = [_prog("FLOOR-X", 100000, 1),        # $10/day, shortest window
             _prog("CEIL-X", 10000000, 24 * 31)]  # $1,000/day, longest window
    rows = {r["ticker"]: r for r in q.select_footprint(progs, now)}
    assert rows["FLOOR-X"]["usd_day"] == 10.0
    assert rows["CEIL-X"]["usd_day"] == 1000.0
    for r in rows.values():
        assert 10.0 <= r["usd_day"] <= 1000.0


def test_ramp_min_still_scales_with_window_length(monkeypatch):
    """The `days` variable is still needed for the per-market ramp — removing it with the division
    would have been a NameError. Short windows must ramp sooner than long ones."""
    monkeypatch.setattr(q, "SERIES_ALLOW", set())
    monkeypatch.setattr(q, "RAMP_MIN", 10_000.0)         # keep the global out of the way
    now = q.utcnow()
    rows = {r["ticker"]: r for r in q.select_footprint(
        [_prog("SHORT-X", 1000000, 1), _prog("LONG-X", 1000000, 24 * 7)], now)}
    assert rows["SHORT-X"]["ramp_min"] < rows["LONG-X"]["ramp_min"]
