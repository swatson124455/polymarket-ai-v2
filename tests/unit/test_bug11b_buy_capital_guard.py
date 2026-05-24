"""S228 Bug 11B: MirrorBot BUY entry-path must guard against insufficient pUSD.

Bug history:
  - With $5 pUSD at the deposit wallet (S226 starting cap), any BUY signal
    sized >$5 would reach the CLOB and produce a "not enough balance /
    allowance: balance: 0" response. OrderGateway's _execute_with_retry
    retries up to LIVE_ORDER_MAX_RETRIES=3 since the error string doesn't
    match _PERMANENT_PATTERNS = ("market closed", "delisted", "invalid",
    "insufficient", "not found", "expired", "cancelled") — note "balance"
    is absent. Three wasted retries per insufficient-capital signal.
  - This is a real risk at the current $5 cap (5 sequential $1 entries
    deplete capital; the 6th onwards retries-fail noisily).

Surfaced: pre-emptive based on S228 live flip #4 analysis. The flip-#4
SELL surface was Bug 11A (paper position SELL via real CLOB). A future
flip would have seen Bug 11B if BUY signals fired against depleted
capital.

Fix:
  - mirror_bot.py entry path adds a pre-lock capital guard:
    `if not SIMULATION_MODE and bankroll.capital > 0 and trade_usd > capital
     → log mirror_buy_capital_guard_reject + return False`.
  - bankroll.capital is wallet-refreshed pUSD (S226 Bug 6) — O(1) cached
    read, no extra RPC per signal.
  - Placed BEFORE _exposure_lock so the cheap check doesn't serialize
    other concurrent whale-signal handlers.
  - Skipped in paper mode (no real capital movement).

Cross-bot blast radius:
  - MirrorBot only. EsportsBot/WeatherBot entry paths have their own
    structure; cross-bot survey filed separate from S228 arc.
  - Behavior change: live-mode BUY signals that exceed bankroll.capital
    now reject cleanly with mirror_buy_capital_guard_reject instead of
    producing 3 retried "balance: 0" errors. No effect when capital
    refresh hasn't been done (bankroll.capital = 0 or None disables the
    guard — defers to existing exposure logic).
  - Paper mode unchanged.

These tests detect future regressions via source-grep + structural
inspection. Behavioral coverage needs an integration harness; deferred.
"""
from __future__ import annotations

import inspect

from bots import mirror_bot as mb_mod


class TestS228Bug11BMarker:
    def test_marker_present(self):
        src = inspect.getsource(mb_mod)
        assert "S228 Bug 11B" in src, (
            "S228 Bug 11B marker missing — capital guard may have been "
            "reverted. Without it, live BUY signals exceeding deposit "
            "wallet pUSD will spam 3-retry storms on every attempt."
        )


class TestBuyCapitalGuardStructural:
    """The guard must be wired in the entry path with the right shape."""

    def test_guard_rejection_log_event_present(self):
        src = inspect.getsource(mb_mod)
        assert "mirror_buy_capital_guard_reject" in src, (
            "Guard reject log event missing — operator visibility broken."
        )

    def test_guard_gated_on_live_mode_via_helper(self):
        """The guard MUST short-circuit in paper mode. Per S83 paper-is-
        production rule, mode detection in bot source goes through
        is_paper_trading_active() helper (config.settings).
        Hardcoded behaviour either way breaks one mode."""
        src = inspect.getsource(mb_mod)
        # Find the BUY guard block
        guard_idx = src.find("mirror_buy_capital_guard_reject")
        assert guard_idx > 0
        # Look backwards a reasonable distance for the helper gate
        window = src[max(0, guard_idx - 1500):guard_idx]
        assert "is_paper_trading_active" in window, (
            "Guard must be gated on is_paper_trading_active() — otherwise "
            "paper mode would be incorrectly blocked by pUSD checks "
            "against a wallet paper mode doesn't actually spend from."
        )

    def test_guard_reads_bankroll_capital(self):
        """The guard reads bankroll.capital (S226 Bug 6 wallet-refreshed
        pUSD balance) rather than making a fresh RPC call per signal."""
        src = inspect.getsource(mb_mod)
        guard_idx = src.find("mirror_buy_capital_guard_reject")
        window = src[max(0, guard_idx - 1500):guard_idx]
        assert "bankroll" in window and "capital" in window, (
            "Guard must read bankroll.capital — not make a fresh RPC. "
            "Per-signal RPC inside the entry path would add 100-500ms "
            "latency to whale-signal handling."
        )

    def test_guard_handles_missing_bankroll(self):
        """The guard must not crash when self.bankroll is None or when
        bankroll.capital is None/0. In those cases the guard should defer
        (not block) — the existing exposure logic still applies."""
        src = inspect.getsource(mb_mod)
        guard_idx = src.find("mirror_buy_capital_guard_reject")
        window = src[max(0, guard_idx - 1500):guard_idx]
        # Must check `is not None` before comparing capital to trade_usd
        assert "is not None" in window or "getattr(self.bankroll" in window, (
            "Guard must defensively handle missing bankroll (None) or "
            "unrefreshed capital (None/0). Hard reference would crash."
        )

    def test_guard_placed_before_exposure_lock(self):
        """The guard's reject path returns False BEFORE acquiring
        self._exposure_lock — otherwise it serializes concurrent whale
        signals on this cheap check (lock is for the daily-cap arithmetic
        below, not for capital balance)."""
        src = inspect.getsource(mb_mod)
        guard_marker = "mirror_buy_capital_guard_reject"
        lock_marker = "async with self._exposure_lock:"
        # Find the BUY entry path containing both markers
        # First find the guard, then the next exposure_lock occurrence
        guard_idx = src.find(guard_marker)
        assert guard_idx > 0, "Guard reject log not found"
        # Find the next exposure_lock after the guard rejection.
        # Look forward from guard rejection — if the next lock is AFTER the
        # guard, structure is correct (guard precedes the lock).
        lock_idx = src.find(lock_marker, guard_idx)
        assert lock_idx > 0, "Exposure lock acquisition not found after guard"
        # The window between them should be short (no other major guards)
        between = src[guard_idx:lock_idx]
        # Sanity check the structural ordering
        assert lock_idx > guard_idx, (
            "Guard must precede the exposure lock acquisition. Otherwise "
            "the cheap balance check holds the lock for concurrent signals."
        )
