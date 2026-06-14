"""S245 §0: Pre-trade balance guard must check pUSD (deposit wallet), not USDC.e (EOA).

Bug: execution_engine.place_order's pre-trade balance check read
contract_manager.get_usdce_balance() — USDC.e (0x2791) at the EOA. V2 trading
collateral is pUSD (0xC011) at the deposit wallet (WI-24: getCollateral() on both
V2 exchanges returns pUSD). The deposit wallet held pUSD but $0.00 USDC.e, so the
guard returned "Insufficient USDC: have $0.00" and permanently rejected 100% of
live BUYs. The sibling approval step was already V2-aware (S228 Bug 8); this balance
check was the one site that missed it.

Fix: repoint to clob_adapter.check_pusd_balance() (the WI-24 accessor; the deposit
wallet it reads IS the CLOB order funder). Behaviour:
  - sufficient pUSD  -> order proceeds to the CLOB
  - shortfall (bal < cost) -> "Insufficient pUSD" reject; "insufficient" keeps the
    order_gateway _PERMANENT_PATTERNS classification (no wasteful in-scan retry; the
    signal re-evaluates next scan when capital changes)
  - None (RPC/config READ FAILURE, distinct from a 0.0 zero-balance) -> fail-closed
    skip with a RETRYABLE error (no _PERMANENT_PATTERNS substring) + distinct log
  - SIMULATION_MODE -> guard skipped entirely (paper unaffected)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from base_engine.execution.execution_engine import ExecutionEngine
from config.settings import settings

_TEST_PRIVATE_KEY = "0x" + ("1" * 64)
_VALID_MARKET_ID = "0x" + "a" * 64
_VALID_TOKEN_ID = "12345678901234567890"

# Mirror of order_gateway.OrderGateway._PERMANENT_PATTERNS (the retry classifier).
_PERMANENT_PATTERNS = (
    "market closed", "delisted", "invalid", "insufficient",
    "not found", "expired", "cancelled",
)


def _build_live_engine(clob_response=None):
    """ExecutionEngine with a TRUTHY contract_manager (so the pre-trade balance
    guard runs) and a mock clob_adapter returning `clob_response`."""
    mock_risk_manager = MagicMock()
    mock_risk_manager.check_risk_limits = AsyncMock(return_value={"allowed": True, "reasons": []})
    mock_risk_manager.update_position = AsyncMock(return_value=None)

    engine = ExecutionEngine(
        client=MagicMock(),
        risk_manager=mock_risk_manager,
        db=MagicMock(),
        private_key=_TEST_PRIVATE_KEY,
        kill_switch=None,
    )
    # contract_manager must be truthy so `if self.contract_manager and ...` runs the
    # guard; its V2 approval short-circuits (S228 Bug 8) so we just stub it success.
    cm = MagicMock()
    cm.ensure_usdce_approved = AsyncMock(return_value={"success": True, "already_approved": True})
    engine.contract_manager = cm

    adapter = MagicMock()
    adapter.available = True
    adapter.place_order = AsyncMock(return_value=clob_response or {"success": True, "order_id": "ord_ok"})
    engine.clob_adapter = adapter
    return engine, adapter


async def _place(engine, size=1.0, price=0.5):
    return await engine.place_order(
        bot_name="MirrorBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
        side="YES", size=size, price=price, confidence=0.7, skip_position_update=True,
    )


@pytest.fixture
def _live(monkeypatch):
    monkeypatch.setattr(settings, "SIMULATION_MODE", False, raising=False)


class TestPretradePusdGuard:

    @pytest.mark.asyncio
    async def test_sufficient_pusd_allows_order(self, _live):
        """A funded wallet (the post-fix reality, ~$34 pUSD) lets the order reach the CLOB."""
        engine, adapter = _build_live_engine()
        with patch("base_engine.execution.clob_adapter.check_pusd_balance",
                   AsyncMock(return_value=34.0)):
            result = await _place(engine, size=1.0, price=0.5)  # cost $0.50
        assert result["success"] is True
        adapter.place_order.assert_awaited()  # order actually reached the CLOB

    @pytest.mark.asyncio
    async def test_insufficient_pusd_rejects_permanent(self, _live):
        engine, adapter = _build_live_engine()
        with patch("base_engine.execution.clob_adapter.check_pusd_balance",
                   AsyncMock(return_value=0.40)):
            result = await _place(engine, size=1.0, price=0.5)  # cost $0.50 > $0.40
        assert result["success"] is False
        assert "Insufficient pUSD" in result["error"]
        # Classified PERMANENT by order_gateway -> no in-scan retry.
        assert any(p in result["error"].lower() for p in _PERMANENT_PATTERNS)
        adapter.place_order.assert_not_awaited()  # never reached the CLOB

    @pytest.mark.asyncio
    async def test_none_balance_skips_retryable(self, _live):
        """None = RPC/config read failure -> fail-closed skip, but RETRYABLE."""
        engine, adapter = _build_live_engine()
        with patch("base_engine.execution.clob_adapter.check_pusd_balance",
                   AsyncMock(return_value=None)):
            result = await _place(engine, size=1.0, price=0.5)
        assert result["success"] is False
        # RETRYABLE: must contain NO permanent pattern so the gateway re-checks / re-evaluates.
        assert not any(p in result["error"].lower() for p in _PERMANENT_PATTERNS), result["error"]
        adapter.place_order.assert_not_awaited()  # did not submit on unverified funding

    @pytest.mark.asyncio
    async def test_zero_balance_is_not_none_and_blocks(self, _live):
        """0.0 is a genuine empty wallet (a successful read), NOT a read failure ->
        treated as a shortfall (permanent), not the retryable None path."""
        engine, adapter = _build_live_engine()
        with patch("base_engine.execution.clob_adapter.check_pusd_balance",
                   AsyncMock(return_value=0.0)):
            result = await _place(engine, size=1.0, price=0.5)
        assert result["success"] is False
        assert "Insufficient pUSD" in result["error"]
        adapter.place_order.assert_not_awaited()


class TestSimulationModeUnaffected:

    @pytest.mark.asyncio
    async def test_simulation_mode_skips_pusd_check(self, monkeypatch):
        monkeypatch.setattr(settings, "SIMULATION_MODE", True, raising=False)
        engine, adapter = _build_live_engine()
        with patch("base_engine.execution.clob_adapter.check_pusd_balance",
                   AsyncMock(return_value=0.0)) as mock_check:
            result = await _place(engine, size=1.0, price=0.5)
        mock_check.assert_not_awaited()  # guard is gated by `not SIMULATION_MODE`
        assert result["success"] is True


class TestSourceRegression:

    def test_pretrade_site_uses_pusd_not_usdce(self):
        import inspect
        from base_engine.execution import execution_engine as ee_mod
        src = inspect.getsource(ee_mod.ExecutionEngine.place_order)
        assert "check_pusd_balance" in src, "pre-trade guard must read pUSD (WI-24 accessor)"
        assert "Insufficient pUSD" in src, "shortfall error must name pUSD"
        assert "get_usdce_balance" not in src, (
            "pre-trade guard must not read USDC.e@EOA — the S245 §0 bug (regression guard)"
        )
