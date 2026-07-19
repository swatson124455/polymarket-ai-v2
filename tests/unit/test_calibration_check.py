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
from unittest.mock import AsyncMock, MagicMock

import pytest

import scripts.calibration_check as cc
from scripts.calibration_check import (
    _bucket_for_lead_time,
    _build_per_side_lead_time_sql,
    _dedup_latest_per_market,
    _parse_args,
    calibration_check,
)


class TestCalibrationCheckIntegration:
    """S232 re-review: the main fetch SELECT and the row-unpacking loop must
    agree on column count. The c11 fix added pl.model_name (7th column) and a
    6-target unpack shipped a live ValueError — the helper-only tests missed
    it. This drives the real calibration_check() with a mocked session."""

    @pytest.mark.asyncio
    async def test_main_path_consumes_7col_rows_without_crash(self, monkeypatch):
        d1 = datetime(2026, 7, 15, 12, 0, 0)
        d2 = datetime(2026, 7, 15, 13, 0, 0)
        # 7-column rows exactly as the main SELECT returns them
        rows = [
            (0.30, 0, "WeatherBot", "weather", "0xA", d1, "weather_temperature"),
            (0.62, 1, "WeatherBot", "weather", "0xB", d2, "weather_temperature"),
        ]
        count_res = MagicMock()
        count_res.fetchone.return_value = (120, 80, 55)   # distinct_resolved >= 50 gate
        main_res = MagicMock()
        main_res.fetchall.return_value = rows
        side_res = MagicMock()
        side_res.fetchall.return_value = []               # per-side×lead: empty ok

        sess = MagicMock()
        sess.execute = AsyncMock(side_effect=[count_res, main_res, side_res])

        class _Ctx:
            async def __aenter__(self_): return sess
            async def __aexit__(self_, *a): return False

        fake_db = MagicMock()
        fake_db.init = AsyncMock()
        fake_db.close = AsyncMock()
        fake_db.get_session = MagicMock(return_value=_Ctx())
        monkeypatch.setattr(cc, "Database", lambda: fake_db)

        # Must NOT raise ValueError: too many values to unpack.
        await calibration_check(bot_name="WeatherBot", cutoff="2026-07-13T16:02:29Z",
                                dedup_markets=True)


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

    def test_sql_filters_to_resolved_predictions_with_entry_event(self):
        """prediction_log.trade_executed and trade_side are NULL for WeatherBot
        rows in production. The trade-existence indicator is the JOIN to
        trade_events ENTRY (which itself filters bot_name=WeatherBot,
        event_type=ENTRY in the inner subquery), and the side comes from
        trade_events.side, not prediction_log.trade_side."""
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.resolution IS NOT NULL" in sql
        # Side comes from trade_events, not prediction_log
        assert "e_entry.side AS trade_side" in sql
        assert "pl.trade_executed" not in sql
        assert "pl.trade_side" not in sql

    def test_sql_uses_since_param(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.prediction_time >= :since_dt" in sql

    def test_sql_pulls_lead_time_from_event_data(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "(e_entry.event_data->>'lead_time_hours')::float" in sql
        assert "e_entry.event_data->>'lead_time_hours' IS NOT NULL" in sql

    def test_sql_uses_distinct_on_pattern(self):
        """Mirrors bot_pnl.py block 5: latest ENTRY per market wins.
        Selects side AND event_data so we can pull lead_time_hours and trade
        side from the same row."""
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "DISTINCT ON (market_id) market_id, side, event_data" in sql
        assert "ORDER BY market_id, event_time DESC" in sql

    def test_sql_selects_required_columns(self):
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "pl.predicted_prob" in sql
        assert "CASE WHEN pl.resolution = 'YES' THEN 1 ELSE 0 END AS outcome" in sql
        assert "e_entry.side AS trade_side" in sql

    def test_sql_orders_by_side_then_lead_time(self):
        """ORDER BY e_entry.side, lead_time_hours keeps deterministic output
        order for downstream Python grouping."""
        sql = _build_per_side_lead_time_sql(clean=False)
        assert "ORDER BY e_entry.side, lead_time_hours" in sql


class TestParseArgs:
    """CLI flag plumbing. Mirrors bot_pnl.py argparse pattern."""

    def test_defaults(self):
        ns = _parse_args([])
        assert ns.bot_name == ""
        assert ns.cutoff == ""
        assert ns.days == 90
        assert ns.since is None
        assert ns.clean is False
        assert ns.dedup_markets is False

    def test_dedup_markets_flag(self):
        ns = _parse_args(["WeatherBot", "--dedup-markets"])
        assert ns.dedup_markets is True

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


class TestDedupLatestPerMarket:
    """One long-open market re-logged 40+ times must not dominate a reliability
    bin. Dedup keeps exactly one row per market_id — its latest prediction_time.

    Row shape: (predicted_prob, outcome, bot_name, category, market_id, ptime).
    """

    @staticmethod
    def _row(prob, outcome, market_id, ptime):
        return (prob, outcome, "WeatherBot", "weather", market_id, ptime)

    def test_collapses_duplicates_to_latest(self):
        rows = [
            self._row(0.13, 1, "0x7cee", datetime(2026, 7, 8, 16, 0, 0)),
            self._row(0.16, 1, "0x7cee", datetime(2026, 7, 8, 20, 0, 0)),
            self._row(0.17, 1, "0x7cee", datetime(2026, 7, 9, 2, 0, 0)),  # latest
        ]
        out = _dedup_latest_per_market(rows)
        assert len(out) == 1
        assert out[0][0] == 0.17  # latest prediction's prob survives
        assert out[0][5] == datetime(2026, 7, 9, 2, 0, 0)

    def test_latest_wins_regardless_of_input_order(self):
        """Input is ORDER BY prediction_time in prod, but the collapse must be
        order-independent so a re-ordering upstream can't change the result."""
        latest = self._row(0.17, 1, "0x7cee", datetime(2026, 7, 9, 2, 0, 0))
        older = self._row(0.13, 0, "0x7cee", datetime(2026, 7, 8, 16, 0, 0))
        assert _dedup_latest_per_market([latest, older])[0][0] == 0.17
        assert _dedup_latest_per_market([older, latest])[0][0] == 0.17

    def test_distinct_markets_preserved(self):
        rows = [
            self._row(0.13, 1, "0x7cee", datetime(2026, 7, 8, 16, 0, 0)),
            self._row(0.13, 1, "0x7cee", datetime(2026, 7, 8, 20, 0, 0)),
            self._row(0.07, 0, "0x2aaa", datetime(2026, 7, 8, 17, 0, 0)),
            self._row(0.05, 1, "0x2d62", datetime(2026, 7, 8, 18, 0, 0)),
        ]
        out = _dedup_latest_per_market(rows)
        assert {r[4] for r in out} == {"0x7cee", "0x2aaa", "0x2d62"}
        assert len(out) == 3

    def test_single_row_unchanged(self):
        rows = [self._row(0.42, 0, "0xabc", datetime(2026, 7, 8, 16, 0, 0))]
        assert _dedup_latest_per_market(rows) == rows

    def test_empty(self):
        assert _dedup_latest_per_market([]) == []

    def test_distinct_models_not_collapsed(self):
        """S232 re-review c11: same market_id, DIFFERENT model_name must be
        kept as SEPARATE observations — the later-logged nowcast 0.44 row must
        NOT replace the main model's prediction (twin of write-side c2)."""
        def _row7(prob, mid, ptime, model):
            return (prob, 1, "WeatherBot", "weather", mid, ptime, model)
        main = _row7(0.62, "0x7cee", datetime(2026, 7, 9, 1, 0, 0), "weather_temperature")
        # nowcast logs LATER (would win a market_id-only collapse)
        nowc = _row7(0.44, "0x7cee", datetime(2026, 7, 9, 1, 30, 0), "weather_nowcast_peak")
        out = _dedup_latest_per_market([main, nowc])
        assert len(out) == 2
        assert {r[0] for r in out} == {0.62, 0.44}
        assert {r[6] for r in out} == {"weather_temperature", "weather_nowcast_peak"}

    def test_main_query_and_side_lead_exclude_nowcast(self):
        """S232 re-review c11: the main-model cuts filter out the nowcast
        model_name so S222/re-measure grade the deployed model cleanly."""
        assert "NOT LIKE '%nowcast%'" in _build_per_side_lead_time_sql(clean=False)
        assert "NOT LIKE '%nowcast%'" in _build_per_side_lead_time_sql(clean=True)
