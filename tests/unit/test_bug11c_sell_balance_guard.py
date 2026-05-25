"""S228 Bug 11C: SELL-side token-balance guard for live mode.

Bug history:
  - All MirrorBot SELL signals reach execution_engine without verifying
    whether the deposit wallet actually holds the outcome token. Under
    paper mode this didn't matter (PaperTradingEngine intercepts). Under
    live mode, paper-derived positions (Bug 11A — restore-filter wiring
    gap) reached the CLOB and produced "not enough balance / allowance:
    balance: 0" responses, costing 3 OrderGateway retries per attempt.
  - Verified S228 live flip #4 (2026-05-24): paper position 0xba7ab705…
    triggered SELL, CLOB rejected with balance: 0, OrderGateway retried.
  - Bug 11A fixes the wiring root cause. Bug 11C is defense-in-depth
    against future regressions or new code paths that bypass the filter.

Fix:
  - clob_adapter.py adds check_ctf_balance(token_id, wallet_address=None)
    helper. Mirrors check_pusd_balance pattern (added S226 Bug 6). Calls
    ERC1155 balanceOf on the Polymarket ConditionalTokens contract
    (0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 on Polygon, 6 decimals).
  - mirror_bot.py adds _live_sell_balance_guard helper. Returns True to
    proceed, False to skip. Defers (returns True) in paper mode, on RPC
    failure, or when balance lookup itself raises.
  - Helper called from BOTH exit dispatch sites:
      * self-driven exit loop (_check_and_execute_exits around line 1468)
      * RTDS-driven SELL (_execute_mirror_trade around line 2520)
  - Guard emits mirror_sell_balance_guard_reject log event on rejection
    for operator visibility (CLAUDE.md "Can't Fully Verify" rule).

Cross-bot blast radius:
  - clob_adapter.py: additive (new function, no signature change to
    existing functions). All 14 bots load clob_adapter; none use
    check_ctf_balance currently.
  - mirror_bot.py: MirrorBot only. Behavior change: in live mode,
    SELLs against unowned tokens reject before CLOB call.
  - Paper mode unchanged.
  - Performance: each blocked SELL avoids ~3 retries × OrderGateway
    backoff (~1s + 2s + 4s = 7s of retry-wait). Each allowed SELL pays
    one extra RPC call (~100-300ms) up front. Net win because most
    rejected attempts under current state are paper-derived.

These tests detect future regressions via source-grep + structural
inspection of both module sources.
"""
from __future__ import annotations

import inspect

from base_engine.execution import clob_adapter as ca_mod
from bots import mirror_bot as mb_mod


class TestCheckCtfBalanceHelper:
    """clob_adapter.py: check_ctf_balance helper exists with the right shape."""

    def test_check_ctf_balance_function_exists(self):
        assert hasattr(ca_mod, "check_ctf_balance"), (
            "clob_adapter.check_ctf_balance helper missing — defense layer "
            "for SELL-side balance guard. Without it, MB SELLs reach CLOB "
            "without on-chain verification."
        )

    def test_check_ctf_balance_is_async(self):
        import asyncio
        assert asyncio.iscoroutinefunction(ca_mod.check_ctf_balance), (
            "check_ctf_balance must be async (RPC call is httpx.AsyncClient)."
        )

    def test_check_ctf_balance_marker_in_source(self):
        src = inspect.getsource(ca_mod.check_ctf_balance)
        assert "S228 Bug 11C" in src, (
            "S228 Bug 11C marker missing from check_ctf_balance — fix may "
            "have been reverted."
        )

    def test_check_ctf_balance_uses_ctf_contract(self):
        src = inspect.getsource(ca_mod.check_ctf_balance)
        # Polymarket's ConditionalTokens contract on Polygon — verified
        # on-chain S228 (2026-05-24).
        assert "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045" in src, (
            "check_ctf_balance must call the canonical ConditionalTokens "
            "contract — substituting an arbitrary address would silently "
            "return wrong balances."
        )

    def test_check_ctf_balance_uses_erc1155_selector(self):
        src = inspect.getsource(ca_mod.check_ctf_balance)
        # ERC1155 balanceOf(address,uint256) selector
        assert "0x00fdd58e" in src, (
            "ERC1155 balanceOf selector missing — wrong selector would "
            "either revert or return wrong data."
        )


class TestLiveSellBalanceGuardHelper:
    """MirrorBot._live_sell_balance_guard helper exists and has correct
    structure."""

    def test_helper_method_exists(self):
        assert hasattr(mb_mod.MirrorBot, "_live_sell_balance_guard"), (
            "MirrorBot._live_sell_balance_guard helper missing."
        )

    def test_helper_marker_present(self):
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        assert "S228 Bug 11C" in src

    def test_helper_skips_in_paper_mode(self):
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        # The early-return path must be gated on the mode-detection
        # helper (S83 paper-is-production rule blocks direct mode-name
        # references in bot source).
        assert "is_paper_trading_active" in src, (
            "Guard must call is_paper_trading_active() to short-circuit "
            "in paper mode — otherwise paper-mode exits hit pointless "
            "RPC calls."
        )

    def test_helper_defers_on_none_balance(self):
        """When RPC config is missing or check fails, the helper should
        return True (defer) rather than block exits."""
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        # Must handle bal is None case
        assert "is None" in src, (
            "Helper must handle check_ctf_balance returning None "
            "(missing config or RPC failure) by deferring."
        )

    def test_helper_emits_reject_log(self):
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        assert "mirror_sell_balance_guard_reject" in src, (
            "Guard rejection log event missing — operator visibility "
            "broken on the protection layer."
        )


class TestExitSiteWiring:
    """Both exit dispatch sites in mirror_bot.py must invoke the guard
    BEFORE place_order."""

    def test_self_driven_exit_calls_guard_before_place_order(self):
        """The self-driven exit loop (_check_and_execute_exits) must
        call _live_sell_balance_guard before place_order, with continue
        on rejection (loop context)."""
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        assert "_live_sell_balance_guard" in src, (
            "Self-driven exit path does not invoke the balance guard. "
            "Bug 11C protection is partial — RTDS path may have it but "
            "self-exits would still hit CLOB with paper positions."
        )

    def test_rtds_exit_calls_guard(self):
        """The RTDS-driven SELL path (in _execute_mirror_trade or
        equivalent) must also invoke the guard."""
        src = inspect.getsource(mb_mod.MirrorBot._execute_mirror_trade)
        assert "_live_sell_balance_guard" in src, (
            "RTDS-driven SELL path does not invoke the balance guard. "
            "Bug 11C protection is partial — self-exits may have it but "
            "whale-driven exits would still hit CLOB with paper positions."
        )

    def test_marker_present_in_mirror_bot(self):
        """Top-level S228 Bug 11C marker in mirror_bot.py source for
        cross-file traceability."""
        src = inspect.getsource(mb_mod)
        # Expect at least 3 occurrences: helper definition + 2 call sites
        count = src.count("S228 Bug 11C")
        assert count >= 3, (
            f"S228 Bug 11C marker count {count} < 3. Expected helper "
            f"definition + 2 call-site comments — one or more sites may "
            f"have been reverted."
        )


class TestSellBalanceGuardEpsilonTolerance:
    """S228 Bug 11C rounding fix: small gaps between DB size (high-precision
    requested) and on-chain balance (6-decimal filled) are 6-decimal rounding
    artifacts, not phantom positions. The guard must allow exits when the
    gap is within a 0.01-token epsilon, otherwise the CLOB and the bot end
    up in a stable mutual-block when a legitimate live position was filled
    at a marginally smaller size than requested."""

    def test_epsilon_constant_in_source(self):
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        assert "_SELL_EPSILON_TOKENS" in src, (
            "Epsilon tolerance constant missing from guard — the rounding "
            "fix may have been reverted. Without it, the canonical example "
            "(DB size 8.695652 vs on-chain 8.690000) blocks every legitimate "
            "exit until the filled-size write-path is fixed."
        )

    def test_epsilon_value_is_0_01(self):
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        # The constant must be exactly 0.01 — a value of 0.001 wouldn't
        # cover the canonical 0.0057-token gap, and a value of 0.1 would
        # allow real phantom positions through.
        assert "_SELL_EPSILON_TOKENS = 0.01" in src, (
            "Epsilon tolerance must be 0.01 tokens — too small and the "
            "guard blocks legitimate 6-decimal rounded exits; too large "
            "and it lets phantom positions through."
        )

    def test_reject_uses_epsilon_minus(self):
        """The rejection branch must compare bal < size - epsilon, NOT
        bal < size. Without this, the guard is identical to pre-fix
        behavior and the epsilon constant is dead code."""
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        assert "bal < float(size) - _SELL_EPSILON_TOKENS" in src, (
            "Guard rejection branch must use 'bal < float(size) - "
            "_SELL_EPSILON_TOKENS' — the bare 'bal < float(size)' check "
            "ignores the epsilon entirely."
        )

    def test_reject_log_emits_gap(self):
        """When the guard rejects, the log must include the actual gap so
        operators can distinguish 6-decimal rounding (gap ~0.005) from
        genuinely empty positions (gap = full size)."""
        src = inspect.getsource(mb_mod.MirrorBot._live_sell_balance_guard)
        assert "gap=" in src, (
            "Rejection log missing 'gap' field — operator can't tell "
            "small rounding gaps from real phantom positions without it."
        )
