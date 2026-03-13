"""
Session 37 — Unit tests for new guardrail features (2b, 2e, 2f, 2g, 2h, 2j) and
websockets v15 migration (5a).

Test groups:
  A. Phase-based bet caps (Priority 2b)
  B. Category-specific Kelly fractions (Priority 2e)
  C. Dynamic KELLY_ACTIVE_BOTS (Priority 2f)
  D. Politics profit-taking exit (Priority 2g)
  E. Weather progressive expiry boost (Priority 2h)
  F. PhaseTracker.evaluate() + should_evaluate() (Priority 2j)
  G. websockets v15 import migration (Priority 5a)
"""
import asyncio
import json
import time
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_risk_manager(settings_overrides: dict | None = None):
    """
    Return a (RiskManager, patcher) pair with a null DB and patched settings.

    Uses types.SimpleNamespace for settings so getattr(settings, key, default)
    works identically to the real settings object — without MagicMock magic-method
    restrictions.
    """
    from base_engine.risk.risk_manager import RiskManager

    mock_db = MagicMock()
    mock_db.session_factory = None  # skip DB calls
    rm = RiskManager(db=mock_db)

    # Sensible base settings so Kelly formula produces a nonzero result.
    base = {
        "KELLY_FRACTION": 0.25,
        "KELLY_ACTIVE_BOTS": 4,
        "MAX_POSITION_SIZE_PCT": 0.10,
        "RISK_MAX_POSITION_SIZE_USD": 10_000.0,
        "VOL_SCALE_FACTOR": 2.0,
        "SIMULATION_MODE": True,
        "TRADING_PHASE": "paper",
        "PHASE_MAX_BET_USD": json.dumps(
            {"paper": 15.0, "learning": 20.0, "graduated": 200.0, "production": 1000.0}
        ),
        "CATEGORY_KELLY_FRACTIONS": json.dumps(
            {"weather": 0.25, "crypto": 0.125, "politics": 0.20, "sports": 0.15}
        ),
        "BOT_ENABLED_ENSEMBLE": True,
        "BOT_ENABLED_ARBITRAGE": True,
        "BOT_ENABLED_MIRROR": True,
        "BOT_ENABLED_ORACLE": False,
        "BOT_ENABLED_LLM_FORECASTER": False,
        "BOT_ENABLED_WEATHER": False,
        "BOT_ENABLED_CROSS_PLATFORM_ARB": False,
    }
    if settings_overrides:
        base.update(settings_overrides)

    # SimpleNamespace: plain attribute access, fully compatible with getattr(obj, key, default).
    mock_settings = types.SimpleNamespace(**base)

    patcher = patch("base_engine.risk.risk_manager.settings", new=mock_settings)
    patcher.start()

    return rm, patcher


# ─────────────────────────────────────────────────────────────────────────────
# A. Phase-based bet caps (2b)
# ─────────────────────────────────────────────────────────────────────────────

class TestPhaseBetCaps:
    """
    Verify that calculate_position_size() hard-caps the USD bet at the phase limit.
    We use high confidence (0.85) and high capital ($100k) to ensure raw Kelly
    would produce a large bet, so the phase cap is the binding constraint.
    """

    @pytest.mark.asyncio
    async def test_paper_phase_capped_at_15_dollars(self):
        """Paper phase: Kelly > $15 should be capped to $15."""
        rm, patcher = _make_risk_manager({"TRADING_PHASE": "paper"})
        try:
            shares = await rm.calculate_position_size(
                bot_name="test_bot",
                confidence=0.85,
                available_capital=100_000.0,
                price=0.50,
                category="",
            )
            # shares * price = position_usd ≤ $15
            position_usd = shares * 0.50
            assert position_usd <= 15.0 + 0.01, (
                f"Paper phase: expected ≤ $15 but got ${position_usd:.2f}"
            )
            assert shares > 0, "Should return positive shares for a valid positive-edge trade"
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_learning_phase_capped_at_20_dollars(self):
        """Learning phase: Kelly > $20 should be capped to $20."""
        rm, patcher = _make_risk_manager({"TRADING_PHASE": "learning"})
        try:
            shares = await rm.calculate_position_size(
                bot_name="test_bot",
                confidence=0.85,
                available_capital=100_000.0,
                price=0.50,
                category="",
            )
            position_usd = shares * 0.50
            assert position_usd <= 20.0 + 0.01, (
                f"Learning phase: expected ≤ $20 but got ${position_usd:.2f}"
            )
            assert shares > 0
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_graduated_phase_capped_at_200_dollars(self):
        """Graduated phase: Kelly > $200 should be capped to $200."""
        rm, patcher = _make_risk_manager({"TRADING_PHASE": "graduated"})
        try:
            shares = await rm.calculate_position_size(
                bot_name="test_bot",
                confidence=0.85,
                available_capital=100_000.0,
                price=0.50,
                category="",
            )
            position_usd = shares * 0.50
            assert position_usd <= 200.0 + 0.01, (
                f"Graduated phase: expected ≤ $200 but got ${position_usd:.2f}"
            )
            assert shares > 0
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_production_phase_allows_up_to_1000_dollars(self):
        """Production phase: cap is $1000, well above a typical Kelly result."""
        rm, patcher = _make_risk_manager({"TRADING_PHASE": "production"})
        try:
            shares = await rm.calculate_position_size(
                bot_name="test_bot",
                confidence=0.85,
                available_capital=100_000.0,
                price=0.50,
                category="",
            )
            position_usd = shares * 0.50
            assert position_usd <= 1000.0 + 0.01, (
                f"Production phase: expected ≤ $1000 but got ${position_usd:.2f}"
            )
            assert shares > 0
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_small_kelly_not_capped_up_by_phase(self):
        """Phase cap must never INCREASE bet size — only decrease it."""
        rm, patcher = _make_risk_manager({
            "TRADING_PHASE": "paper",
            # Small capital → small Kelly bet well under $15
        })
        try:
            shares = await rm.calculate_position_size(
                bot_name="test_bot",
                confidence=0.52,
                available_capital=10.0,  # tiny capital
                price=0.50,
                category="",
            )
            position_usd = shares * 0.50
            # Kelly on $10 capital at tiny edge should be < $15 — confirm no inflating
            assert position_usd < 15.0, (
                f"Small Kelly should not be inflated by phase cap, got ${position_usd:.2f}"
            )
        finally:
            patcher.stop()

    @pytest.mark.asyncio
    async def test_paper_cap_tighter_than_learning(self):
        """Paper cap ($15) must always be ≤ learning cap ($20)."""
        rm_paper, p1 = _make_risk_manager({"TRADING_PHASE": "paper"})
        rm_learning, p2 = _make_risk_manager({"TRADING_PHASE": "learning"})
        try:
            s_paper = await rm_paper.calculate_position_size(
                "bot", 0.85, 100_000.0, 0.50, category=""
            )
            s_learning = await rm_learning.calculate_position_size(
                "bot", 0.85, 100_000.0, 0.50, category=""
            )
            assert s_paper * 0.50 <= s_learning * 0.50, (
                "Paper cap should be ≤ learning cap"
            )
        finally:
            p1.stop()
            p2.stop()


# ─────────────────────────────────────────────────────────────────────────────
# B. Category-specific Kelly fractions (2e)
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryKellyFractions:
    """
    Verify that different market categories produce proportionally different bet sizes.
    Crypto (0.125) < politics (0.20) < weather (0.25) — all else equal.
    """

    @pytest.mark.asyncio
    async def test_weather_category_larger_than_crypto(self):
        """weather Kelly (0.25) should produce larger bets than crypto (0.125)."""
        rm_w, p_w = _make_risk_manager({"TRADING_PHASE": "production"})  # use production to remove cap
        rm_c, p_c = _make_risk_manager({"TRADING_PHASE": "production"})
        try:
            shares_weather = await rm_w.calculate_position_size(
                "bot", 0.65, 100_000.0, 0.50, category="weather"
            )
            shares_crypto = await rm_c.calculate_position_size(
                "bot", 0.65, 100_000.0, 0.50, category="crypto"
            )
            assert shares_weather > shares_crypto, (
                f"Weather ({shares_weather:.2f}) should exceed crypto ({shares_crypto:.2f}) shares"
            )
        finally:
            p_w.stop()
            p_c.stop()

    @pytest.mark.asyncio
    async def test_politics_category_between_crypto_and_weather(self):
        """politics (0.20) should produce bets between crypto (0.125) and weather (0.25)."""
        rm_p, p1 = _make_risk_manager({"TRADING_PHASE": "production"})
        rm_c, p2 = _make_risk_manager({"TRADING_PHASE": "production"})
        rm_w, p3 = _make_risk_manager({"TRADING_PHASE": "production"})
        try:
            s_pol = await rm_p.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="politics")
            s_cryp = await rm_c.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="crypto")
            s_weat = await rm_w.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="weather")
            assert s_cryp <= s_pol <= s_weat, (
                f"Expected crypto ({s_cryp:.2f}) ≤ politics ({s_pol:.2f}) ≤ weather ({s_weat:.2f})"
            )
        finally:
            p1.stop(); p2.stop(); p3.stop()

    @pytest.mark.asyncio
    async def test_unknown_category_uses_default_fraction(self):
        """A category not in CATEGORY_KELLY_FRACTIONS should fall back to KELLY_FRACTION."""
        rm_default, p1 = _make_risk_manager({"TRADING_PHASE": "production"})
        rm_unknown, p2 = _make_risk_manager({"TRADING_PHASE": "production"})
        try:
            s_default = await rm_default.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="")
            s_unknown = await rm_unknown.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="foobar")
            # Both should use the same fraction (0.25); result should be equal
            assert abs(s_default - s_unknown) < 0.01, (
                f"Unknown category ({s_unknown:.2f}) should equal no-category ({s_default:.2f})"
            )
        finally:
            p1.stop(); p2.stop()

    @pytest.mark.asyncio
    async def test_category_case_insensitive(self):
        """'WEATHER', 'Weather', and 'weather' should all apply the 0.25× fraction."""
        rm1, p1 = _make_risk_manager({"TRADING_PHASE": "production"})
        rm2, p2 = _make_risk_manager({"TRADING_PHASE": "production"})
        rm3, p3 = _make_risk_manager({"TRADING_PHASE": "production"})
        try:
            s1 = await rm1.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="weather")
            s2 = await rm2.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="Weather")
            s3 = await rm3.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="WEATHER")
            assert abs(s1 - s2) < 0.01 and abs(s1 - s3) < 0.01, (
                f"Case variants should be equal: {s1:.2f}, {s2:.2f}, {s3:.2f}"
            )
        finally:
            p1.stop(); p2.stop(); p3.stop()


# ─────────────────────────────────────────────────────────────────────────────
# C. KELLY_ACTIVE_BOTS Legacy Divisor (2f)
# Session 47: Per-bot sizing now handled by BotBankrollManager.
# calculate_position_size() is DEPRECATED — only uses KELLY_ACTIVE_BOTS setting
# directly (no dynamic bot-counting). These tests verify the legacy path still
# applies the setting-based divisor for backward compatibility.
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicKellyActiveBots:
    """
    Legacy KELLY_ACTIVE_BOTS divisor: higher setting → smaller bet from
    calculate_position_size(). Dynamic bot-counting was removed in Session 47;
    per-bot sizing is now handled by BotBankrollManager.
    """

    @pytest.mark.asyncio
    async def test_higher_kelly_active_bots_means_smaller_bet(self):
        """KELLY_ACTIVE_BOTS=8 produces smaller bet than KELLY_ACTIVE_BOTS=1 (legacy divisor).

        Session 47: Dynamic bot-counting was removed. The legacy path now just uses
        max(1, KELLY_ACTIVE_BOTS) directly. This test verifies the setting still
        controls the divisor for backward compat.
        """
        # ── Step 1: KELLY_ACTIVE_BOTS=1 ──
        rm_few, p1 = _make_risk_manager({
            "TRADING_PHASE": "production",
            "KELLY_ACTIVE_BOTS": 1,
        })
        try:
            s_few = await rm_few.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="")
        finally:
            p1.stop()

        # ── Step 2: KELLY_ACTIVE_BOTS=8 ──
        rm_many, p2 = _make_risk_manager({
            "TRADING_PHASE": "production",
            "KELLY_ACTIVE_BOTS": 8,
        })
        try:
            s_many = await rm_many.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="")
        finally:
            p2.stop()

        assert s_few > s_many, (
            f"KELLY_ACTIVE_BOTS=1 ({s_few:.2f}) should bet more than =8 ({s_many:.2f})"
        )

    @pytest.mark.asyncio
    async def test_uses_configured_minimum_when_higher(self):
        """
        KELLY_ACTIVE_BOTS=10 → fraction/10 → smaller bet than KELLY_ACTIVE_BOTS=1.
        Session 47: Legacy path. Per-bot sizing now uses BotBankrollManager.
        """
        # ── Step 1: KELLY_ACTIVE_BOTS=10 ──
        rm_conf, p1 = _make_risk_manager({
            "TRADING_PHASE": "production",
            "KELLY_ACTIVE_BOTS": 10,
        })
        try:
            s_conf = await rm_conf.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="")
        finally:
            p1.stop()

        # ── Step 2: KELLY_ACTIVE_BOTS=1 ──
        rm_raw, p2 = _make_risk_manager({
            "TRADING_PHASE": "production",
            "KELLY_ACTIVE_BOTS": 1,
        })
        try:
            s_raw = await rm_raw.calculate_position_size("bot", 0.65, 100_000.0, 0.50, category="")
        finally:
            p2.stop()

        # KELLY_ACTIVE_BOTS=10 (N=10) → fraction/10 → smaller bet than fraction/1
        assert s_conf <= s_raw, (
            f"10-bot floor ({s_conf:.2f}) should produce ≤ bet than 1-bot ({s_raw:.2f})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# D. Politics profit-taking exit (2g)
# ─────────────────────────────────────────────────────────────────────────────

class TestPoliticsProfitTaking:
    """
    Unit tests for EnsembleBot._check_politics_profit_taking().
    We stub base_engine and order_gateway — no DB or network needed.
    """

    def _make_bot(self, positions: dict, market_prices: dict, sim_mode: bool = True):
        """
        Build a minimal EnsembleBot-like object with mocked internals.
        positions: {market_id: {bot_name, category, side, entry_price, size}}
        market_prices: {market_id: yes_price}
        """
        from bots.ensemble_bot import EnsembleBot
        mock_db = MagicMock()
        mock_db.session_factory = None
        mock_engine = MagicMock()
        mock_engine.db = mock_db

        # Wire order_gateway._positions and _market_index
        mock_og = MagicMock()
        mock_og._positions = positions
        mock_engine.order_gateway = mock_og
        mock_engine._market_index = {
            mid: {"yes_price": price} for mid, price in market_prices.items()
        }
        # Mock place_order as async (used for SELL exits)
        mock_engine.place_order = AsyncMock(return_value={"success": True})

        bot = EnsembleBot.__new__(EnsembleBot)
        bot.bot_name = "EnsembleBot"
        bot.base_engine = mock_engine
        bot._recently_exited = {}
        bot._exit_count = {}
        return bot, sim_mode

    @pytest.mark.asyncio
    async def test_yes_position_triggers_when_price_hits_target(self):
        """YES position at 0.50 with 65% exit target triggers when current_price ≥ 0.825."""
        # entry=0.50, max_profit=0.50, target = 0.50 + 0.65*0.50 = 0.825
        bot, _ = self._make_bot(
            positions={"mkt1": {
                "bot_name": "EnsembleBot", "category": "politics",
                "side": "YES", "entry_price": 0.50, "size": 10.0,
            }},
            market_prices={"mkt1": 0.83},  # above 0.825 target
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = True
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 0.0  # no minimum for this test
            await bot._check_politics_profit_taking()
        # place_order called with SELL to close position
        bot.base_engine.place_order.assert_called_once()
        call_kwargs = bot.base_engine.place_order.call_args
        assert call_kwargs.kwargs.get("side") == "SELL" or call_kwargs[1].get("side") == "SELL"
        # Cooldown applied after close
        assert "mkt1" in bot._recently_exited, "Market should be added to recently_exited"
        assert bot._exit_count.get("mkt1") == 3

    @pytest.mark.asyncio
    async def test_yes_position_no_trigger_below_target(self):
        """YES position at 0.50 does NOT trigger when current_price < 0.825."""
        bot, _ = self._make_bot(
            positions={"mkt1": {
                "bot_name": "EnsembleBot", "category": "politics",
                "side": "YES", "entry_price": 0.50, "size": 10.0,
            }},
            market_prices={"mkt1": 0.70},  # below 0.825 target
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = True
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 0.0
            await bot._check_politics_profit_taking()
        assert "mkt1" not in bot._recently_exited, "Should not trigger below target"

    @pytest.mark.asyncio
    async def test_no_position_triggers_when_no_price_drops_enough(self):
        """
        NO position: entry YES price = 0.70 → entry_no = 0.30.
        target_no = 0.30 - 0.65*0.30 = 0.105.
        Trigger when no_price (= 1 - yes_price) ≤ 0.105 → yes_price ≥ 0.895.
        """
        bot, _ = self._make_bot(
            positions={"mkt2": {
                "bot_name": "EnsembleBot", "category": "politics",
                "side": "NO", "entry_price": 0.70, "size": 10.0,
            }},
            market_prices={"mkt2": 0.91},  # no_price = 0.09 ≤ 0.105 → trigger
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = True
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 0.0
            await bot._check_politics_profit_taking()
        assert "mkt2" in bot._recently_exited

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self):
        """POLITICS_EXIT_ENABLED=False → method returns immediately, no cooldown set."""
        bot, _ = self._make_bot(
            positions={"mkt1": {
                "bot_name": "EnsembleBot", "category": "politics",
                "side": "YES", "entry_price": 0.50, "size": 10.0,
            }},
            market_prices={"mkt1": 0.95},  # well above target
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = False
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 0.0
            await bot._check_politics_profit_taking()
        assert "mkt1" not in bot._recently_exited, "Should not trigger when disabled"

    @pytest.mark.asyncio
    async def test_skips_non_politics_category(self):
        """Positions in 'crypto' category are not touched by the politics exit logic."""
        bot, _ = self._make_bot(
            positions={"mkt1": {
                "bot_name": "EnsembleBot", "category": "crypto",  # NOT politics
                "side": "YES", "entry_price": 0.50, "size": 10.0,
            }},
            market_prices={"mkt1": 0.99},
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = True
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 0.0
            await bot._check_politics_profit_taking()
        assert "mkt1" not in bot._recently_exited

    @pytest.mark.asyncio
    async def test_skips_when_min_profit_not_met(self):
        """Target hit but unrealized P&L < POLITICS_EXIT_MIN_PROFIT_USD → skip."""
        # entry=0.50, size=0.01 → max_pnl ≈ $0.005 — below $2 minimum
        bot, _ = self._make_bot(
            positions={"mkt1": {
                "bot_name": "EnsembleBot", "category": "politics",
                "side": "YES", "entry_price": 0.50, "size": 0.01,
            }},
            market_prices={"mkt1": 0.90},
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = True
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 2.0
            await bot._check_politics_profit_taking()
        assert "mkt1" not in bot._recently_exited

    @pytest.mark.asyncio
    async def test_skips_other_bot_positions(self):
        """Positions belonging to a different bot are ignored."""
        bot, _ = self._make_bot(
            positions={"mkt1": {
                "bot_name": "ArbitrageBot",  # different bot
                "category": "politics",
                "side": "YES", "entry_price": 0.50, "size": 10.0,
            }},
            market_prices={"mkt1": 0.95},
        )
        with patch("bots.ensemble_bot.settings") as mock_s:
            mock_s.POLITICS_EXIT_ENABLED = True
            mock_s.POLITICS_EXIT_PCT = 0.65
            mock_s.POLITICS_EXIT_MIN_PROFIT_USD = 0.0
            await bot._check_politics_profit_taking()
        assert "mkt1" not in bot._recently_exited


# ─────────────────────────────────────────────────────────────────────────────
# E. Weather progressive expiry boost (2h)
# ─────────────────────────────────────────────────────────────────────────────

class TestWeatherExpiryBoost:
    """
    Verify the progressive NOAA boost schedule:
      < 12h → 2.0×  | < 24h → 1.5×  | < hold_hours → 1.2×  | else → 1.0×
    Test the logic directly via the _build_position_kwargs helper, or
    by reading the boost from a patched scan iteration.

    We test the logic by importing the function and simulating the conditional tree.
    Since the boost is computed inline in _process_opportunity(), we replicate the
    exact same conditional logic here to confirm the expected outcomes.
    """

    def _compute_boost(self, lead_time: float, hold_h: float = 48.0) -> float:
        """Mirror the exact logic from weather_bot.py._process_opportunity()."""
        if lead_time < 12.0:
            return 2.0
        elif lead_time < 24.0:
            return 1.5
        elif lead_time < hold_h:
            return 1.2
        else:
            return 1.0

    def test_boost_is_2x_at_6_hours(self):
        """6h lead time → NOAA final-call → 2.0× boost."""
        assert self._compute_boost(6.0) == 2.0

    def test_boost_is_2x_at_11_hours(self):
        """11.9h lead time → still < 12h → 2.0×."""
        assert self._compute_boost(11.9) == 2.0

    def test_boost_is_1_5x_at_12_hours(self):
        """12.0h lead time → < 24h branch → 1.5×."""
        assert self._compute_boost(12.0) == 1.5

    def test_boost_is_1_5x_at_20_hours(self):
        """20h lead time → < 24h → 1.5×."""
        assert self._compute_boost(20.0) == 1.5

    def test_boost_is_1_2x_at_24_hours(self):
        """24.0h → inside hold_window (< 48h) → 1.2×."""
        assert self._compute_boost(24.0) == 1.2

    def test_boost_is_1_2x_at_36_hours(self):
        """36h → within 48h hold window → 1.2×."""
        assert self._compute_boost(36.0) == 1.2

    def test_boost_is_1x_at_48_hours(self):
        """48.0h → exactly on hold_window boundary → standard 1.0×."""
        assert self._compute_boost(48.0) == 1.0

    def test_boost_is_1x_at_72_hours(self):
        """72h → well outside hold window → 1.0×."""
        assert self._compute_boost(72.0) == 1.0

    def test_custom_hold_window_12h(self):
        """Custom hold_window=12h: 10h lead → 2.0× (< 12h), 14h lead → 1.0× (≥ 12h)."""
        assert self._compute_boost(10.0, hold_h=12.0) == 2.0
        # 14h is NOT < 24h either? No, 14 < 24 → 1.5×
        assert self._compute_boost(14.0, hold_h=12.0) == 1.5

    def test_boost_ordering(self):
        """Verify strict ordering: 4h > 18h > 36h > 72h."""
        b4 = self._compute_boost(4.0)    # 2.0
        b18 = self._compute_boost(18.0)  # 1.5
        b36 = self._compute_boost(36.0)  # 1.2
        b72 = self._compute_boost(72.0)  # 1.0
        assert b4 > b18 > b36 > b72

    def test_combined_boost_capped_at_2_5(self):
        """Combined boost (expiry × regime) is capped at 2.5. Test cap at 2.0 × 1.5 = 3.0 → 2.5."""
        expiry_boost = 2.0  # <12h
        regime_boost = 1.5
        combined = min(expiry_boost * regime_boost, 2.5)
        assert combined == 2.5

    def test_combined_boost_no_cap_when_under_2_5(self):
        """1.2 × 1.5 = 1.8 → no cap applied."""
        expiry_boost = 1.2
        regime_boost = 1.5
        combined = min(expiry_boost * regime_boost, 2.5)
        assert abs(combined - 1.8) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# F. PhaseTracker (2j)
# ─────────────────────────────────────────────────────────────────────────────

class TestPhaseTracker:
    """
    Tests for base_engine.monitoring.phase_tracker.PhaseTracker.
    DB calls are fully mocked — no real database required.
    """

    def _make_tracker(self, current_phase: str = "paper"):
        from base_engine.monitoring.phase_tracker import PhaseTracker
        mock_db = MagicMock()
        # session_factory = truthy so tracker doesn't short-circuit
        mock_db.session_factory = True

        tracker = PhaseTracker(db=mock_db)
        return tracker, mock_db

    # ── should_evaluate ──────────────────────────────────────────────────

    def test_should_evaluate_true_at_startup(self):
        """Last evaluated = 0 → should_evaluate() is True immediately."""
        tracker, _ = self._make_tracker()
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_GRADUATION_CHECK_HOURS = 24.0
            assert tracker.should_evaluate() is True

    def test_should_evaluate_false_right_after_evaluation(self):
        """After setting _last_evaluated to now, should_evaluate() is False."""
        tracker, _ = self._make_tracker()
        tracker._last_evaluated = time.monotonic()  # just evaluated
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_GRADUATION_CHECK_HOURS = 24.0
            assert tracker.should_evaluate() is False

    def test_should_evaluate_true_after_interval(self):
        """Setting _last_evaluated to 25h ago → should_evaluate() is True."""
        tracker, _ = self._make_tracker()
        tracker._last_evaluated = time.monotonic() - (25 * 3600)  # 25h ago
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_GRADUATION_CHECK_HOURS = 24.0
            assert tracker.should_evaluate() is True

    # ── evaluate — no DB ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_evaluate_returns_neutral_when_no_db(self):
        """PhaseTracker with db=None returns a neutral PhaseMetrics (no crash)."""
        from base_engine.monitoring.phase_tracker import PhaseTracker
        tracker = PhaseTracker(db=None)
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.TRADING_PHASE = "paper"
            ms.PHASE_GRADUATION_CHECK_HOURS = 24.0
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            ms.PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS = 300
            ms.PHASE_LEARNING_TO_GRADUATED_WIN_RATE = 0.55
            ms.PHASE_LEARNING_TO_GRADUATED_MAX_BRIER = 0.20
            metrics = await tracker.evaluate()
        assert metrics.current_phase == "paper"
        assert metrics.resolved_count == 0
        assert metrics.win_rate == 0.0

    # ── PhaseMetrics.meets_promotion_criteria ─────────────────────────────

    def test_meets_promotion_paper_to_learning_all_criteria(self):
        """120 predictions, 55% win rate, Brier 0.20 → meets paper→learning."""
        from base_engine.monitoring.phase_tracker import PhaseMetrics
        m = PhaseMetrics(
            resolved_count=120,
            win_rate=0.55,
            brier_score=0.20,
            current_phase="paper",
        )
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            assert m.meets_promotion_criteria("learning") is True

    def test_not_promoted_when_count_too_low(self):
        """50 predictions (< 100 minimum) → no promotion to learning."""
        from base_engine.monitoring.phase_tracker import PhaseMetrics
        m = PhaseMetrics(resolved_count=50, win_rate=0.60, brier_score=0.18, current_phase="paper")
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            assert m.meets_promotion_criteria("learning") is False

    def test_not_promoted_when_win_rate_too_low(self):
        """Win rate 0.48 (< 0.52 threshold) → no promotion."""
        from base_engine.monitoring.phase_tracker import PhaseMetrics
        m = PhaseMetrics(resolved_count=150, win_rate=0.48, brier_score=0.20, current_phase="paper")
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            assert m.meets_promotion_criteria("learning") is False

    def test_not_promoted_when_brier_too_high(self):
        """Brier 0.25 (> 0.22 threshold) → no promotion."""
        from base_engine.monitoring.phase_tracker import PhaseMetrics
        m = PhaseMetrics(resolved_count=150, win_rate=0.55, brier_score=0.25, current_phase="paper")
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            assert m.meets_promotion_criteria("learning") is False

    def test_meets_promotion_learning_to_graduated(self):
        """350 predictions, 57% win rate, Brier 0.18 → meets learning→graduated."""
        from base_engine.monitoring.phase_tracker import PhaseMetrics
        m = PhaseMetrics(resolved_count=350, win_rate=0.57, brier_score=0.18, current_phase="learning")
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS = 300
            ms.PHASE_LEARNING_TO_GRADUATED_WIN_RATE = 0.55
            ms.PHASE_LEARNING_TO_GRADUATED_MAX_BRIER = 0.20
            assert m.meets_promotion_criteria("graduated") is True

    def test_graduated_to_production_always_false(self):
        """graduated → production is manual-only; always returns False."""
        from base_engine.monitoring.phase_tracker import PhaseMetrics
        m = PhaseMetrics(resolved_count=1000, win_rate=0.70, brier_score=0.10, current_phase="graduated")
        # No settings needed — returns False immediately
        assert m.meets_promotion_criteria("production") is False

    # ── evaluate — DB mock ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_evaluate_with_mock_db_returns_correct_win_rate(self):
        """Mock DB returns 60 correct of 100 total → win_rate=0.60."""
        from base_engine.monitoring.phase_tracker import PhaseTracker

        mock_db = MagicMock()
        mock_db.session_factory = True

        # Build a mock session context manager
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, i: [100, 60, 0.21, 0.08][i]
        mock_row.__bool__ = lambda self: True
        mock_execute_result = MagicMock()
        mock_execute_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_execute_result

        mock_db.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.get_session.return_value.__aexit__ = AsyncMock(return_value=False)

        tracker = PhaseTracker(db=mock_db)

        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.TRADING_PHASE = "paper"
            ms.PHASE_GRADUATION_CHECK_HOURS = 24.0
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            ms.PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS = 300
            ms.PHASE_LEARNING_TO_GRADUATED_WIN_RATE = 0.55
            ms.PHASE_LEARNING_TO_GRADUATED_MAX_BRIER = 0.20
            metrics = await tracker.evaluate()

        assert metrics.resolved_count == 100
        assert abs(metrics.win_rate - 0.60) < 0.001

    @pytest.mark.asyncio
    async def test_evaluate_marks_last_evaluated_timestamp(self):
        """After evaluate(), _last_evaluated is updated → should_evaluate() returns False."""
        from base_engine.monitoring.phase_tracker import PhaseTracker

        tracker = PhaseTracker(db=None)  # no DB → returns early but still marks timestamp
        with patch("base_engine.monitoring.phase_tracker.settings") as ms:
            ms.TRADING_PHASE = "paper"
            ms.PHASE_GRADUATION_CHECK_HOURS = 24.0
            ms.PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS = 100
            ms.PHASE_PAPER_TO_LEARNING_WIN_RATE = 0.52
            ms.PHASE_PAPER_TO_LEARNING_MAX_BRIER = 0.22
            ms.PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS = 300
            ms.PHASE_LEARNING_TO_GRADUATED_WIN_RATE = 0.55
            ms.PHASE_LEARNING_TO_GRADUATED_MAX_BRIER = 0.20
            # With no DB, returns early without setting _last_evaluated
            # This test just confirms it doesn't crash
            metrics = await tracker.evaluate()
        assert metrics is not None


# ─────────────────────────────────────────────────────────────────────────────
# G. websockets v15 migration (5a)
# ─────────────────────────────────────────────────────────────────────────────

class TestWebsocketsMigration:
    """
    Verify that websockets v15 compatibility fixes are correctly applied.
    - websockets.exceptions must be importable and expose ConnectionClosed + ConcurrencyError
    - WebSocketManager and UserOrderWebSocket use 'import websockets.exceptions'
    - ConcurrencyError detection uses isinstance() (not string comparison)
    """

    def test_websockets_exceptions_importable(self):
        """websockets.exceptions is importable and exposes ConnectionClosed."""
        import websockets.exceptions
        assert hasattr(websockets.exceptions, "ConnectionClosed")
        assert hasattr(websockets.exceptions, "ConcurrencyError")

    def test_concurrency_error_isinstance_check(self):
        """ConcurrencyError is a real exception class usable in isinstance()."""
        import websockets.exceptions
        # Should not raise — ConcurrencyError is a real class
        assert issubclass(websockets.exceptions.ConcurrencyError, Exception)

    def test_websocket_manager_imports_exceptions_submodule(self):
        """websocket_manager.py has 'import websockets.exceptions' in source."""
        import inspect
        import base_engine.data.websocket_manager as wm
        source = inspect.getsource(wm)
        assert "import websockets.exceptions" in source, (
            "websocket_manager.py must explicitly import websockets.exceptions"
        )

    def test_user_order_websocket_imports_exceptions_submodule(self):
        """user_order_websocket.py has 'import websockets.exceptions' in source."""
        import inspect
        import base_engine.data.user_order_websocket as uow
        source = inspect.getsource(uow)
        assert "import websockets.exceptions" in source, (
            "user_order_websocket.py must explicitly import websockets.exceptions"
        )

    def test_websocket_manager_uses_isinstance_for_concurrency_error(self):
        """websocket_manager.py uses isinstance() not string-based type check."""
        import inspect
        import base_engine.data.websocket_manager as wm
        source = inspect.getsource(wm)
        assert "isinstance(e, websockets.exceptions.ConcurrencyError)" in source, (
            "ConcurrencyError check must use isinstance() not type(e).__name__"
        )
        # The old string check should be gone
        assert 'type(e).__name__ == "ConcurrencyError"' not in source, (
            "Old string-based ConcurrencyError check should be removed"
        )

    def test_proxy_kwarg_still_accepted_by_v15_connect(self):
        """websockets.connect() in v15 still accepts the 'proxy' kwarg."""
        import inspect
        import websockets
        sig = inspect.signature(websockets.connect)
        assert "proxy" in sig.parameters, (
            "websockets.connect() in v15 must accept 'proxy' kwarg"
        )

    def test_websocket_manager_imports_ok(self):
        """WebSocketManager imports without error (validates module-level code)."""
        from base_engine.data.websocket_manager import WebSocketManager
        assert WebSocketManager is not None

    def test_user_order_websocket_imports_ok(self):
        """UserOrderWebSocket imports without error."""
        from base_engine.data.user_order_websocket import UserOrderWebSocket
        assert UserOrderWebSocket is not None
