"""S250: realized fill-price capture for cost basis (clob_adapter -> execution_engine -> order_gateway).

Bug: the live path recorded the SUBMITTED price as cost basis, never the realized fill.
  - clob_adapter._place_order_sync returned {"price": <submitted>} with no fill field.
  - execution_engine.place_order echoed the input `price` into update_position and its
    return dict; it never read a fill back.
  - order_gateway.confirm_position (the live cost-basis write) used its own effective_price.
Confirmed end-to-end this session for BOTH GTC and FOK.

Fix (defensive, additive):
  - clob_adapter._derive_fok_fill_price extracts a realized price from a matched FOK
    response ONLY when a clearly-named field parses to (0,1) AND passes a side-aware
    one-sided bound vs the actually-submitted FOK limit; else None (submitted-price
    fallback + response-keys warning so the true shape self-reveals on first live fill).
  - _place_order_sync emits a `fill_price` key (None on GTC / unresolved / paper).
  - execution_engine.place_order propagates `fill_price` into its return dict and uses
    it (fallback: submitted price) for the direct-path update_position cost basis.
  - order_gateway consumes result["fill_price"] at the confirm_position cost-basis write
    and the shadow-fill execution price.

The V2 matched-order response field name is UNVERIFIED (no live order at authoring time):
the guard makes a wrong guess non-corrupting (any accepted value is bounded <= submitted
for a BUY / >= submitted for a SELL), and the unresolved path preserves current behavior.

Cross-bot blast radius: shared base_engine execution path (all 14 bots). Behavior change:
live cost basis becomes the realized fill WHEN captured, else unchanged.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from base_engine.execution.clob_adapter import _derive_fok_fill_price
from base_engine.execution.execution_engine import ExecutionEngine


class TestDeriveFokFillPrice:
    """The validation guard is the safety-critical unit: a wrong field guess must never
    write a cost basis worse than the submitted limit."""

    def test_valid_buy_fill_within_bound_captured(self):
        # submitted BUY limit 0.60; realized fill 0.57 (better) -> captured
        assert _derive_fok_fill_price({"avgPrice": "0.57"}, "BUY", 0.60) == pytest.approx(0.57)

    def test_valid_sell_fill_within_bound_captured(self):
        # submitted SELL limit 0.40; realized fill 0.43 (better) -> captured
        assert _derive_fok_fill_price({"matched_price": 0.43}, "SELL", 0.40) == pytest.approx(0.43)

    def test_buy_fill_above_limit_rejected(self):
        # a BUY cannot fill worse than its limit; a value above limit+tol is a bad field -> None
        assert _derive_fok_fill_price({"avgPrice": 0.75}, "BUY", 0.60) is None

    def test_sell_fill_below_limit_rejected(self):
        assert _derive_fok_fill_price({"avgPrice": 0.20}, "SELL", 0.40) is None

    def test_half_tick_tolerance_allowed(self):
        # exactly limit + half-tick is within tolerance (rounding slack)
        assert _derive_fok_fill_price({"avgPrice": 0.605}, "BUY", 0.60) == pytest.approx(0.605)

    def test_out_of_range_price_rejected(self):
        assert _derive_fok_fill_price({"avgPrice": 1.5}, "BUY", 0.99) is None
        assert _derive_fok_fill_price({"avgPrice": 0.0}, "BUY", 0.60) is None
        assert _derive_fok_fill_price({"avgPrice": -0.1}, "BUY", 0.60) is None

    def test_unparseable_value_rejected(self):
        assert _derive_fok_fill_price({"avgPrice": "not-a-number"}, "BUY", 0.60) is None
        assert _derive_fok_fill_price({"avgPrice": None}, "BUY", 0.60) is None

    def test_plain_price_key_ignored(self):
        # "price" is the submitted-limit echo, NOT a fill; must not be captured
        assert _derive_fok_fill_price({"price": 0.55}, "BUY", 0.60) is None

    def test_no_candidate_field_returns_none(self):
        assert _derive_fok_fill_price({"status": "matched", "orderID": "x"}, "BUY", 0.60) is None

    def test_non_dict_returns_none(self):
        assert _derive_fok_fill_price(None, "BUY", 0.60) is None
        assert _derive_fok_fill_price("matched", "BUY", 0.60) is None

    def test_first_valid_candidate_key_wins(self):
        # iterate the known key set; the first present+valid one is used
        r = {"executed_price": 0.58, "avgPrice": 0.57}
        assert _derive_fok_fill_price(r, "BUY", 0.60) == pytest.approx(0.57)


_TEST_PRIVATE_KEY = "0x" + ("1" * 64)
_VALID_MARKET_ID = "0x" + "a" * 64
_VALID_TOKEN_ID = "12345678901234567890"


def _build_engine(clob_response):
    mock_client = MagicMock()
    mock_risk_manager = MagicMock()
    mock_risk_manager.check_risk_limits = AsyncMock(return_value={"allowed": True, "reasons": []})
    mock_risk_manager.update_position = AsyncMock(return_value=None)
    engine = ExecutionEngine(
        client=mock_client,
        risk_manager=mock_risk_manager,
        db=MagicMock(),
        private_key=_TEST_PRIVATE_KEY,
        kill_switch=None,
    )
    engine.contract_manager = None
    mock_adapter = MagicMock()
    mock_adapter.available = True
    mock_adapter.place_order = AsyncMock(return_value=clob_response)
    engine.clob_adapter = mock_adapter
    return engine, mock_adapter


class TestExecutionEnginePropagation:
    """execution_engine must thread fill_price into its return dict and use it (fallback:
    submitted price) for the direct-path cost basis."""

    @pytest.mark.asyncio
    async def test_fill_price_propagated_to_return(self):
        engine, _ = _build_engine(
            {"success": True, "order_id": "o1", "price": 0.60, "fill_price": 0.57}
        )
        result = await engine.place_order(
            bot_name="MirrorBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.60, confidence=0.7, skip_position_update=True,
        )
        assert result["success"] is True
        assert result["fill_price"] == pytest.approx(0.57)

    @pytest.mark.asyncio
    async def test_direct_path_uses_fill_price_for_cost_basis(self):
        engine, _ = _build_engine(
            {"success": True, "order_id": "o1", "price": 0.60, "fill_price": 0.57}
        )
        await engine.place_order(
            bot_name="MirrorBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.60, confidence=0.7, skip_position_update=False,
        )
        # update_position must receive the realized fill (0.57), not the submitted 0.60
        _, kwargs = engine.risk_manager.update_position.call_args
        assert kwargs["price"] == pytest.approx(0.57)

    @pytest.mark.asyncio
    async def test_missing_fill_price_falls_back_to_submitted(self):
        # GTC / unresolved shape: no fill_price key -> cost basis stays the submitted price
        engine, _ = _build_engine({"success": True, "order_id": "o1", "price": 0.60})
        result = await engine.place_order(
            bot_name="MirrorBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.60, confidence=0.7, skip_position_update=False,
        )
        assert result["fill_price"] is None
        _, kwargs = engine.risk_manager.update_position.call_args
        assert kwargs["price"] == pytest.approx(0.60)

    @pytest.mark.asyncio
    async def test_null_fill_price_falls_back_to_submitted(self):
        # explicit fill_price=None (FOK unresolved shape) -> fallback, no crash
        engine, _ = _build_engine(
            {"success": True, "order_id": "o1", "price": 0.60, "fill_price": None}
        )
        result = await engine.place_order(
            bot_name="MirrorBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.60, confidence=0.7, skip_position_update=False,
        )
        assert result["fill_price"] is None
        _, kwargs = engine.risk_manager.update_position.call_args
        assert kwargs["price"] == pytest.approx(0.60)
