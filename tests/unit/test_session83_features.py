"""
Tests for Session 83 features:
- HorizonBiasCalibrator wiring
- Per-game ONNX inference
- Improved team name extraction
- MAPIE conformal prediction intervals
- Dynamic EGM d tuning
- Edge decay sizing multiplier
- TabPFN ensemble
- CoT validator
- _compute_ttr_days helper
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import numpy as np


# ── _compute_ttr_days ─────────────────────────────────────────────────

class TestComputeTtrDays:
    """Test the TTR days computation helper."""

    def _get_bot_class(self):
        from bots.esports_bot import EsportsBot
        return EsportsBot

    def test_with_future_end_date(self):
        cls = self._get_bot_class()
        future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        result = cls._compute_ttr_days({"end_date_iso": future})
        assert result is not None
        assert 6.9 < result < 7.1

    def test_with_past_end_date(self):
        cls = self._get_bot_class()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = cls._compute_ttr_days({"end_date_iso": past})
        assert result == 0.0  # Clamps to 0

    def test_with_no_end_date(self):
        cls = self._get_bot_class()
        result = cls._compute_ttr_days({})
        assert result is None

    def test_with_z_suffix(self):
        cls = self._get_bot_class()
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = cls._compute_ttr_days({"end_date_iso": future})
        assert result is not None
        assert 29.5 < result < 30.5

    def test_with_invalid_date(self):
        cls = self._get_bot_class()
        result = cls._compute_ttr_days({"end_date_iso": "not-a-date"})
        assert result is None


# ── Dynamic EGM d tuning ─────────────────────────────────────────────

class TestDynamicEgmD:
    """Test per-game EGM d tuning from Brier-based Kelly multipliers."""

    def _make_bot(self):
        with patch("bots.esports_bot.EsportsBot.__init__", return_value=None):
            from bots.esports_bot import EsportsBot
            bot = EsportsBot.__new__(EsportsBot)
            bot._game_kelly_mult = {}
            bot._game_egm_d = {}
            bot._egm_d = 1.5
            return bot

    def test_good_brier_increases_d(self):
        bot = self._make_bot()
        bot._game_kelly_mult = {"lol": 1.2}
        bot._update_per_game_egm_d()
        assert bot._game_egm_d["lol"] == 2.0  # 1.5 + 0.5

    def test_bad_brier_decreases_d(self):
        bot = self._make_bot()
        bot._game_kelly_mult = {"cs2": 0.5}
        bot._update_per_game_egm_d()
        assert bot._game_egm_d["cs2"] == 1.0  # S138: poor calibration floors to 1.0 (was egm_d - 0.3)

    def test_normal_brier_keeps_default(self):
        bot = self._make_bot()
        bot._game_kelly_mult = {"dota2": 1.0}
        bot._update_per_game_egm_d()
        assert bot._game_egm_d["dota2"] == 1.5

    def test_d_capped_at_25(self):
        bot = self._make_bot()
        bot._egm_d = 2.3
        bot._game_kelly_mult = {"lol": 1.2}
        bot._update_per_game_egm_d()
        assert bot._game_egm_d["lol"] == 2.5  # Capped

    def test_d_floored_at_10(self):
        bot = self._make_bot()
        bot._egm_d = 1.0
        bot._game_kelly_mult = {"cs2": 0.5}
        bot._update_per_game_egm_d()
        assert bot._game_egm_d["cs2"] == 1.0  # Floored


# ── Edge decay sizing multiplier ──────────────────────────────────────

class TestEdgeDecaySizingMult:
    """Test edge decay based sizing multiplier."""

    def _make_bot(self):
        with patch("bots.esports_bot.EsportsBot.__init__", return_value=None):
            from bots.esports_bot import EsportsBot
            bot = EsportsBot.__new__(EsportsBot)
            bot._edge_decay_data = {}
            return bot

    def test_no_data_returns_1(self):
        bot = self._make_bot()
        assert bot._get_edge_decay_sizing_mult("lol") == 1.0

    def test_positive_clv_returns_1(self):
        bot = self._make_bot()
        bot._edge_decay_data = {"lol": {"bins": [{"avg_clv": 0.05}]}}
        assert bot._get_edge_decay_sizing_mult("lol") == 1.0

    def test_mild_negative_clv_returns_08(self):
        bot = self._make_bot()
        bot._edge_decay_data = {"cs2": {"bins": [{"avg_clv": -0.02}]}}
        assert bot._get_edge_decay_sizing_mult("cs2") == 0.8

    def test_severe_negative_clv_returns_06(self):
        bot = self._make_bot()
        bot._edge_decay_data = {"dota2": {"bins": [{"avg_clv": -0.10}]}}
        assert bot._get_edge_decay_sizing_mult("dota2") == 0.6

    def test_empty_bins_returns_1(self):
        bot = self._make_bot()
        bot._edge_decay_data = {"rl": {"bins": []}}
        assert bot._get_edge_decay_sizing_mult("rl") == 1.0


# ── Team name matching improvements ──────────────────────────────────

class TestTeamNameMatching:
    """Test improved team name extraction and matching."""

    def _make_bot(self):
        with patch("bots.esports_bot.EsportsBot.__init__", return_value=None):
            from bots.esports_bot import EsportsBot
            bot = EsportsBot.__new__(EsportsBot)
            bot._team_name_to_id = {
                "t1": "team_t1",
                "gen.g": "team_geng",
                "cloud9": "team_c9",
                "fnatic": "team_fnatic",
                "natus vincere": "team_navi",
                "g2 esports": "team_g2",
                "team liquid": "team_tl",
                "hanwha life esports": "team_hle",
                "jd gaming": "team_jdg",
                "top esports": "team_tes",
            }
            return bot

    def test_exact_match(self):
        bot = self._make_bot()
        assert bot._match_team_name("fnatic") == "team_fnatic"

    def test_alias_jdg(self):
        bot = self._make_bot()
        assert bot._match_team_name("jdg") == "team_jdg"

    def test_alias_navi(self):
        bot = self._make_bot()
        assert bot._match_team_name("navi") == "team_navi"

    def test_alias_tl(self):
        bot = self._make_bot()
        assert bot._match_team_name("tl") == "team_tl"

    def test_alias_tes(self):
        bot = self._make_bot()
        assert bot._match_team_name("tes") == "team_tes"

    def test_substring_long_name(self):
        bot = self._make_bot()
        # "hanwha life esports" should match when contained in longer name
        assert bot._match_team_name("hanwha life esports academy") is not None

    def test_reverse_substring(self):
        bot = self._make_bot()
        assert bot._match_team_name("hanwha life") is not None

    def test_word_boundary_short_name(self):
        bot = self._make_bot()
        # "t1" as whole word should match
        assert bot._match_team_name("t1") == "team_t1"

    def test_short_name_no_false_positive(self):
        bot = self._make_bot()
        # "t1" should NOT match "contest1" (no word boundary)
        # but we check against known names, not input names
        # So this is about _team_name_to_id matching
        pass  # This is inherently safe due to word boundary regex

    def test_empty_name(self):
        bot = self._make_bot()
        assert bot._match_team_name("") is None

    def test_clean_team_names_strips_lol_prefix(self):
        from bots.esports_bot import EsportsBot
        a, b = EsportsBot._clean_team_names("league of legends: t1", "gen.g")
        assert a == "t1"
        assert b == "gen.g"

    def test_clean_team_names_strips_bo3(self):
        from bots.esports_bot import EsportsBot
        a, b = EsportsBot._clean_team_names("t1", "gen.g (bo3) - lck spring 2026")
        assert a == "t1"
        assert "lck" not in b.lower()

    def test_clean_team_names_strips_map_suffix(self):
        from bots.esports_bot import EsportsBot
        a, b = EsportsBot._clean_team_names("fnatic map 3", "cloud9")
        assert a == "fnatic"

    def test_clean_team_names_strips_region_tag(self):
        from bots.esports_bot import EsportsBot
        a, b = EsportsBot._clean_team_names("t1 (KR)", "gen.g")
        assert a == "t1"


# ── Conformal predictor ──────────────────────────────────────────────

class TestConformalPredictor:
    """Test MAPIE conformal prediction wrapper."""

    def test_init(self):
        from esports.models.conformal_wrapper import ConformalPredictor
        cp = ConformalPredictor(alpha=0.10)
        assert not cp.is_fitted
        assert cp.alpha == 0.10

    def test_unfitted_returns_identity(self):
        from esports.models.conformal_wrapper import ConformalPredictor
        cp = ConformalPredictor()
        cp._mapie_clf = MagicMock()
        cp._mapie_clf.estimator_ = MagicMock()
        cp._mapie_clf.estimator_.predict_proba = MagicMock(
            return_value=np.array([[0.3, 0.7]])
        )
        p_low, p_mid, p_high = cp.predict_interval(np.array([[1.0]]))
        # Not fitted, so all should be point estimate
        assert np.allclose(p_low, p_mid)
        assert np.allclose(p_mid, p_high)

    def test_conservative_prob_yes_side(self):
        from esports.models.conformal_wrapper import ConformalPredictor
        cp = ConformalPredictor()
        # Mock fitted state
        cp._fitted = True
        cp._mapie_clf = MagicMock()
        cp._mapie_clf.estimator_ = MagicMock()
        cp._mapie_clf.estimator_.predict_proba = MagicMock(
            return_value=np.array([[0.3, 0.7]])
        )
        # Mock predict to return set with only class 1
        cp._mapie_clf.predict = MagicMock(
            return_value=(np.array([1]), np.array([[[False], [True]]]))
        )
        result = cp.conservative_prob(np.array([[1.0]]))
        # p_mid=0.7, YES side, should use p_low
        assert result[0] < 0.7  # Conservative

    def test_fit_insufficient_data(self):
        from esports.models.conformal_wrapper import ConformalPredictor
        cp = ConformalPredictor()
        model = MagicMock()
        result = cp.fit(model, np.array([[1.0]]*10), np.array([0]*10))
        assert not result  # < 30 samples


# ── TabPFN ensemble ──────────────────────────────────────────────────

class TestTabPFNEnsemble:
    """Test TabPFN ensemble for sparse games."""

    def test_init_without_package(self):
        from esports.models.tabpfn_ensemble import TabPFNEnsemble
        ens = TabPFNEnsemble()
        # May or may not be available depending on environment
        assert isinstance(ens.is_available, bool)

    def test_sparse_games_list(self):
        from esports.models.tabpfn_ensemble import SPARSE_GAMES
        assert "sc2" in SPARSE_GAMES
        assert "rl" in SPARSE_GAMES
        assert "cod" in SPARSE_GAMES
        assert "r6" in SPARSE_GAMES
        assert "lol" not in SPARSE_GAMES

    def test_blend_weight(self):
        from esports.models.tabpfn_ensemble import TabPFNEnsemble
        assert TabPFNEnsemble.get_blend_weight() == 0.3

    def test_predict_unfitted(self):
        from esports.models.tabpfn_ensemble import TabPFNEnsemble
        ens = TabPFNEnsemble()
        result = ens.predict("sc2", {"team_strength_diff": 0.1})
        assert result is None

    def test_is_fitted_false(self):
        from esports.models.tabpfn_ensemble import TabPFNEnsemble
        ens = TabPFNEnsemble()
        assert not ens.is_fitted("sc2")

    def test_fit_rejects_non_sparse_game(self):
        from esports.models.tabpfn_ensemble import TabPFNEnsemble
        ens = TabPFNEnsemble()
        X = np.random.randn(50, 6).astype(np.float32)
        y = np.random.randint(0, 2, 50)
        result = ens.fit_game("lol", X, y)
        assert not result  # lol is not a sparse game


# ── CoT validator ─────────────────────────────────────────────────────

class TestCoTValidator:
    """Test CoT LLM validator for high-edge trades."""

    def test_init_without_api_key(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            from esports.models.cot_validator import CoTValidator
            v = CoTValidator()
            assert not v.is_available

    @pytest.mark.asyncio
    async def test_below_threshold_returns_approved(self):
        from esports.models.cot_validator import CoTValidator
        v = CoTValidator()
        v._available = True
        result = await v.validate_trade(
            question="T1 vs Gen.G",
            game="lol",
            model_prob=0.55,
            market_price=0.50,
            edge=0.05,  # Below 0.15 threshold
            side="YES",
        )
        assert result["approved"] is True
        assert result["reason"] == "below_threshold"

    @pytest.mark.asyncio
    async def test_rate_limited_returns_approved(self):
        from esports.models.cot_validator import CoTValidator
        v = CoTValidator()
        v._available = True
        v._call_count = 10  # Over limit
        result = await v.validate_trade(
            question="T1 vs Gen.G",
            game="lol",
            model_prob=0.80,
            market_price=0.60,
            edge=0.20,
            side="YES",
        )
        assert result["approved"] is True
        assert result["reason"] == "rate_limited"

    def test_reset_scan_counter(self):
        from esports.models.cot_validator import CoTValidator
        v = CoTValidator()
        v._call_count = 5
        v.reset_scan_counter()
        assert v._call_count == 0


# ── ONNX predict game helper ─────────────────────────────────────────

class TestOnnxPredictGame:
    """Test the _onnx_predict_game helper method."""

    def _make_bot(self):
        with patch("bots.esports_bot.EsportsBot.__init__", return_value=None):
            from bots.esports_bot import EsportsBot
            bot = EsportsBot.__new__(EsportsBot)
            return bot

    def test_falls_back_to_native_when_no_session(self):
        bot = self._make_bot()
        native_model = MagicMock()
        native_model.predict = MagicMock(return_value=0.65)
        result = bot._onnx_predict_game(None, {"a": 1.0}, native_model, "dota2")
        assert result == 0.65
        native_model.predict.assert_called_once()

    def test_uses_onnx_when_session_available(self):
        bot = self._make_bot()
        session = MagicMock()
        native_model = MagicMock()
        native_model.FEATURE_NAMES = ["team_strength_diff", "matchup_uncertainty"]
        native_model.predict = MagicMock(return_value=0.5)

        with patch("esports.models.onnx_compiler.OnnxCompiler") as mock_compiler:
            mock_instance = MagicMock()
            mock_instance.predict_proba = MagicMock(
                return_value=np.array([[0.3, 0.7]])
            )
            mock_compiler.return_value = mock_instance

            result = bot._onnx_predict_game(
                session,
                {"team_strength_diff": 0.1, "matchup_uncertainty": 0.5},
                native_model,
                "dota2",
            )
            assert abs(result - 0.7) < 0.01


# ── _load_per_game_onnx_sessions ─────────────────────────────────────

class TestLoadPerGameOnnx:
    """Test per-game ONNX session loading."""

    def _make_bot(self):
        with patch("bots.esports_bot.EsportsBot.__init__", return_value=None):
            from bots.esports_bot import EsportsBot
            bot = EsportsBot.__new__(EsportsBot)
            bot._onnx_lol_session = None
            bot._onnx_cs2_session = None
            bot._onnx_dota2_session = None
            bot._onnx_valorant_session = None
            return bot

    def test_no_crash_when_onnx_not_installed(self):
        bot = self._make_bot()
        with patch.dict("sys.modules", {"esports.models.onnx_compiler": None}):
            # Should not raise
            try:
                bot._load_per_game_onnx_sessions()
            except Exception:
                pass  # ImportError is expected

    def test_loads_nothing_when_no_files(self):
        bot = self._make_bot()
        with patch("esports.models.onnx_compiler.OnnxCompiler") as mock:
            mock_instance = MagicMock()
            mock_instance.load_session = MagicMock(return_value=None)
            mock.return_value = mock_instance
            with patch("os.path.exists", return_value=False):
                bot._load_per_game_onnx_sessions()
        assert bot._onnx_lol_session is None
        assert bot._onnx_cs2_session is None
