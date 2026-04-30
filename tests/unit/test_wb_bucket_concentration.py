"""Unit tests for scripts/wb_bucket_concentration.py (S204 Lead 4(5c))."""
from datetime import datetime

import pytest

from scripts.wb_bucket_concentration import (
    _build_concentration_sql,
    _parse_args,
)


class TestBuildConcentrationSql:
    """SQL shape for the per-(city × entry_date × side) decomposition.

    Same testing pattern as TestBuildPerSideLeadTimeSql in
    test_calibration_check.py — guards against silent regressions in the JOIN
    pattern or contamination CTE wiring without requiring a live DB.
    """

    def test_raw_sql_omits_contamination_cte(self):
        sql = _build_concentration_sql(clean=False)
        assert "WITH contaminated" not in sql
        assert "NOT IN (SELECT market_id FROM contaminated)" not in sql

    def test_clean_sql_includes_contamination_cte(self):
        sql = _build_concentration_sql(clean=True)
        assert "WITH contaminated AS (" in sql
        assert "r.market_id NOT IN (SELECT market_id FROM contaminated)" in sql

    def test_clean_sql_uses_canonical_cte_body(self):
        from scripts.bot_pnl import _CONTAMINATION_CTE_BODY
        sql = _build_concentration_sql(clean=True)
        assert _CONTAMINATION_CTE_BODY.strip() in sql

    def test_sql_groups_by_city_date_side(self):
        sql = _build_concentration_sql(clean=False)
        assert "GROUP BY e_entry.event_data->>'city'" in sql
        assert "(e_entry.event_data->>'date')::date" in sql
        assert "e_entry.side" in sql

    def test_sql_filters_to_weatherbot(self):
        sql = _build_concentration_sql(clean=False)
        assert "bot_name = 'WeatherBot'" in sql
        assert "event_type = 'ENTRY'" in sql

    def test_sql_filters_to_resolution_or_exit(self):
        sql = _build_concentration_sql(clean=False)
        assert "r.event_type IN ('RESOLUTION', 'EXIT')" in sql
        assert "r.realized_pnl IS NOT NULL" in sql

    def test_sql_uses_distinct_on_pattern(self):
        sql = _build_concentration_sql(clean=False)
        assert "DISTINCT ON (market_id)" in sql
        assert "ORDER BY market_id, event_time DESC" in sql

    def test_sql_uses_since_param(self):
        sql = _build_concentration_sql(clean=False)
        assert "r.event_time >= :since_ts" in sql

    def test_sql_orders_by_total_pnl_ascending(self):
        """Worst clusters first — caller flags concentration where one
        cluster accounts for >50% of city loss."""
        sql = _build_concentration_sql(clean=False)
        assert "ORDER BY total_pnl ASC" in sql


class TestParseArgs:
    def test_since_required(self):
        with pytest.raises(SystemExit):
            _parse_args([])

    def test_minimal_invocation(self):
        ns = _parse_args(["--since", "20260414_132211"])
        assert ns.since == datetime(2026, 4, 14, 13, 22, 11)
        assert ns.clean is False
        assert ns.top_n == 10

    def test_full_invocation(self):
        ns = _parse_args(["--since", "20260414_132211", "--clean", "--top-n", "5"])
        assert ns.since == datetime(2026, 4, 14, 13, 22, 11)
        assert ns.clean is True
        assert ns.top_n == 5
