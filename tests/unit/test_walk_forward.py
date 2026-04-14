"""Tests for B1: Walk-forward backtester."""
import pytest
from datetime import datetime
from unittest.mock import MagicMock

from esports_v2.backtest.walk_forward import (
    BacktestResult,
    FoldResult,
    _build_record,
    _parse_date,
    generate_date_folds,
    run_walk_forward,
)
from esports_v2.data.normalizer import RawMatch
from esports_v2.ratings.trinity import TrinityPrediction


class TestParseDate:
    def test_iso_format(self):
        d = _parse_date("2024-06-15T12:00:00")
        assert d.year == 2024
        assert d.month == 6

    def test_none(self):
        assert _parse_date(None) == datetime.min

    def test_empty(self):
        assert _parse_date("") == datetime.min


class TestBuildRecord:
    def test_basic(self):
        raw = RawMatch(
            match_id="m1", game="cs2", team_a="T1", team_b="T2",
            winner="T1", match_date="2024-06-15", event_tier="a_tier",
            is_lan=True, best_of=3,
        )
        pred = TrinityPrediction(
            team_a="T1", team_b="T2",
            p_elo=0.6, p_glicko=0.58, p_openskill=0.62,
            trinity_spread=0.04, trinity_mean=0.6,
        )
        record = _build_record(raw, pred)
        assert record["match_id"] == "m1"
        assert record["actual"] == 1
        assert record["p_elo"] == 0.6
        assert record["is_lan"] is True

    def test_team_b_wins(self):
        raw = RawMatch(
            match_id="m2", game="lol", team_a="T1", team_b="T2",
            winner="T2",
        )
        pred = TrinityPrediction(
            team_a="T1", team_b="T2",
            p_elo=0.4, p_glicko=0.42, p_openskill=0.38,
            trinity_spread=0.04, trinity_mean=0.4,
        )
        record = _build_record(raw, pred)
        assert record["actual"] == 0


class TestGenerateFolds:
    def _make_matches(self, n_months, per_month=10, start_year=2024):
        """Create matches spread over months, multiple per month."""
        matches = []
        idx = 0
        for i in range(n_months):
            month = (i % 12) + 1
            year = start_year + (i // 12)
            for d in range(per_month):
                day = min(d + 1, 28)
                matches.append(RawMatch(
                    match_id=f"m{idx}",
                    game="cs2",
                    team_a="T1",
                    team_b="T2",
                    winner="T1",
                    match_date=f"{year}-{month:02d}-{day:02d}T12:00:00",
                ))
                idx += 1
        return matches

    def test_generates_folds(self):
        matches = self._make_matches(24, per_month=10)  # 240 matches over 2 years
        folds = generate_date_folds(matches, min_train_months=3, fold_months=1)
        assert len(folds) > 0
        for train_idx, test_idx in folds:
            assert len(train_idx) > 0
            assert len(test_idx) > 0
            # No overlap
            assert not set(train_idx) & set(test_idx)

    def test_temporal_ordering(self):
        matches = self._make_matches(24, per_month=10)
        folds = generate_date_folds(matches, min_train_months=3, fold_months=1)
        for train_idx, test_idx in folds:
            assert max(train_idx) < min(test_idx)

    def test_empty_matches(self):
        assert generate_date_folds([], min_train_months=3) == []

    def test_too_few_matches(self):
        matches = self._make_matches(2, per_month=3)
        folds = generate_date_folds(matches, min_train_months=3)
        assert len(folds) == 0


class TestRunWalkForward:
    def test_basic_run(self):
        """Test walk-forward with a mock pipeline."""
        matches = []
        for i in range(120):
            month = (i % 12) + 1
            year = 2024 + (i // 12)
            day = min((i % 28) + 1, 28)
            matches.append(RawMatch(
                match_id=f"m{i}",
                game="cs2",
                team_a="TeamA",
                team_b="TeamB",
                winner="TeamA" if i % 2 == 0 else "TeamB",
                match_date=f"{year}-{month:02d}-{day:02d}T12:00:00",
                best_of=3,
            ))

        # Mock pipeline
        pipeline = MagicMock()
        pipeline.predict.return_value = {
            "p_model": 0.6,
            "is_singleton": True,
            "kelly_fraction": 0.05,
            "stake": 50.0,
            "conformal_set": [1],
            "p_raw": 0.6,
            "p_lower": 0.55,
            "p_upper": 0.65,
            "edge": 0.1,
            "market_price": 0.5,
        }

        result = run_walk_forward(
            matches, pipeline, min_train_months=3, fold_months=1,
        )
        assert isinstance(result, BacktestResult)
        assert len(result.folds) > 0
        assert len(result.all_predictions) > 0
        assert pipeline.fit.called
        assert pipeline.predict.called

    def test_empty_input(self):
        pipeline = MagicMock()
        result = run_walk_forward([], pipeline)
        assert len(result.folds) == 0
        assert len(result.all_predictions) == 0
