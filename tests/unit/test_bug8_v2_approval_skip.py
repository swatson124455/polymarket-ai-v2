"""S228 Bug 8: V2-aware early-return in ContractManager approval helpers.

Bug history:
  - S226 migrated MirrorBot to CLOB V2 (deposit-wallet flow). Under V2, the
    deposit wallet (an EIP-1271 smart contract wallet provisioned by
    Polymarket's relayer at sign-in) holds both pUSD (buying power) and
    outcome tokens (ERC-1155 CTF positions). The EOA only signs messages
    via EIP-1271 lookup.
  - S226 §2 noted: USDC.e allowance from EOA to V2 Exchange is "UNLIMITED
    but moot (V2 uses deposit wallet for buying power)". The legacy V1
    `ensure_usdce_approved` call still ran on every BUY and silently
    passed via "already_approved" — no functional effect, but wasted a
    per-order RPC round-trip.
  - S227 second live flip surfaced Bug 8: `ensure_outcome_token_approved`
    has the same V1-legacy shape but receives a UInt256 token_id (not a
    hex address). `_is_valid_address(token_id)` rejects → every SELL
    trade returns `live_order_permanent_reject` before reaching CLOB.
  - On-chain verification 2026-05-24:
      isApprovedForAll(deposit_wallet, V2_Exchange) on CTF = True
      isApprovedForAll(EOA, V2_Exchange) on CTF = False (expected — EOA
      doesn't hold outcome tokens under V2)
    Confirms Polymarket's relayer provisioned the deposit-wallet-side
    approval at sign-in. EOA-side per-order approval is structurally
    moot under V2.

Fix:
  - Both `ensure_usdce_approved` and `ensure_outcome_token_approved` now
    early-return success when `DEPOSIT_WALLET_ADDRESS` is set in settings,
    with a log event (`usdce_approval_skipped_v2` and
    `outcome_token_approval_skipped_v2`) for operator visibility.
  - V1 path preserved unchanged for bots without V2 deposit-wallet config.

These tests detect future regressions via behavioral assertions and source-
grep, mirroring the S217/S218/S227 regression-test pattern.
"""
from __future__ import annotations

import pytest

from base_engine.execution import contract_manager as cm_mod


# Well-formed 32-byte test private key (same convention as test_contract_manager_approve.py)
_TEST_PRIVATE_KEY = "0x" + ("1" * 64)
# A real UInt256 token_id captured from the S227 live-flip rejection log
_REAL_UINT256_TOKEN_ID = (
    "84965742918524303103337050361064035842701096761205716399299962137721559986480"
)
# Production deposit wallet address (per S226 §3 Bug 3, provisioned 2026-05-20)
_DEPOSIT_WALLET = "0xBB3988D74a853ddC16f22eEC52fa53E3Cedd2247"


class TestOutcomeTokenApprovalV2Skip:
    """Bug 8 primary: ensure_outcome_token_approved must skip the V1 validation
    when V2 deposit-wallet flow is active (DEPOSIT_WALLET_ADDRESS set)."""

    @pytest.mark.asyncio
    async def test_v2_path_accepts_uint256_token_id(self, monkeypatch):
        """The exact UInt256 token_id that surfaced Bug 8 in S227 must now
        return success without hitting the V1 hex-address validator."""
        monkeypatch.setattr(cm_mod.settings, "DEPOSIT_WALLET_ADDRESS", _DEPOSIT_WALLET)
        manager = cm_mod.ContractManager(private_key=_TEST_PRIVATE_KEY)

        result = await manager.ensure_outcome_token_approved(
            _REAL_UINT256_TOKEN_ID, amount_tokens=10.0
        )

        assert result["success"] is True, (
            f"V2 path rejected the UInt256 token_id — Bug 8 fix regressed: {result}"
        )
        assert result.get("v2_skipped") is True, (
            "V2-skip marker missing from response — silent regression risk."
        )

    @pytest.mark.asyncio
    async def test_v2_path_preserves_no_wallet_error(self, monkeypatch):
        """Even with V2 active, no-wallet must still error (the skip path
        must NOT mask a missing wallet)."""
        monkeypatch.setattr(cm_mod.settings, "DEPOSIT_WALLET_ADDRESS", _DEPOSIT_WALLET)
        manager = cm_mod.ContractManager(private_key="")
        manager.account = None  # belt-and-suspenders

        result = await manager.ensure_outcome_token_approved(
            _REAL_UINT256_TOKEN_ID, amount_tokens=10.0
        )

        assert result["success"] is False
        assert "No wallet configured" in result["error"]

    @pytest.mark.asyncio
    async def test_v1_path_still_rejects_uint256_token_id(self, monkeypatch):
        """When V2 is NOT active (no DEPOSIT_WALLET_ADDRESS), legacy V1
        behavior must be preserved — UInt256 token_id still rejects via
        _is_valid_address. Regression guard for the V1 fallback path."""
        monkeypatch.setattr(cm_mod.settings, "DEPOSIT_WALLET_ADDRESS", "")
        manager = cm_mod.ContractManager(private_key=_TEST_PRIVATE_KEY)

        result = await manager.ensure_outcome_token_approved(
            _REAL_UINT256_TOKEN_ID, amount_tokens=10.0
        )

        assert result["success"] is False, (
            "V1 fallback path no longer rejects UInt256 — V1 behavior changed unintentionally."
        )
        assert "Invalid token address" in result["error"]


class TestUsdceApprovalV2Skip:
    """Bug 8 symmetric: ensure_usdce_approved should also skip the V1 RPC
    work when V2 deposit-wallet flow is active. The V1 BUY-side call was
    silently passing under V2 (EOA has unlimited USDC.e allowance), but
    cost a per-order on-chain RPC round-trip. Skip is correctness + latency."""

    @pytest.mark.asyncio
    async def test_v2_path_skips_usdce_check(self, monkeypatch):
        """V2 active → ensure_usdce_approved returns success without
        attempting any allowance check or RPC call."""
        monkeypatch.setattr(cm_mod.settings, "DEPOSIT_WALLET_ADDRESS", _DEPOSIT_WALLET)
        manager = cm_mod.ContractManager(private_key=_TEST_PRIVATE_KEY)
        # Sentinel: if check_allowance is called, the test should fail.
        # We deliberately do NOT set up w3/ensure_client mocks — if the V2
        # skip regresses, the real RPC attempt will time out or crash.
        # The asyncio framework will surface that as a test failure.

        result = await manager.ensure_usdce_approved(amount_usd=100.0)

        assert result["success"] is True, (
            f"V2 path failed to skip ensure_usdce_approved: {result}"
        )
        assert result.get("v2_skipped") is True

    @pytest.mark.asyncio
    async def test_v2_path_preserves_no_wallet_error_usdce(self, monkeypatch):
        """V2 skip must NOT mask a missing-wallet error."""
        monkeypatch.setattr(cm_mod.settings, "DEPOSIT_WALLET_ADDRESS", _DEPOSIT_WALLET)
        manager = cm_mod.ContractManager(private_key="")
        manager.account = None

        result = await manager.ensure_usdce_approved(amount_usd=100.0)

        assert result["success"] is False
        assert "No wallet configured" in result["error"]

    @pytest.mark.asyncio
    async def test_v2_path_preserves_negative_amount_error_usdce(self, monkeypatch):
        """V2 skip must NOT mask a negative-amount input validation error.
        The negative-amount check runs before the V2 skip by design."""
        monkeypatch.setattr(cm_mod.settings, "DEPOSIT_WALLET_ADDRESS", _DEPOSIT_WALLET)
        manager = cm_mod.ContractManager(private_key=_TEST_PRIVATE_KEY)

        result = await manager.ensure_usdce_approved(amount_usd=-1.0)

        assert result["success"] is False
        assert "negative" in result["error"].lower()


class TestS228Bug8SourceRegression:
    """Source-grep regression tests mirroring S217/S218/S227 Bug 7 pattern.
    Detects accidental removal of the V2-skip code path or its operator-
    visible log events."""

    def test_s228_bug8_marker_present(self):
        """Production source must contain the S228 Bug 8 marker for
        grep-ability (appears in BOTH approval functions)."""
        import inspect
        src = inspect.getsource(cm_mod)
        assert src.count("S228 Bug 8") >= 2, (
            "S228 Bug 8 marker missing from one or both approval functions — "
            "Bug 8 fix may have been partially reverted."
        )

    def test_outcome_token_skip_log_event_present(self):
        """outcome_token_approval_skipped_v2 log event must be emitted on
        V2 skip — operator startup/runtime visibility for the V2 path."""
        import inspect
        src = inspect.getsource(cm_mod)
        assert "outcome_token_approval_skipped_v2" in src, (
            "outcome_token_approval_skipped_v2 log event removed — silent "
            "regression risk for V2 outcome-token approval skip."
        )

    def test_usdce_skip_log_event_present(self):
        """usdce_approval_skipped_v2 log event must be emitted on V2 skip."""
        import inspect
        src = inspect.getsource(cm_mod)
        assert "usdce_approval_skipped_v2" in src, (
            "usdce_approval_skipped_v2 log event removed — silent regression "
            "risk for V2 USDCe approval skip."
        )

    def test_v2_skip_gated_on_deposit_wallet_address(self):
        """Both V2 skips must be gated on DEPOSIT_WALLET_ADDRESS to preserve
        V1 fallback for any bot without V2 deposit-wallet config."""
        import inspect
        src = inspect.getsource(cm_mod)
        # Find each S228 marker; verify the surrounding window has the gate
        marker = "S228 Bug 8"
        cursor = 0
        gate_windows = 0
        while True:
            idx = src.find(marker, cursor)
            if idx == -1:
                break
            window = src[idx:idx + 1500]
            if "DEPOSIT_WALLET_ADDRESS" in window:
                gate_windows += 1
            cursor = idx + len(marker)
        assert gate_windows >= 2, (
            "V2 skip blocks not gated on DEPOSIT_WALLET_ADDRESS — bots "
            "without V2 deposit-wallet config could skip approval incorrectly."
        )
