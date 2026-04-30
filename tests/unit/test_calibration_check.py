"""Unit tests for scripts/calibration_check.py extension (S204).

Covers the WB-only per-(trade_side x lead_time_bucket) Brier verification path
added in S204 to support the S203 Track 5 H0' hypothesis-test (NO-side
calibration over-confidence specifically in the 24-48h lead-time bucket).

Tests cover the SQL builder shape, lead-time bucketization, and CLI flag
plumbing. They do not hit a live database — that's the job of the
integration step (running the script against prod, in-session, per
Protocol 11).
"""
from datetime import datetime

import pytest

from scripts.calibration_check import (
    _bucket_for_lead_time,
    _build_per_side_lead_time_sql,
    _parse_args,
)


class TestBucketForLeadTime:
    """Boundary semantics must match bot_pnl.py block 5 (lines 466-470).

    Buckets are half-open intervals: [0,24), [24,48), [48,72), [72,120), [120, inf).
    """

    @pytest.mark.parametrize("lt,expected", [
        (0.0, "<24h"),
        (1.5, "<24h"),
        (23.9, "<24h"),
        (24.0, "24-48h"),
        (24.0001, "24-48h"),
        (47.9, "24-48h"),
        (48.0, "48-72h"),
        (60.0, "48-72h"),
        (71.9, "48-72h"),
        (72.0, "72-120h"),
        (100.0, "72-120h"),
        (119.9, "72-120h"),
        (120.0, ">=120h"),
        (240.0, ">=120h"),
        (1000.0, ">=120h"),
    ])
    def test_bucket_boundaries(self, lt, expected):
        assert _bucket_for_lead_time(lt) == expected


class TestBuildPerSideLeadTimeSql:
    """SQL shape is testable without a DB. The structural assertions guard
    against silent regressions in the JOIN pattern or contamination CTE wiring.
    """

    def test_raw_sql_omits_contamination_cte(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "WITH contaminated" not in sql
        assert "NOT IN (SELECT market_id FROM contaminated)" not in sql

    def test_clean_sql_includes_contamination_cte(self):
        sql = _build_per_side_lead_time_sql(clean=True)
        assert "WITH contaminated AS (" in sql
        assert "pl.market_id NOT IN (SELECT market_id FROM contaminated)" in sql

    def test_clean_sql_uses_canonical_cte_body(self):
        """The contamination CTE must come from bot_pnl._CONTAMINATION_CTE_BODY
        (single source of truth). Verify the load-bearing semantic markers are
        present rather than copy-pasting the full body — the markers prove the
        canonical body was inlined."""
        from scripts.bot_pnl import _CONTAMINATION_CTE_BODY
        sql = _build_per_side_lead_time_sql(clean=True)
        # Spot-check semantic markers from the canonical body.
        assert "FROM trade_events" in _CONTAMINATION_CTE_BODY
        assert "event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')" in _CONTAMINATION_CTE_BODY
        # And those markers should be present in the built SQL via the prefix.
        assert _CONTAMINATION_CTE_BODY.strip() in sql

    def test_sql_filters_to_weatherbot(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.bot_name = 'WeatherBot'" in sql
        # Inner subquery filters trade_events to WeatherBot ENTRY events.
        assert "bot_name = 'WeatherBot' AND event_type = 'ENTRY'" in sql

    def test_sql_filters_to_executed_resolved_predictions(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.trade_executed = true" in sql
        assert "pl.resolution IS NOT NULL" in sql

    def test_sql_uses_since_param(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.prediction_time >= :since_dt" in sql

    def test_sql_pulls_lead_time_from_event_data(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "(e_entry.event_data->>'lead_time_hours')::float" in sql
        assert "e_entry.event_data->>'lead_time_hours' IS NOT NULL" in sql

    def test_sql_uses_distinct_on_pattern(self):
        """Mirrors bot_pnl.py block 5: latest ENTRY per market wins."""
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "DISTINCT ON (market_id)" in sql
        assert "ORDER BY market_id, event_time DESC" in sql

    def test_sql_selects_required_columns(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.predicted_prob" in sql
        assert "CASE WHEN pl.resolution = 'YES' THEN 1 ELSE 0 END AS outcome" in sql
        assert "pl.trade_side" in sql

    def test_sql_orders_by_side_then_lead_time(self):
        """ORDER BY pl.trade_side, lead_time_hours keeps deterministic output
        order for downstream Python grouping."""
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "ORDER BY pl.trade_side, lead_time_hours" in sql


class TestParseArgs:
    """CLI flag plumbing. Mirrors bot_pnl.py argparse pattern."""

    def test_defaults(self):
        ns = _parse_args([])
        assert ns.bot_name == ""
        assert ns.cutoff == ""
        assert ns.days == 90
        assert ns.since is None
        assert ns.clean is False

    def test_positional_bot_name(self):
        ns = _parse_args(["WeatherBot"])
        assert ns.bot_name == "WeatherBot"

    def test_since_flag_parses_deploy_stamp(self):
        ns = _parse_args(["--since", "20260414_132211"])
        assert ns.since == datetime(2026, 4, 14, 13, 22, 11)

    def test_clean_flag(self):
        ns = _parse_args(["--clean"])
        assert ns.clean is True

    def test_days_flag(self):
        ns = _parse_args(["--days", "30"])
        assert ns.days == 30

    def test_cutoff_flag(self):
        ns = _parse_args(["--cutoff", "2026-04-08T16:01:40"])
        assert ns.cutoff == "2026-04-08T16:01:40"

    def test_combined_h0_prime_invocation(self):
        """The canonical S204 invocation for the H0' verification:
            python scripts/calibration_check.py WeatherBot --since 20260414_132211 --clean
        """
        ns = _parse_args(["WeatherBot", "--since", "20260414_132211", "--clean"])
        assert ns.bot_name == "WeatherBot"
        assert ns.since == datetime(2026, 4, 14, 13, 22, 11)
        assert ns.clean is True

    def test_invalid_since_format_raises(self):
        """parse_deploy_timestamp uses strict %Y%m%d_%H%M%S — ISO-format input
        should raise rather than silently accept."""
        with pytest.raises(SystemExit):
            # argparse converts ValueError from a type= callable into SystemExit.
            _parse_args(["--since", "2026-04-14T13:22:11"])
