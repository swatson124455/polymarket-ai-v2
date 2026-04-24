"""S194: Pin the MIRROR_REGIME_START str→datetime conversion.

Pre-S194: settings.py declared MIRROR_REGIME_START as `str`. Both consumers
(EliteReliabilityTracker.refresh and EliteWatchlist copy-tier scoring SQL)
bound this str directly to an asyncpg-backed timestamptz query parameter.
asyncpg validated the parameter type before sending and rejected with
`DataError: expected a datetime instance, got 'str'`. Both queries failed
silently for 25 days starting 2026-03-30, leaving the reliability cache
empty → trader _eq_n=0 → MB gate_score capped → MB stopped trading 2026-04-13.
"""
from datetime import datetime, timezone

import pytest


class TestParseIsoDt:
    """The settings._parse_iso_dt helper must produce a datetime or None."""

    def test_iso_string_with_tz_parses_to_datetime(self):
        from config.settings import _parse_iso_dt

        result = _parse_iso_dt("2026-03-30T12:43:00+00:00")
        assert isinstance(result, datetime), \
            "Must return datetime instance for asyncpg compatibility"
        assert result.year == 2026 and result.month == 3 and result.day == 30
        assert result.tzinfo is not None, "tz-aware input must yield tz-aware result"

    def test_empty_string_returns_none(self):
        from config.settings import _parse_iso_dt
        assert _parse_iso_dt("") is None
        assert _parse_iso_dt("   ") is None
        assert _parse_iso_dt(None) is None

    def test_malformed_string_returns_none(self):
        """Bad input must not crash settings import — fall back to None (no filter)."""
        from config.settings import _parse_iso_dt
        assert _parse_iso_dt("not a date") is None
        assert _parse_iso_dt("2026-13-99") is None  # invalid month/day
        assert _parse_iso_dt("garbage") is None


class TestSettingsRegimeStartType:
    """Settings.MIRROR_REGIME_START must be datetime or None, not str."""

    def test_default_value_is_datetime(self):
        from config.settings import settings
        assert settings.MIRROR_REGIME_START is None or isinstance(settings.MIRROR_REGIME_START, datetime), \
            "S194: Type must be Optional[datetime]; asyncpg rejects str at bind time"

    def test_settings_field_annotation_is_optional_datetime(self):
        """The field's declared type must reflect the runtime type to avoid drift."""
        from config.settings import Settings
        ann = Settings.model_fields["MIRROR_REGIME_START"].annotation
        # Optional[datetime] == Union[datetime, None]; just check datetime appears
        ann_str = str(ann)
        assert "datetime" in ann_str.lower(), \
            f"S194: MIRROR_REGIME_START annotation must include datetime; got {ann_str}"


class TestEliteReliabilityTrackerSignatureAcceptsDatetime:
    """EliteReliabilityTracker.__init__ must accept Optional[datetime]."""

    def test_init_accepts_datetime(self):
        """Construction with a datetime regime_start must not raise."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker

        regime_dt = datetime(2026, 3, 30, 12, 43, tzinfo=timezone.utc)
        tracker = EliteReliabilityTracker(db=None, lookback_days=365, regime_start=regime_dt)
        assert tracker.regime_start is regime_dt, \
            "datetime must pass through unchanged for asyncpg binding"

    def test_init_accepts_none(self):
        """None regime_start (no filter) must construct cleanly."""
        from base_engine.learning.elite_reliability import EliteReliabilityTracker
        tracker = EliteReliabilityTracker(db=None, regime_start=None)
        assert tracker.regime_start is None
