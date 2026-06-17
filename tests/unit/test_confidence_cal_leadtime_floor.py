"""S246: regression guards for the confidence-calibrator lead-time training floor.

The per-side WeatherConfidenceCalibrator was effectively OFF for ~6 weeks: its
training query in fit_from_trade_events hardcoded `lead_time_hours >= 48.0`, but the
bot's trade mix shifted to almost entirely <48h, so every 6h refit found n~2 vs
need=200 and no-op'd (`weatherbot_confidence_cal_insufficient_data n=2 need=200`).
With the calibrator frozen at a stale fit AND `_cal_fitted=False`, the bot bet on RAW
over-confident probabilities (YES conf 0.80 → ~37.5% realized WR).

These guards pin the fix so the bug class cannot silently return:
  * the lead-time floor must stay config-driven (no hardcoded 48h),
  * its default must match the served distribution (train on what we trade),
  * the min-samples gate must stay reachable under the short-lead mix.
The behavioral proof (n=2→162, per-side Brier improves +0.05/+0.15, fit passes the
train-Brier gate) is in the S246 backtest against the production DB — a SQL-side
filter can't be exercised by a mocked session.
"""
import inspect

# IMPORTANT: import settings the SAME way weather_bot.py does (the silo module), NOT
# the top-level config.settings. weather_bot.py:56 reads the silo settings, so a fix to
# only the top-level module is dead code (S246 production catch: top-level said 120 but
# the bot logged need=200 from the silo's 200). Assert the module the bot actually reads.
from bots.weather.engine.config.settings import settings
from bots.weather_bot import WeatherConfidenceCalibrator, _CONF_CAL_MIN_SAMPLES


class TestConfidenceCalLeadTimeFloor:
    def test_training_floor_is_config_driven_not_hardcoded_48h(self):
        src = inspect.getsource(WeatherConfidenceCalibrator.fit_from_trade_events)
        assert ":min_lead_h" in src, (
            "fit_from_trade_events must bind the configurable lead-time floor "
            "(WEATHER_CONFIDENCE_CAL_MIN_LEAD_H), not hardcode it"
        )
        assert ">= 48.0" not in src, (
            "hardcoded 48h lead-time floor reintroduced — this freezes the calibrator "
            "whenever the bot trades short-lead (the S246 bug)"
        )

    def test_min_lead_h_default_matches_served_distribution(self):
        # 0.0 trains on the lead-times the bot actually serves (<48h). A non-zero
        # default re-creates the train/serve mismatch.
        assert settings.WEATHER_CONFIDENCE_CAL_MIN_LEAD_H == 0.0

    def test_min_samples_gate_is_reachable(self):
        # 200 was unreachable once the trade mix went short-lead (30d window ~162
        # qualifying samples). The gate must stay below that so the calibrator can fit.
        assert settings.WEATHER_CONFIDENCE_CAL_MIN_SAMPLES < 200
        assert _CONF_CAL_MIN_SAMPLES < 200
        # ...but not so low the per-side isotonic (n>=30) / Beta (n>=80) gates lose meaning.
        assert settings.WEATHER_CONFIDENCE_CAL_MIN_SAMPLES >= 60
