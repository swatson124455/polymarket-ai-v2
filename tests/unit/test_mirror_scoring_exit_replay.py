"""Tests for the pure-function port of MB's exit ladder. Thresholds and
priority order must match bots/mirror_bot.py:1274-1441."""

from datetime import datetime, timedelta, timezone

import pytest

from bots.mirror_scoring.exit_replay import ExitParams, replay_exit

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def series(*points):
    """points: (hours_after_entry, price)"""
    return [(T0 + timedelta(hours=h), p) for h, p in points]


def run(prices, res_hours=200.0, outcome=1.0, entry=0.50, conf=0.55,
        sells=None, params=None):
    return replay_exit(
        entry_price=entry, entry_time=T0, price_series=prices,
        resolution_time=T0 + timedelta(hours=res_hours),
        resolution_outcome=outcome, entry_confidence=conf,
        trader_sell_times=sells, params=params,
    )


class TestLadder:
    def test_take_profit_fires_at_25pct(self):
        r = run(series((3, 0.63)))  # +26%
        assert r.exit_reason == "take_profit"
        assert r.o_x == 0.63

    def test_grace_period_skips_early_stop(self):
        # -20% at 1h: within 2h grace and far from resolution => no stop.
        r = run(series((1, 0.40)))
        assert r.exit_reason == "resolution"

    def test_graduated_stop_tier_0_24h(self):
        # after grace, 0-24h tier stops at -6%
        r = run(series((3, 0.46)))  # -8%
        assert r.exit_reason == "stop_loss"

    def test_graduated_stop_tier_24_48h_needs_12pct(self):
        r = run(series((30, 0.46)))   # -8% at 30h: tier is -12% => hold
        assert r.exit_reason == "resolution"
        r = run(series((30, 0.43)))   # -14% => stop
        assert r.exit_reason == "stop_loss"

    def test_near_resolution_override_tightens_to_5pct(self):
        # 10h to resolution, -6% at 30h: near-res stop -5% fires even
        # though the 24-48h tier would allow -12%.
        r = run(series((30, 0.47)), res_hours=40.0)
        assert r.exit_reason == "stop_loss"

    def test_force_exit_at_96h(self):
        r = run(series((97, 0.55)), res_hours=500.0)
        assert r.exit_reason == "force_exit"

    def test_max_hold_fraction_before_force(self):
        # 85h held, 10h to resolution: hold_frac 0.89 >= 0.80 => exit
        r = run(series((85, 0.55)), res_hours=95.0)
        assert r.exit_reason == "max_hold_fraction"

    def test_trader_sell_exit_after_min_hold(self):
        r = run(series((0.4, 0.52), (1.0, 0.52)),
                sells=[T0 + timedelta(minutes=20)])
        # first tick at 0.4h is below the 0.5h guard; second fires
        assert r.exit_reason == "trader_sell_exit"
        assert r.hours_held == pytest.approx(1.0)

    def test_edge_decay_halves_stop_on_stale_position(self):
        # conf 0.55 decays below 0.50 after 2.5 days; at 72h the base
        # -15% stop halves to -7.5%, so -10% fires.
        r = run(series((73, 0.45)), conf=0.55)  # -10%
        assert r.exit_reason == "stop_loss"

    def test_hold_to_resolution_pays_outcome(self):
        r = run(series((5, 0.55)), outcome=1.0)
        assert r.exit_reason == "resolution"
        assert r.o_x == 1.0
        r = run(series((5, 0.55)), outcome=0.0)
        assert r.o_x == 0.0

    def test_hard_stop_when_configured(self):
        p = ExitParams(hard_stop_pct=0.30)
        r = run(series((3, 0.30)), params=p)  # -40%
        assert r.exit_reason == "hard_stop"

    def test_priority_tp_before_trader_sell(self):
        # both TP and trader-sell true on the same tick: TP wins (live order)
        r = run(series((1.0, 0.70)), sells=[T0 + timedelta(minutes=10)])
        assert r.exit_reason == "take_profit"

    def test_ticks_after_resolution_ignored(self):
        r = run(series((250, 0.10)), res_hours=200.0, outcome=1.0)
        assert r.exit_reason == "resolution"
        assert r.o_x == 1.0
