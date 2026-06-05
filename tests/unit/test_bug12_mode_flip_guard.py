"""S230 Bug 12: Mid-runtime mode-flip guard for MirrorBot.

Bug history:
  - 2026-05-24 live-trading experiment opened 4 positions on the deposit
    wallet. SIMULATION_MODE was subsequently flipped to true. Stop-loss
    fired on those (still in _open_positions because Bug 11A wasn't
    deployed yet). place_order routed via the bot's *current mode*
    (paper) rather than the *position's* is_paper flag. PaperTrading
    Engine simulated the SELLs, marked positions.status='closed', never
    touched the on-chain CTF tokens. Result: $3.68 pUSD spent live,
    tokens stuck on-chain, DB reporting them closed. Documented in
    feedback_mb_top_priority.md and WALLET_LEDGER.md.

  - Bug 11A (deployed 20260525_141133) filters _open_positions at restart
    by is_paper to match SIMULATION_MODE. Closes the startup-side root
    cause. Does NOT cover the mid-runtime mode-flip case: if env reloads
    without service restart (or any future path that toggles mode at
    runtime), _open_positions still holds the OLD mode's positions and
    routes through whatever the bot's mode is when exit fires.

  - Bug 12 guard: snapshot is_paper_trading_active() at the moment
    _restore_state_on_startup populates the in-memory dict, and refuse
    to act (in both exit and entry paths) if the current mode no longer
    matches. Surfaces a warning rather than masking — operator workflow
    is "always restart polymarket-mirror when changing SIMULATION_MODE."

Fix shape:
  - mirror_bot.py __init__: self._state_restore_mode_is_paper = None
  - mirror_bot.py _restore_state_on_startup: capture mode after the
    existing is_paper_trading_active() call (around line 338).
  - mirror_bot.py _check_and_execute_exits: guard at top, after the
    early-return for empty _open_positions. Logs warning + returns.
  - mirror_bot.py _execute_mirror_trade: guard at top, before any
    state mutation. Logs warning + returns False.

Cross-bot blast radius:
  - MirrorBot only. EsportsBot and WeatherBot do not have the
    _open_positions in-memory dict pattern (positions are reconstructed
    per-scan from DB) and aren't subject to this class of mid-runtime
    drift.
  - Paper mode behavior: identical when mode hasn't flipped. Same when
    mode has flipped but no exits/entries scheduled this scan.
  - Performance: zero overhead in steady state (one bool compare per
    scan cycle).

These tests verify structural presence of the guard in the source
file. Source-grep approach mirrors test_bug11c_sell_balance_guard.py
because functional integration tests would require a full MirrorBot
bootstrap with DB + base_engine which is out of scope for a unit
file.
"""
from __future__ import annotations

import inspect

from bots import mirror_bot as mb_mod


class TestBug12GuardInit:
    """__init__ initializes the snapshot attribute as a sentinel."""

    def test_state_restore_mode_is_paper_in_init(self):
        src = inspect.getsource(mb_mod.MirrorBot.__init__)
        assert "_state_restore_mode_is_paper" in src, (
            "MirrorBot.__init__ must initialize self._state_restore_mode_is_paper. "
            "Without the attribute, the guard in _check_and_execute_exits / "
            "_execute_mirror_trade will AttributeError on first scan."
        )
        # Initialized to None as sentinel ("restore hasn't run yet" → guard inert).
        assert "_state_restore_mode_is_paper: Optional[bool] = None" in src or \
               "_state_restore_mode_is_paper = None" in src, (
            "Init must use None sentinel so the guard treats unpopulated state "
            "as no-guard (backwards-compat with code paths that bypass restore)."
        )


class TestBug12GuardRestoreCapture:
    """_restore_state_on_startup captures the mode for later comparison."""

    def test_restore_captures_mode_snapshot(self):
        src = inspect.getsource(mb_mod.MirrorBot._restore_state_on_startup)
        assert "self._state_restore_mode_is_paper = _is_paper_mode" in src, (
            "_restore_state_on_startup must snapshot is_paper_trading_active() "
            "into self._state_restore_mode_is_paper. Without the snapshot, the "
            "guard can't detect a subsequent mode flip."
        )


class TestBug12GuardExitPath:
    """_check_and_execute_exits refuses to act when mode flipped mid-runtime."""

    def test_exit_path_has_guard(self):
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        # Must reference both the snapshot AND the live mode helper.
        assert "_state_restore_mode_is_paper" in src, (
            "_check_and_execute_exits must read self._state_restore_mode_is_paper "
            "to compare against current mode. Missing = Bug 12 regression."
        )
        assert "is_paper_trading_active()" in src, (
            "_check_and_execute_exits must call is_paper_trading_active() to "
            "compare current mode against restore-time snapshot."
        )

    def test_exit_path_logs_warning_on_mismatch(self):
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        assert "bug12_mode_flip_detected_exits_skipped" in src, (
            "Guard must emit a structured log event with the canonical name "
            "'bug12_mode_flip_detected_exits_skipped' for operator visibility "
            "(CLAUDE.md 'Can't Fully Verify' rule + LogMiner pattern matching)."
        )

    def test_exit_path_returns_when_mismatch(self):
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        # The guard should appear BEFORE the main exit loop so the early return
        # skips ALL exit attempts on a mode flip.
        guard_idx = src.find("bug12_mode_flip_detected_exits_skipped")
        # The early-return `if not self._open_positions: return` is the first
        # `return` statement. Guard should be near the top.
        first_loop_idx = src.find("for ")
        assert guard_idx != -1 and first_loop_idx != -1, "Markers missing"
        assert guard_idx < first_loop_idx, (
            "Guard must fire BEFORE the main exit loop. Otherwise some exits "
            "may execute before the guard rejects subsequent ones."
        )


class TestBug12GuardEntryPath:
    """_execute_mirror_trade refuses to open new positions on mode flip."""

    def test_entry_path_has_guard(self):
        src = inspect.getsource(mb_mod.MirrorBot._execute_mirror_trade)
        assert "_state_restore_mode_is_paper" in src, (
            "_execute_mirror_trade must read self._state_restore_mode_is_paper. "
            "Missing = entry path can still open positions in the wrong mode "
            "after a mid-runtime flip, defeating the guard."
        )
        assert "is_paper_trading_active()" in src

    def test_entry_path_logs_warning_on_mismatch(self):
        src = inspect.getsource(mb_mod.MirrorBot._execute_mirror_trade)
        assert "bug12_mode_flip_detected_entry_skipped" in src, (
            "Entry guard must emit 'bug12_mode_flip_detected_entry_skipped' "
            "(distinct from exit guard's event name for log filtering)."
        )

    def test_entry_path_returns_false_on_mismatch(self):
        src = inspect.getsource(mb_mod.MirrorBot._execute_mirror_trade)
        # Find the guard block and verify it returns False (caller treats as reject).
        guard_idx = src.find("bug12_mode_flip_detected_entry_skipped")
        assert guard_idx != -1, "Entry-side guard marker missing"
        # The `return False` should appear shortly after the guard log call.
        post_guard = src[guard_idx:guard_idx + 600]
        assert "return False" in post_guard, (
            "_execute_mirror_trade returns bool. Guard must return False on "
            "mismatch so the caller treats it as a clean reject rather than "
            "an error. True would re-enter the mode-mismatch issue."
        )


class TestBug12GuardOrdering:
    """Guard fires BEFORE Bug 11C SELL balance guard, so mode-flip catches
    paper-routed exits BEFORE the on-chain balance check (which would also
    reject for paper-derived positions but for a different reason)."""

    def test_exit_guard_before_bug11c_guard(self):
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        bug12_idx = src.find("bug12_mode_flip_detected_exits_skipped")
        bug11c_idx = src.find("_live_sell_balance_guard")
        # Both markers should be present.
        assert bug12_idx != -1, "Bug 12 exit guard missing"
        assert bug11c_idx != -1, "Bug 11C SELL balance guard missing (regression)"
        # Bug 12 fires first — surfaces the mode-flip BEFORE attempting an
        # on-chain RPC call that would also fail. Cheaper + clearer diagnostic.
        assert bug12_idx < bug11c_idx, (
            "Bug 12 mode-flip guard must fire BEFORE Bug 11C balance guard. "
            "Otherwise a mode-flipped paper-derived position would emit a "
            "balance-guard rejection (misleading) instead of the canonical "
            "mode-flip log line."
        )
