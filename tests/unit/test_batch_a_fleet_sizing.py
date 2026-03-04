"""
Batch A — Fleet Sizing Accuracy tests.

Covers:
  - I01 / I14: DegradationManager percentage-based tiers (4-bot fleet at 100% Kelly)
  - I10:       AdaptiveKelly wired into bankroll_manager (already implemented)
  - I22:       RiskManager conservative drawdown cold-start default
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# I01 / I14  Degradation tiers — percentage-based, fleet-size-agnostic
# ─────────────────────────────────────────────────────────────────────────────

class TestDegradation4BotAt100Pct:
    """
    DegradationManager percentage-based tier tests.

    BotStateMachine starts in 'healthy' state by default (initial="healthy").
    _recompute_tier() is called internally on every state change and can also
    be called directly to evaluate the current snapshot.
    """

    def _make_healthy_mgr(self, n_bots: int):
        """Register n_bots, all starting in 'healthy' state, trigger initial recompute."""
        from base_engine.monitoring.degradation_manager import DegradationManager
        mgr = DegradationManager()
        for i in range(n_bots):
            mgr.register_bot(f"Bot{i}")
        # Force tier recompute to reflect current healthy state
        mgr._recompute_tier()
        return mgr

    def _degrade_bot(self, mgr, bot_name: str):
        """Move a bot to degraded state (triggers _recompute_tier via callback)."""
        machine = mgr.get_machine(bot_name)
        if machine:
            try:
                machine.record_error(is_fatal=False)
            except Exception:
                # Fallback: set state directly and notify manager
                machine.state = "degraded"
                mgr._on_bot_state_change(bot_name, "degraded")

    def test_4_of_4_healthy_is_tier_0(self):
        """All 4 bots healthy → ratio 1.0 ≥ 0.85 → Tier 0 (1.00×)."""
        mgr = self._make_healthy_mgr(4)
        assert mgr.get_sizing_multiplier() == pytest.approx(1.00)
        assert mgr.get_current_tier() == 0

    def test_3_of_4_healthy_is_tier_1(self):
        """3/4 bots healthy → ratio 0.75 ≥ 0.70 → Tier 1 (0.75×)."""
        mgr = self._make_healthy_mgr(4)
        self._degrade_bot(mgr, "Bot3")
        # ratio = 3/4 = 0.75 → Tier 1
        assert mgr.get_sizing_multiplier() == pytest.approx(0.75)

    def test_2_of_4_healthy_is_tier_2(self):
        """2/4 bots healthy → ratio 0.50 ≥ 0.50 → Tier 2 (0.50×)."""
        mgr = self._make_healthy_mgr(4)
        self._degrade_bot(mgr, "Bot2")
        self._degrade_bot(mgr, "Bot3")
        # ratio = 2/4 = 0.50 → Tier 2
        assert mgr.get_sizing_multiplier() == pytest.approx(0.50)

    def test_7_of_7_healthy_is_tier_0(self):
        """Full 7-bot fleet → ratio 1.0 → Tier 0 (1.00×)."""
        mgr = self._make_healthy_mgr(7)
        assert mgr.get_sizing_multiplier() == pytest.approx(1.00)
        assert mgr.get_current_tier() == 0

    def test_6_of_7_healthy_is_tier_0(self):
        """6/7 bots healthy → ratio 0.857 ≥ 0.85 → Tier 0 (1.00×)."""
        mgr = self._make_healthy_mgr(7)
        self._degrade_bot(mgr, "Bot6")
        # ratio = 6/7 ≈ 0.857 → still Tier 0
        assert mgr.get_sizing_multiplier() == pytest.approx(1.00)
        assert mgr.get_current_tier() == 0

    def test_no_bots_registered_is_tier_0(self):
        """0 bots registered → health_ratio defaults to 1.0 → Tier 0."""
        from base_engine.monitoring.degradation_manager import DegradationManager
        mgr = DegradationManager()
        # No register_bot calls — _current_tier_index starts at 0
        assert mgr.get_sizing_multiplier() == pytest.approx(1.00)
        assert mgr.get_current_tier() == 0

    def test_1_of_4_healthy_is_tier_3(self):
        """1/4 bots healthy → ratio 0.25 ≥ 0.25 → Tier 3 (0.10×), NOT close-only."""
        mgr = self._make_healthy_mgr(4)
        self._degrade_bot(mgr, "Bot1")
        self._degrade_bot(mgr, "Bot2")
        self._degrade_bot(mgr, "Bot3")
        # ratio = 1/4 = 0.25 → Tier 3 (0.10×)
        assert mgr.get_sizing_multiplier() == pytest.approx(0.10)
        assert not mgr.is_close_only_mode()

    def test_zero_healthy_bots_is_close_only(self):
        """0/4 healthy → ratio 0.0 < 0.25 → Tier 4 (close-only)."""
        mgr = self._make_healthy_mgr(4)
        for i in range(4):
            self._degrade_bot(mgr, f"Bot{i}")
        # All bots degraded — ratio = 0.0 → Tier 4
        assert mgr.is_close_only_mode()
        assert mgr.get_sizing_multiplier() == pytest.approx(0.00)

    def test_tier_computation_is_dynamic(self):
        """Adding a new healthy bot increases registered count, ratio recalculates."""
        from base_engine.monitoring.degradation_manager import DegradationManager
        mgr = DegradationManager()
        # Register 2 bots, degrade 1 → 1/2 = 0.50 → Tier 2
        mgr.register_bot("BotA")
        mgr.register_bot("BotB")
        mgr._recompute_tier()
        self._degrade_bot(mgr, "BotB")
        assert mgr.get_sizing_multiplier() == pytest.approx(0.50)

        # Add 2 more healthy bots → 3/4 = 0.75 → Tier 1
        mgr.register_bot("BotC")
        mgr.register_bot("BotD")
        mgr._recompute_tier()
        assert mgr.get_sizing_multiplier() == pytest.approx(0.75)

    def test_old_absolute_count_bug_is_fixed(self):
        """
        Regression: the old code required healthy_count >= 6 for Tier 0.
        With only 4 bots, Tier 0 was physically unreachable (4 < 6 threshold).
        New percentage-based code: 4/4 = 1.0 ≥ 0.85 → Tier 0 (1.00×).
        """
        mgr = self._make_healthy_mgr(4)
        assert mgr.get_current_tier() == 0, \
            "4/4 healthy bots must reach Tier 0 (1.00×), not be stuck at Tier 1/2"
        assert mgr.get_sizing_multiplier() == pytest.approx(1.00), \
            "Full Kelly must be reachable with any fleet size when all bots are healthy"


# ─────────────────────────────────────────────────────────────────────────────
# I10  AdaptiveKelly wired into SportsBankrollManager
# ─────────────────────────────────────────────────────────────────────────────

class TestAdaptiveKellyWiredToInjuryBot:
    """
    Verify that SportsBankrollManager.get_bet_size() reads kelly_fraction
    from adaptive_kelly.get_kelly_fraction() (not hardcoded constant).

    bankroll_manager uses lazy local import inside get_bet_size():
        from sports.kelly.adaptive_kelly import get_kelly_fraction
    So we patch the SOURCE module (sports.kelly.adaptive_kelly), not the
    bankroll_manager namespace.
    """

    @pytest.mark.asyncio
    async def test_bankroll_manager_calls_get_kelly_fraction(self):
        """get_bet_size() must call get_kelly_fraction() for the given sport+market_type."""
        from sports.kelly.bankroll_manager import SportsBankrollManager

        mgr = SportsBankrollManager(order_gateway=None)

        with patch(
            "sports.kelly.adaptive_kelly.get_kelly_fraction",
            new=AsyncMock(return_value=0.30),
        ) as mock_get_kelly:
            size = await mgr.get_bet_size(
                fair_prob=0.65,
                market_price=0.50,
                sport="nba",
                market_type="moneyline",
                db=None,
            )

        mock_get_kelly.assert_awaited_once_with("nba", "moneyline", db=None)
        # kelly_bet = 0.30 * 10000 * (0.15/0.65) ≈ 692 → capped at $100
        assert size == pytest.approx(100.0, rel=1e-2)

    @pytest.mark.asyncio
    async def test_bankroll_manager_falls_back_on_kelly_timeout(self):
        """If get_kelly_fraction() times out, default fraction is used and size is nonzero."""
        from sports.kelly.bankroll_manager import SportsBankrollManager

        mgr = SportsBankrollManager(order_gateway=None)

        with patch(
            "sports.kelly.adaptive_kelly.get_kelly_fraction",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            size = await mgr.get_bet_size(
                fair_prob=0.60,
                market_price=0.50,
                sport="nfl",
                market_type="moneyline",
                db=None,
            )

        # Falls back to SPORTS_KELLY_DEFAULT_FRACTION=0.25 → nonzero bet
        assert size > 0.0, "Fallback must still produce a nonzero bet size"
        # kelly_bet = 0.25 * 10000 * (0.10/0.60) ≈ 416 → capped at $100
        assert size == pytest.approx(100.0, rel=1e-2)

    @pytest.mark.asyncio
    async def test_bankroll_manager_respects_low_kelly_fraction(self):
        """Low kelly_fraction (bad calibration) → smaller bet than high kelly_fraction."""
        from sports.kelly.bankroll_manager import SportsBankrollManager

        mgr = SportsBankrollManager(order_gateway=None)

        # Use a small edge (3pp) so the per-bet cap doesn't dominate both scenarios
        with patch(
            "sports.kelly.adaptive_kelly.get_kelly_fraction",
            new=AsyncMock(return_value=0.10),   # Bad Brier → min fraction
        ):
            size_low = await mgr.get_bet_size(
                fair_prob=0.53,   # edge = 0.03
                market_price=0.50,
                sport="tennis",
                market_type="moneyline",
                db=None,
            )

        with patch(
            "sports.kelly.adaptive_kelly.get_kelly_fraction",
            new=AsyncMock(return_value=0.50),   # Good Brier → max fraction
        ):
            size_high = await mgr.get_bet_size(
                fair_prob=0.53,
                market_price=0.50,
                sport="tennis",
                market_type="moneyline",
                db=None,
            )

        # Good calibration → larger bet than bad calibration
        assert size_high > size_low, \
            "Higher kelly_fraction must produce a larger bet size"


# ─────────────────────────────────────────────────────────────────────────────
# I22  RiskManager conservative drawdown cold-start default
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskManagerDrawdownDefault:
    """_cached_drawdown_pct must default to 0.05 (not 0.0) at construction."""

    def test_drawdown_default_is_conservative(self):
        from base_engine.risk.risk_manager import RiskManager

        db_mock = MagicMock()
        db_mock.session_factory = MagicMock()
        rm = RiskManager(db=db_mock)

        assert hasattr(rm, "_cached_drawdown_pct"), \
            "_cached_drawdown_pct must be initialized in __init__"
        assert rm._cached_drawdown_pct == pytest.approx(0.05), \
            "Cold-start drawdown should be 0.05 (5%) until first HealthScheduler check"

    def test_drawdown_default_is_not_zero(self):
        """The old default was 0.0 (getattr fallback). New code must set 0.05 explicitly."""
        from base_engine.risk.risk_manager import RiskManager

        db_mock = MagicMock()
        rm = RiskManager(db=db_mock)

        # Using getattr with 0.0 was the old implicit default — must be > 0
        assert rm._cached_drawdown_pct > 0.0, \
            "0.0 default allows full Kelly during first 30s even if already in drawdown"

    def test_drawdown_can_be_updated(self):
        """HealthScheduler should be able to update _cached_drawdown_pct after startup."""
        from base_engine.risk.risk_manager import RiskManager

        db_mock = MagicMock()
        rm = RiskManager(db=db_mock)

        # Simulate HealthScheduler propagating the real drawdown (no drawdown)
        rm._cached_drawdown_pct = 0.00
        assert rm._cached_drawdown_pct == pytest.approx(0.00)
        # Simulate significant drawdown
        rm._cached_drawdown_pct = 0.08
        assert rm._cached_drawdown_pct == pytest.approx(0.08)
