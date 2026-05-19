"""S218: regression test for build_transaction await bug.

Pre-fix, approve_token() called `contract.functions.approve(...).build_transaction({...})`
without `await`. self.w3 is AsyncWeb3 (AsyncHTTPProvider + AsyncEth), so contract is
an AsyncContract and build_transaction returns a coroutine that must be awaited.
The unawaited coroutine was passed to sign_transaction() which raised TypeError
("transaction_dict must be dict-like"), crashing WeatherBot at startup pre-approval
whenever the wallet's USDCe allowance was below MAX.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from base_engine.execution import contract_manager as cm_mod


# 0x-prefixed 64-hex private key, well-formed test value
_TEST_PRIVATE_KEY = "0x" + ("1" * 64)


class TestApproveTokenBuildTransactionAwaited:
    """Verify build_transaction is awaited — pre-fix this raised TypeError."""

    @pytest.mark.asyncio
    async def test_approve_token_awaits_build_transaction(self):
        """build_transaction (AsyncContractFunction) must be awaited.

        Pre-fix: returned a coroutine that was passed to sign_transaction →
        TypeError "transaction_dict must be dict-like". Post-fix: returns dict,
        sign_transaction succeeds.
        """
        # Account mock with a deterministic address
        mock_account = MagicMock()
        mock_account.address = "0x0000000000000000000000000000000000000001"
        # sign_transaction must receive a dict (post-fix); fails if given a coroutine.
        # AssertionError will surface clearly if pre-fix regression returns.
        def _sign_transaction(tx):
            assert isinstance(tx, dict), (
                f"sign_transaction got non-dict (likely unawaited coroutine): {type(tx).__name__}"
            )
            mock_signed = MagicMock()
            mock_signed.raw_transaction = b"\x00" * 32
            return mock_signed
        mock_account.sign_transaction = _sign_transaction

        # Mock the AsyncContract's chain: contract.functions.approve(...).build_transaction(...)
        # AsyncMock makes build_transaction a coroutine returning a built dict.
        built_tx = {
            "chainId": 137,
            "gas": 100000,
            "gasPrice": 30_000_000_000,
            "nonce": 0,
            "from": mock_account.address,
        }
        mock_approve_fn = MagicMock()
        mock_approve_fn.build_transaction = AsyncMock(return_value=built_tx)
        mock_functions = MagicMock()
        mock_functions.approve = MagicMock(return_value=mock_approve_fn)
        mock_contract = MagicMock()
        mock_contract.functions = mock_functions

        # Mock w3.eth: contract(), get_transaction_count, gas_price, send_raw_transaction,
        # wait_for_transaction_receipt
        mock_w3 = MagicMock()
        mock_w3.eth.contract = MagicMock(return_value=mock_contract)
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)
        # gas_price is awaited as an attribute on AsyncEth — model with AsyncMock
        # via the get/set pattern: set attribute to a coroutine-returning mock.
        mock_gas_price = AsyncMock()
        mock_gas_price.__await__ = lambda *a, **kw: iter([30_000_000_000])
        # Simpler: just make it a property-like with a sync 30 gwei return; the code
        # uses `await self.w3.eth.gas_price` so we need it to be awaitable.
        async def _gas_price_coro():
            return 30_000_000_000
        # MagicMock attribute access returns MagicMock; assign coroutine instead.
        type(mock_w3.eth).gas_price = property(lambda self: _gas_price_coro())

        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"\x11" * 32)
        # wait_for_transaction_receipt returns a receipt with status=1
        mock_receipt = MagicMock()
        mock_receipt.status = 1
        mock_w3.eth.wait_for_transaction_receipt = AsyncMock(return_value=mock_receipt)

        manager = cm_mod.ContractManager(private_key=_TEST_PRIVATE_KEY)
        manager.account = mock_account
        manager.w3 = mock_w3

        # check_allowance / cache: skip to make approve_token go straight through
        # by passing amount explicitly (non-None) and patching ensure_client to noop
        with patch.object(manager, "ensure_client", AsyncMock(return_value=None)):
            result = await manager.approve_token(
                token_address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
                spender_address="0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
                amount=10**18,
            )

        # Build_transaction must have been awaited (call_count == 1, awaitable used)
        assert mock_approve_fn.build_transaction.await_count == 1, (
            "build_transaction was not awaited — regression to pre-S218 bug"
        )
        # No TypeError surfaced; result reports success path
        assert result["success"] is True, f"approve_token failed: {result}"
