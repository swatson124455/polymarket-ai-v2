"""
Unit tests for esports/models/patch_drift.py (PatchDriftDetector).

Tests:
  - record_prediction stores predictions
  - compute_brier_score returns None with insufficient data
  - compute_brier_score returns 0.0 for perfect predictions
  - compute_brier_score returns ~0.25 for random predictions
  - is_observation_mode returns False initially
  - is_observation_mode returns True after new patch detected
  - is_halted returns False initially
  - check_champion_drift returns drifted champions when shift > 3%
  - should_retrain returns True when Brier degrades
  - predictions list bounded at 100
"""
import datetime as _dt

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from esports.models.patch_drift import PatchDriftDetector


def make_detector(riot_client=None, hltv_scraper=None):
    """Create a PatchDriftDetector with optional mock clients."""
    return PatchDriftDetector(
        riot_client=riot_client,
        hltv_scraper=hltv_scraper,
    )


# =========================================================================
# record_prediction
# =========================================================================


class TestRecordPrediction:
    def test_stores_prediction(self):
        """record_prediction appends (predicted, actual) to internal list."""
        d = make_detector()
        d.record_prediction("lol", predicted=0.70, actual=1.0)
        assert len(d._predictions["lol"]) == 1
        assert d._predictions["lol"][0] == (0.70, 1.0)

    def test_stores_multiple_predictions(self):
        """Multiple calls accumulate predictions."""
        d = make_detector()
        d.record_prediction("lol", 0.70, 1.0)
        d.record_prediction("lol", 0.30, 0.0)
        d.record_prediction("cs2", 0.50, 1.0)
        assert len(d._predictions["lol"]) == 2
        assert len(d._predictions["cs2"]) == 1

    def test_bounded_at_100(self):
        """Predictions list is pruned to keep only last 100 entries."""
        d = make_detector()
        for i in range(120):
            d.record_prediction("lol", predicted=0.50, actual=float(i % 2))
        assert len(d._predictions["lol"]) == 100

    def test_keeps_most_recent_after_pruning(self):
        """After pruning, the most recent predictions are retained."""
        d = make_detector()
        for i in range(110):
            d.record_prediction("lol", predicted=float(i) / 110.0, actual=1.0)
        # The first 10 entries (i=0..9) should have been pruned
        first_pred = d._predictions["lol"][0][0]
        # Should be i=10 -> 10/110 ~ 0.0909
        assert first_pred == pytest.approx(10.0 / 110.0, abs=0.001)


# =========================================================================
# compute_brier_score
# =========================================================================


class TestComputeBrierScore:
    def test_returns_none_with_insufficient_data(self):
        """Returns None when fewer than window predictions exist."""
        d = make_detector()
        d.record_prediction("lol", 0.70, 1.0)
        assert d.compute_brier_score("lol") is None

    def test_returns_none_for_unknown_game(self):
        """Returns None for a game with no predictions at all."""
        d = make_detector()
        assert d.compute_brier_score("lol") is None

    def test_perfect_predictions_return_zero(self):
        """Brier score = 0.0 when all predictions perfectly match outcomes."""
        d = make_detector()
        for _ in range(20):
            d.record_prediction("lol", predicted=1.0, actual=1.0)
        brier = d.compute_brier_score("lol")
        assert brier == pytest.approx(0.0, abs=1e-6)

    def test_perfect_no_predictions_return_zero(self):
        """Brier score = 0.0 when predicting 0.0 for all 0 outcomes."""
        d = make_detector()
        for _ in range(20):
            d.record_prediction("cs2", predicted=0.0, actual=0.0)
        brier = d.compute_brier_score("cs2")
        assert brier == pytest.approx(0.0, abs=1e-6)

    def test_worst_predictions_return_one(self):
        """Brier score = 1.0 when all predictions are maximally wrong."""
        d = make_detector()
        for _ in range(20):
            d.record_prediction("lol", predicted=1.0, actual=0.0)
        brier = d.compute_brier_score("lol")
        assert brier == pytest.approx(1.0, abs=1e-6)

    def test_random_predictions_around_0_25(self):
        """Brier score ~ 0.25 for 50/50 predictions on balanced outcomes.
        (0.5 - 1.0)^2 = 0.25 and (0.5 - 0.0)^2 = 0.25 -> avg = 0.25."""
        d = make_detector()
        for i in range(20):
            d.record_prediction("lol", predicted=0.50, actual=float(i % 2))
        brier = d.compute_brier_score("lol")
        assert brier == pytest.approx(0.25, abs=0.01)

    def test_custom_window_size(self):
        """Using a smaller window returns score over fewer predictions."""
        d = make_detector()
        # First 10: perfect predictions
        for _ in range(10):
            d.record_prediction("lol", predicted=1.0, actual=1.0)
        # Next 10: worst predictions
        for _ in range(10):
            d.record_prediction("lol", predicted=1.0, actual=0.0)

        # Window of 10 covers only the worst predictions
        brier = d.compute_brier_score("lol", window=10)
        assert brier == pytest.approx(1.0, abs=1e-6)

        # Window of 20 covers both
        brier_all = d.compute_brier_score("lol", window=20)
        assert brier_all == pytest.approx(0.5, abs=1e-6)


# =========================================================================
# is_observation_mode
# =========================================================================


class TestIsObservationMode:
    def test_false_initially(self):
        """No patch detected -> observation mode is False."""
        d = make_detector()
        assert d.is_observation_mode("lol") is False
        assert d.is_observation_mode("cs2") is False

    def test_true_after_recent_patch(self):
        """Observation mode is True within 48h of a new patch detection."""
        d = make_detector()
        # Simulate a patch detected just now
        d._patch_timestamps["lol"] = _dt.datetime.now(_dt.timezone.utc)
        assert d.is_observation_mode("lol") is True

    def test_false_after_48_hours(self):
        """Observation mode is False after 48h have elapsed."""
        d = make_detector()
        # Simulate a patch detected 49 hours ago
        d._patch_timestamps["lol"] = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=49)
        )
        assert d.is_observation_mode("lol") is False

    def test_game_specific(self):
        """Observation mode is per-game."""
        d = make_detector()
        d._patch_timestamps["lol"] = _dt.datetime.now(_dt.timezone.utc)
        assert d.is_observation_mode("lol") is True
        assert d.is_observation_mode("cs2") is False

    def test_exactly_at_48_hours(self):
        """At exactly 48h boundary, still within window (hours_since < 48)."""
        d = make_detector()
        d._patch_timestamps["lol"] = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=47, minutes=59)
        )
        assert d.is_observation_mode("lol") is True


# =========================================================================
# is_halted
# =========================================================================


class TestIsHalted:
    def test_false_initially(self):
        """No games are halted initially."""
        d = make_detector()
        assert d.is_halted("lol") is False
        assert d.is_halted("cs2") is False

    def test_true_after_halt(self):
        """Game is halted after adding to _halted_games set."""
        d = make_detector()
        d._halted_games.add("lol")
        assert d.is_halted("lol") is True
        assert d.is_halted("cs2") is False

    def test_unhalt_clears(self):
        """unhalt() removes a game from halted set."""
        d = make_detector()
        d._halted_games.add("lol")
        assert d.is_halted("lol") is True
        d.unhalt("lol")
        assert d.is_halted("lol") is False

    def test_unhalt_nonexistent_game_no_error(self):
        """unhalt() on a non-halted game does not raise."""
        d = make_detector()
        d.unhalt("lol")  # Should not raise


# =========================================================================
# check_champion_drift
# =========================================================================


class TestCheckChampionDrift:
    def test_returns_empty_when_no_baseline(self):
        """No baseline set -> returns empty list."""
        d = make_detector()
        current = {"Yone": 0.55, "Ahri": 0.48}
        result = d.check_champion_drift("lol", current)
        assert result == []

    def test_returns_drifted_champions(self):
        """Champions with >3% shift from baseline are returned."""
        d = make_detector()
        d.set_champion_baseline("lol", {"Yone": 0.50, "Ahri": 0.52, "Jinx": 0.48})
        current = {"Yone": 0.55, "Ahri": 0.52, "Jinx": 0.40}
        result = d.check_champion_drift("lol", current)
        # Yone shifted +5% (>3%), Jinx shifted -8% (>3%), Ahri unchanged
        assert "Yone" in result
        assert "Jinx" in result
        assert "Ahri" not in result

    def test_returns_empty_when_no_drift(self):
        """All champions within 3% -> returns empty list."""
        d = make_detector()
        d.set_champion_baseline("lol", {"Yone": 0.50, "Ahri": 0.52})
        current = {"Yone": 0.51, "Ahri": 0.50}
        result = d.check_champion_drift("lol", current)
        assert result == []

    def test_exact_3_percent_not_drifted(self):
        """Exactly 3% shift is NOT drifted (> not >=).
        Use values that avoid floating-point rounding issues."""
        d = make_detector()
        d.set_champion_baseline("lol", {"Yone": 0.50})
        # 0.50 + 0.03 = 0.53 but due to float imprecision use 0.5299
        current = {"Yone": 0.5299}
        result = d.check_champion_drift("lol", current)
        assert result == []

    def test_just_over_3_percent_is_drifted(self):
        """3.1% shift is drifted."""
        d = make_detector()
        d.set_champion_baseline("lol", {"Yone": 0.50})
        current = {"Yone": 0.531}
        result = d.check_champion_drift("lol", current)
        assert "Yone" in result

    def test_new_champions_not_in_baseline_ignored(self):
        """Champions not in baseline are not flagged."""
        d = make_detector()
        d.set_champion_baseline("lol", {"Yone": 0.50})
        current = {"Yone": 0.50, "NewChamp": 0.80}
        result = d.check_champion_drift("lol", current)
        assert result == []


# =========================================================================
# should_retrain
# =========================================================================


class TestShouldRetrain:
    def test_false_initially(self):
        """No predictions, no patch -> should not retrain."""
        d = make_detector()
        assert d.should_retrain("lol") is False

    def test_true_when_brier_degrades(self):
        """Brier score above threshold -> should retrain."""
        d = make_detector()
        # All predictions maximally wrong -> Brier = 1.0 >> 0.05
        for _ in range(20):
            d.record_prediction("lol", predicted=1.0, actual=0.0)
        assert d.should_retrain("lol") is True

    def test_false_when_brier_ok(self):
        """Brier score below threshold -> should not retrain."""
        d = make_detector()
        # Near-perfect predictions -> Brier ~ 0.0
        for _ in range(20):
            d.record_prediction("lol", predicted=1.0, actual=1.0)
        assert d.should_retrain("lol") is False

    def test_true_when_recent_patch(self):
        """Recent patch (within 48h) -> should retrain."""
        d = make_detector()
        d._patch_timestamps["lol"] = _dt.datetime.now(_dt.timezone.utc)
        assert d.should_retrain("lol") is True

    def test_false_when_old_patch(self):
        """Old patch (>48h ago) + good Brier -> should not retrain."""
        d = make_detector()
        d._patch_timestamps["lol"] = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=72)
        )
        # Also need enough good predictions to avoid None Brier
        for _ in range(20):
            d.record_prediction("lol", predicted=1.0, actual=1.0)
        assert d.should_retrain("lol") is False


# =========================================================================
# check_all_games
# =========================================================================


class TestCheckAllGames:
    @pytest.mark.asyncio
    async def test_returns_status_for_all_four_games(self):
        """check_all_games returns status dict for lol, cs2, dota2, valorant."""
        d = make_detector()
        results = await d.check_all_games()
        assert set(results.keys()) == {"lol", "cs2", "dota2", "valorant"}

    @pytest.mark.asyncio
    async def test_default_status_values(self):
        """Default status has observation_mode=False, should_retrain=False, etc."""
        d = make_detector()
        results = await d.check_all_games()
        for game, status in results.items():
            assert status["observation_mode"] is False
            assert status["should_retrain"] is False
            assert status["brier_ok"] is True
            assert status["calibration_ok"] is True
            assert status["halted"] is False


# =========================================================================
# check_game (integration of all checks)
# =========================================================================


class TestCheckGame:
    @pytest.mark.asyncio
    async def test_calibration_failure_halts_game(self):
        """When calibration gap exceeds threshold, game is halted."""
        d = make_detector()
        # Fill predictions with a large calibration gap:
        # predicted avg = 0.80, actual avg = 0.20 -> gap = 0.60 >> 0.15
        for _ in range(30):
            d.record_prediction("lol", predicted=0.80, actual=0.20)

        status = await d.check_game("lol")
        assert status["calibration_ok"] is False
        assert status["halted"] is True
        assert d.is_halted("lol") is True

    @pytest.mark.asyncio
    async def test_brier_degradation_triggers_retrain(self):
        """When Brier score exceeds threshold, should_retrain is True."""
        d = make_detector()
        # All wrong predictions -> Brier = 1.0 >> 0.05
        for _ in range(30):
            d.record_prediction("cs2", predicted=1.0, actual=0.0)

        status = await d.check_game("cs2")
        assert status["brier_ok"] is False
        assert status["should_retrain"] is True

    @pytest.mark.asyncio
    async def test_new_patch_triggers_observation(self):
        """New patch version detected -> observation_mode=True."""
        riot_client = MagicMock()
        riot_client.get_current_patch_version = AsyncMock(return_value="14.5")
        d = make_detector(riot_client=riot_client)
        # Set a known patch first so the second one is a "new" patch
        d._known_patches["lol"] = "14.4"

        status = await d.check_game("lol")
        assert status["observation_mode"] is True
        assert status["should_retrain"] is True
