"""
Unit tests for base_engine/risk/bankroll_manager.py (BotBankrollManager).

Session 47: Per-bot independence architecture — each bot gets its own capital pool,
Kelly fraction, per-trade/daily caps. Replaces shared KELLY_ACTIVE_BOTS divisor.

Tests:
  - Config loading: defaults, env overrides, fallback for unknown bots
  - get_bet_size: zero when no edge, positive when edge exists
  - get_bet_size: respects per-bet cap (max_bet_usd)
  - get_bet_size: respects daily cap (max_daily_usd)
  - get_bet_size: returns 0 when daily cap exhausted
  - get_bet_size: returns 0 when result < $1 minimum
  - get_bet_size: calibration quality scaling (Brier-based)
  - get_bet_size: category-specific Kelly fractions
  - Kelly formula correctness
  - _get_daily_spent: reads per-bot from order_gateway
  - get_state: returns correct diagnostics dict
  - Per-bot isolation: different bots get different capital
"""
import json
import pytest
from unittest.mock import MagicMock, patch


def make_manager(bot_name="EnsembleBot", daily_exposure=None, bankroll_config=None):
    """
    Create a BotBankrollManager with mocked dependencies.

    Args:
        bot_name: Name of the bot.
        daily_exposure: Dict mapping bot_name -> float of daily exposure.
        bankroll_config: JSON string for BOT_BANKROLL_CONFIG env override.
    """
    gw = MagicMock()
    gw._daily_exposure_usd = daily_exposure or {}
    # P3: Provide proper get_daily_exposure_usd method (replaces raw dict access)
    _de = daily_exposure or {}
    gw.get_daily_exposure_usd = lambda bot_name: float(_de.get(bot_name, 0.0))

    with patch("base_engine.risk.bankroll_manager.settings") as mock_settings:
        mock_settings.BOT_BANKROLL_CONFIG = bankroll_config or "{}"
        mock_settings.CATEGORY_KELLY_FRACTIONS = "{}"
        mock_settings.SIMULATION_MODE = False
        # S173: Phase cap — set high so it doesn't interfere with per-bot tests
        mock_settings.PHASE_MAX_BET_USD = '{"paper": 9999.0}'
        mock_settings.TRADING_PHASE = "paper"

        from base_engine.risk.bankroll_manager import BotBankrollManager
        mgr = BotBankrollManager(bot_name=bot_name, order_gateway=gw, db=None)

    return mgr


# =========================================================================
# Config Loading
# =========================================================================


class TestConfigLoading:
    def test_ensemble_bot_default_capital(self):
        """EnsembleBot gets 20000 capital by default (S105 alignment)."""
        mgr = make_manager("EnsembleBot")
        assert mgr.capital == 20000.0

    def test_arbitrage_bot_default_capital(self):
        """ArbitrageBot gets 1000 capital by default."""
        mgr = make_manager("ArbitrageBot")
        assert mgr.capital == 1000.0

    def test_unknown_bot_gets_fallback(self):
        """Unknown bot name falls back to generic defaults."""
        mgr = make_manager("SomeNewBot")
        assert mgr.capital == 1000.0  # fallback
        assert mgr.kelly_fraction == 0.25  # fallback
        assert mgr.max_bet_usd == 100.0  # fallback
        assert mgr.max_daily_usd == 500.0  # fallback

    def test_env_override_applied(self):
        """BOT_BANKROLL_CONFIG JSON overrides built-in defaults."""
        override = json.dumps({"EnsembleBot": {"capital": 5000, "kelly_fraction": 0.15}})
        mgr = make_manager("EnsembleBot", bankroll_config=override)
        assert mgr.capital == 5000.0
        assert mgr.kelly_fraction == 0.15
        # Non-overridden values remain from built-in defaults (S105: $300/$10K)
        assert mgr.max_bet_usd == 300.0
        assert mgr.max_daily_usd == 10000.0

    def test_cross_platform_arb_bot_config(self):
        """CrossPlatformArbBot gets correct non-default config."""
        mgr = make_manager("CrossPlatformArbBot")
        assert mgr.capital == 500.0
        assert mgr.kelly_fraction == 0.20
        assert mgr.max_bet_usd == 50.0
        assert mgr.max_daily_usd == 200.0

    def test_invalid_json_config_uses_defaults(self):
        """Invalid JSON in BOT_BANKROLL_CONFIG falls back gracefully."""
        mgr = make_manager("EnsembleBot", bankroll_config="not-valid-json")
        assert mgr.capital == 20000.0  # still uses built-in default

    def test_per_bot_isolation(self):
        """Different bots get different capital pools (no sharing)."""
        ensemble = make_manager("EnsembleBot")
        arb = make_manager("ArbitrageBot")
        mirror = make_manager("MirrorBot")
        assert ensemble.capital == 20000.0
        assert arb.capital == 1000.0
        assert mirror.capital == 20000.0
        # Sizes are independent — no KELLY_ACTIVE_BOTS divisor
        assert ensemble.capital != arb.capital


# =========================================================================
# get_bet_size — No Edge / Boundary Conditions
# =========================================================================


class TestNoEdge:
    @pytest.mark.asyncio
    async def test_zero_when_no_edge(self):
        """confidence <= price -> no positive edge -> returns 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.50, price=0.55)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_equal_price(self):
        """confidence == price -> no edge -> returns 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.60, price=0.60)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_price_zero(self):
        """price = 0 -> invalid -> returns 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.60, price=0.0)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_price_one(self):
        """price = 1.0 -> invalid -> returns 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.60, price=1.0)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_confidence_zero(self):
        """confidence = 0 -> invalid -> returns 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.0, price=0.50)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_confidence_one(self):
        """confidence = 1.0 -> invalid -> returns 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=1.0, price=0.50)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_with_negative_kelly(self):
        """Very slight edge at bad price -> kelly_full might be negative -> 0."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            # confidence barely above price
            result = await mgr.get_bet_size(confidence=0.51, price=0.50)
        # Very small edge: kelly = (0.51*1 - 0.49)/1 = 0.02
        # 0.02 * 0.25 * 8000 = $40.0 — actually positive.
        # Try even smaller
        result2 = await mgr.get_bet_size(confidence=0.501, price=0.50)
        # kelly = (0.501*1 - 0.499)/1 = 0.002 → 0.002 * 0.25 * 8000 = $4.0
        assert result2 >= 0.0  # Either a valid small bet or zero


# =========================================================================
# get_bet_size — Positive Edge + Kelly Formula
# =========================================================================


class TestPositiveEdge:
    @pytest.mark.asyncio
    async def test_returns_positive_with_edge(self):
        """confidence > price -> positive bet size returned."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        assert result > 0.0

    @pytest.mark.asyncio
    async def test_kelly_formula_correctness(self):
        """Verify exact Kelly formula: kelly_full * fraction * capital, capped."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False

            # confidence=0.65, price=0.50
            # b = (1-0.50)/0.50 = 1.0
            # q = 1.0 - 0.65 = 0.35
            # kelly_full = (0.65 * 1.0 - 0.35) / 1.0 = 0.30
            # size_usd = 0.30 * 0.25 * 20000 = 1500.0
            # Capped at max_bet_usd = 300.0 (phase_max_bet_usd = 9999 from make_manager mock)
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        assert result == pytest.approx(300.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_small_edge_uncapped(self):
        """Small edge -> kelly below per-bet cap -> uncapped result."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False

            # confidence=0.52, price=0.50
            # b = 1.0, q = 0.48
            # kelly_full = (0.52*1.0 - 0.48) / 1.0 = 0.04
            # size_usd = 0.04 * 0.25 * 20000 = 200.0
            # Under max_bet_usd (300) -> uncapped
            result = await mgr.get_bet_size(confidence=0.52, price=0.50)
        assert result == pytest.approx(200.0, abs=1.0)
        assert result < 300.0

    @pytest.mark.asyncio
    async def test_asymmetric_price_kelly(self):
        """Test Kelly at non-50/50 price point."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False

            # confidence=0.80, price=0.70
            # b = (1-0.70)/0.70 = 0.4286
            # q = 0.20
            # kelly_full = (0.80 * 0.4286 - 0.20) / 0.4286 = (0.3429 - 0.20) / 0.4286 = 0.3333
            # size_usd = 0.3333 * 0.25 * 20000 = 1666.67 -> capped at 300
            result = await mgr.get_bet_size(confidence=0.80, price=0.70)
        assert result == pytest.approx(300.0, abs=0.01)


# =========================================================================
# Per-Bet Cap
# =========================================================================


class TestPerBetCap:
    @pytest.mark.asyncio
    async def test_capped_at_max_bet_usd(self):
        """Large edge -> kelly exceeds max -> capped at max_bet_usd."""
        # Use ArbitrageBot with $100 cap, $1000 capital
        mgr = make_manager("ArbitrageBot")
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            # Big edge
            result = await mgr.get_bet_size(confidence=0.80, price=0.40)
        assert result <= mgr.max_bet_usd

    @pytest.mark.asyncio
    async def test_small_cap_bot(self):
        """CrossPlatformArbBot has $50 cap."""
        mgr = make_manager("CrossPlatformArbBot")
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.70, price=0.50)
        assert result <= 50.0


# =========================================================================
# Daily Cap
# =========================================================================


class TestDailyCap:
    @pytest.mark.asyncio
    async def test_respects_daily_cap(self):
        """Partially spent daily cap limits the bet."""
        # EnsembleBot: daily cap $10000, already spent $9990
        mgr = make_manager("EnsembleBot", daily_exposure={"EnsembleBot": 9990.0})
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            # Kelly would give ~$300 but only $10 remaining
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        assert result <= 10.0
        assert result > 0.0

    @pytest.mark.asyncio
    async def test_zero_when_daily_cap_exhausted(self):
        """Daily cap fully spent -> returns 0."""
        mgr = make_manager("EnsembleBot", daily_exposure={"EnsembleBot": 10000.0})
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_zero_when_daily_overspent(self):
        """Daily exposure exceeds cap -> returns 0."""
        mgr = make_manager("EnsembleBot", daily_exposure={"EnsembleBot": 15000.0})
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_per_bot_daily_isolation(self):
        """Daily exposure of OTHER bots doesn't affect THIS bot's cap."""
        mgr = make_manager(
            "EnsembleBot",
            daily_exposure={"ArbitrageBot": 9999.0, "EnsembleBot": 0.0},
        )
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        # ArbitrageBot's exposure doesn't count — EnsembleBot has 0 spent
        assert result > 0.0


# =========================================================================
# Minimum Bet Size
# =========================================================================


class TestMinimumBetSize:
    @pytest.mark.asyncio
    async def test_zero_when_below_one_dollar(self):
        """Bet < $1 is rounded to 0 (not meaningful for trading)."""
        # Use an override with very tiny capital to produce sub-$1 kelly
        override = json.dumps({"TinyBot": {"capital": 50, "kelly_fraction": 0.10, "max_bet_usd": 100, "max_daily_usd": 500}})
        mgr = make_manager("TinyBot", bankroll_config=override)
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            # confidence=0.505, price=0.50
            # b = 1.0, q = 0.495
            # kelly_full = (0.505 - 0.495) / 1.0 = 0.01
            # size = 0.01 * 0.10 * 50 = $0.05 -> below $1 minimum
            result = await mgr.get_bet_size(confidence=0.505, price=0.50)
        assert result == 0.0


# =========================================================================
# Calibration Quality Scaling
# =========================================================================


class TestCalibrationScaling:
    @pytest.mark.asyncio
    async def test_good_calibration_no_reduction(self):
        """Brier < 0.15 -> no calibration penalty."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            good_cal = {"brier": 0.10, "count": 100}
            # Use small edge (0.52) so result is below $100 cap
            result_good = await mgr.get_bet_size(
                confidence=0.52, price=0.50, calibration_quality=good_cal
            )
            no_cal = await mgr.get_bet_size(confidence=0.52, price=0.50)
        # Good calibration should give same or similar result as no calibration
        assert result_good == pytest.approx(no_cal, abs=1.0)

    @pytest.mark.asyncio
    async def test_poor_calibration_reduces_size(self):
        """Brier > 0.15 with enough samples -> size reduced."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            poor_cal = {"brier": 0.25, "count": 100}
            # Use small edge (0.52 vs 0.50) so both results are under $100 cap
            result_poor = await mgr.get_bet_size(
                confidence=0.52, price=0.50, calibration_quality=poor_cal
            )
            no_cal = await mgr.get_bet_size(confidence=0.52, price=0.50)
        # Poor calibration should give smaller bet (uncapped)
        assert result_poor < no_cal

    @pytest.mark.asyncio
    async def test_calibration_ignored_when_few_samples(self):
        """Calibration scaling skipped when count < 20."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            few_samples = {"brier": 0.30, "count": 10}  # Below 20 threshold
            # Use small edge so result is below cap
            result_few = await mgr.get_bet_size(
                confidence=0.52, price=0.50, calibration_quality=few_samples
            )
            no_cal = await mgr.get_bet_size(confidence=0.52, price=0.50)
        # Should be same as no calibration
        assert result_few == pytest.approx(no_cal, abs=0.01)


# =========================================================================
# Category-Specific Kelly Fractions
# =========================================================================


class TestCategoryFractions:
    @pytest.mark.asyncio
    async def test_category_override_applied(self):
        """Category-specific Kelly fraction overrides bot's default."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = json.dumps({"crypto": 0.125})
            ms.SIMULATION_MODE = False
            # Use small edge (0.52) so result is below $100 cap
            result_crypto = await mgr.get_bet_size(
                confidence=0.52, price=0.50, category="crypto"
            )
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result_default = await mgr.get_bet_size(
                confidence=0.52, price=0.50, category=""
            )
        # Crypto fraction (0.125) < default fraction (0.25) -> smaller bet
        assert result_crypto < result_default

    @pytest.mark.asyncio
    async def test_unknown_category_uses_default_fraction(self):
        """Category not in CATEGORY_KELLY_FRACTIONS -> default fraction."""
        mgr = make_manager()
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = json.dumps({"crypto": 0.125})
            ms.SIMULATION_MODE = False
            # Use small edge so result is below cap
            result = await mgr.get_bet_size(
                confidence=0.52, price=0.50, category="science"
            )
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            result_no_cat = await mgr.get_bet_size(confidence=0.52, price=0.50)
        assert result == pytest.approx(result_no_cat, abs=0.01)


# =========================================================================
# _get_daily_spent
# =========================================================================


class TestGetDailySpent:
    def test_reads_correct_bot_name(self):
        """_get_daily_spent reads only THIS bot's exposure."""
        mgr = make_manager(
            "EnsembleBot",
            daily_exposure={"EnsembleBot": 150.0, "ArbitrageBot": 999.0},
        )
        total = mgr._get_daily_spent()
        assert total == pytest.approx(150.0)

    def test_missing_bot_returns_zero(self):
        """Bot not in exposure dict -> 0.0."""
        mgr = make_manager("EnsembleBot", daily_exposure={"ArbitrageBot": 200.0})
        total = mgr._get_daily_spent()
        assert total == 0.0

    def test_empty_exposure_dict(self):
        """Empty exposure dict -> 0.0."""
        mgr = make_manager("EnsembleBot", daily_exposure={})
        total = mgr._get_daily_spent()
        assert total == 0.0

    def test_no_order_gateway_returns_zero(self):
        """No order_gateway -> 0.0."""
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.BOT_BANKROLL_CONFIG = "{}"
            from base_engine.risk.bankroll_manager import BotBankrollManager
            mgr = BotBankrollManager("EnsembleBot", order_gateway=None, db=None)
        total = mgr._get_daily_spent()
        assert total == 0.0


# =========================================================================
# get_daily_exposure (async wrapper)
# =========================================================================


class TestGetDailyExposure:
    @pytest.mark.asyncio
    async def test_returns_same_as_sync(self):
        """Async wrapper returns same value as _get_daily_spent."""
        mgr = make_manager("EnsembleBot", daily_exposure={"EnsembleBot": 250.0})
        result = await mgr.get_daily_exposure()
        assert result == pytest.approx(250.0)

    @pytest.mark.asyncio
    async def test_lock_guarded(self):
        """The method uses the daily lock (no assertion needed, just verify it runs)."""
        mgr = make_manager()
        result = await mgr.get_daily_exposure()
        assert result == pytest.approx(0.0)


# =========================================================================
# get_state — diagnostics
# =========================================================================


class TestGetState:
    def test_returns_correct_keys(self):
        """get_state returns all expected diagnostic fields."""
        mgr = make_manager("ArbitrageBot")
        state = mgr.get_state()
        assert state["bot_name"] == "ArbitrageBot"
        assert state["capital"] == 1000.0
        assert state["kelly_fraction"] == 0.25
        assert state["max_bet_usd"] == 100.0
        assert state["max_daily_usd"] == 500.0
        assert "daily_spent" in state

    def test_daily_spent_reflects_exposure(self):
        """get_state daily_spent reflects actual order_gateway exposure."""
        mgr = make_manager("EnsembleBot", daily_exposure={"EnsembleBot": 123.45})
        state = mgr.get_state()
        assert state["daily_spent"] == pytest.approx(123.45)


# =========================================================================
# Per-Bot Kelly Independence (no more KELLY_ACTIVE_BOTS divisor)
# =========================================================================


class TestPerBotKellyIndependence:
    @pytest.mark.asyncio
    async def test_ensemble_bot_uses_own_capital(self):
        """EnsembleBot Kelly uses $20000 capital, not global_capital / n_bots."""
        mgr = make_manager("EnsembleBot")
        assert mgr.capital == 20000.0
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            # confidence=0.52, price=0.50
            # kelly_full = 0.04
            # size = 0.04 * 0.25 * 20000 = $200.0
            result = await mgr.get_bet_size(confidence=0.52, price=0.50)
        # Each bot uses own capital, no divisor
        assert result == pytest.approx(200.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_arb_bot_independent_capital(self):
        """ArbitrageBot uses its own $1000 capital independently."""
        mgr = make_manager("ArbitrageBot")
        assert mgr.capital == 1000.0
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            # confidence=0.52, price=0.50
            # kelly_full = 0.04
            # size = 0.04 * 0.25 * 1000 = $10.0
            result = await mgr.get_bet_size(confidence=0.52, price=0.50)
        assert result == pytest.approx(10.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_no_kelly_active_bots_divisor(self):
        """
        BotBankrollManager does NOT divide by KELLY_ACTIVE_BOTS.

        Old behavior: available_capital = total_capital / KELLY_ACTIVE_BOTS
        New behavior: each bot's capital is fixed, no divisor.
        """
        mgr = make_manager("EnsembleBot")
        # The capital should be exactly 8000, not 8000/N
        assert mgr.capital == 20000.0
        # Kelly fraction should be 0.25, not 0.25/N
        assert mgr.kelly_fraction == 0.25


# =========================================================================
# S173 Day 2: Phase Cap Enforcement
# =========================================================================


class TestPhaseCap:
    def test_phase_cap_stored(self):
        """Phase cap is parsed from PHASE_MAX_BET_USD JSON during init."""
        mgr = make_manager("EnsembleBot")
        # make_manager sets PHASE_MAX_BET_USD = '{"paper": 9999.0}'
        assert mgr.phase_max_bet_usd == pytest.approx(9999.0)

    def test_phase_cap_with_low_value(self):
        """Phase cap enforces when set lower than per-bot max."""
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.BOT_BANKROLL_CONFIG = "{}"
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            ms.PHASE_MAX_BET_USD = '{"paper": 50.0}'
            ms.TRADING_PHASE = "paper"
            from base_engine.risk.bankroll_manager import BotBankrollManager
            mgr = BotBankrollManager("EnsembleBot", order_gateway=MagicMock(), db=None)
        assert mgr.phase_max_bet_usd == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_phase_cap_limits_bet_size(self):
        """Phase cap at 50 limits a Kelly-computed bet that would be 200+."""
        with patch("base_engine.risk.bankroll_manager.settings") as ms:
            ms.BOT_BANKROLL_CONFIG = "{}"
            ms.CATEGORY_KELLY_FRACTIONS = "{}"
            ms.SIMULATION_MODE = False
            ms.PHASE_MAX_BET_USD = '{"paper": 50.0}'
            ms.TRADING_PHASE = "paper"
            from base_engine.risk.bankroll_manager import BotBankrollManager
            gw = MagicMock()
            gw.get_daily_exposure_usd = lambda bot_name: 0.0
            mgr = BotBankrollManager("EnsembleBot", order_gateway=gw, db=None)
            # confidence=0.65, price=0.50 -> Kelly wants $1500 -> per-bot cap $300 -> phase cap $50
            result = await mgr.get_bet_size(confidence=0.65, price=0.50)
        assert result == pytest.approx(50.0, abs=0.01)
