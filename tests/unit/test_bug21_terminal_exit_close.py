"""Bug 21 (S233): MirrorBot exit path must close resolved/delisted positions
instead of re-attempting a doomed SELL every scan.

Bug history:
  - S231 live re-flip surfaced an overnight escalation storm. S232 mis-attributed
    the residual to "paper-position leak"; the true cause was the exit path.
  - S233 (this fix) confirmed via journal on the live bot (2026-05-29):
      * 0x9bce19… — resolved YES winner; SELL price=1.0 → CLOB "invalid price
        (1.0), min: 0.001 - max: 0.999". 86 of 92 root-trigger lines in 24h.
      * 0x13c91c7a… — resolved NO loser; SELL price=0.0 → "invalid price (0.0)".
      * 0x78a736… — delisted; "the orderbook 4425… does not exist".
    These rejections are NOT slippage, so the pre-fix `else` branch merely
    cleared the slippage streak and re-issued the same SELL on the next scan,
    forever. The per-scan rejections drove the live CircuitBreaker into the
    Bug 17 in-process kill-switch escalation cycle (consecutive_reopens=48),
    freezing all new entries for 24h+.

Fix (bots/mirror_bot.py, MirrorBot only):
  - MIRROR_TERMINAL_EXIT_PATTERNS: case-insensitive substrings of the
    order-result `error` string that mean a SELL can never fill. Deliberately
    narrow — "insufficient" (balance) is excluded; that path is owned by
    _live_sell_balance_guard and may be a transient sync gap, not a dead market.
  - _terminal_reject_count: per-position consecutive-reject counter; close only
    after _MIRROR_TERMINAL_REJECT_CONFIRM (2) consecutive terminal rejections so
    a single transient CLOB blip can't orphan a live holding.
  - _close_position_terminal: drops the position from in-memory tracking,
    frees daily + category exposure under the lock, marks the DB row
    status='closed' (3-retry), and logs mirror_redemption_pending (resolved
    WINNER — auto-redemption is ABI-walled per PHASE_N_REDEMPTION_AUTOMATION.md)
    or mirror_terminal_position_closed (loser/delisted). Books NO P&L — the
    resolution backfill path owns resolution P&L (same contract as the normal
    close path).

Cross-bot blast radius:
  - mirror_bot.py only. No shared module touched. order_gateway/base_engine
    read-only verified to pass the `error` field through unchanged.
  - No existing signature or external interface changed.
  - Paper mode unaffected (terminal SELL rejections originate from the live
    CLOB path).
"""
from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict
from unittest import mock

from bots import mirror_bot as mb_mod
from bots.mirror_bot import MirrorBot


# ─────────────────────────────────────────────────────────────────────────
# Helpers: minimal fake DB so _close_position_terminal can run without a real
# BaseEngine. get_session() returns an async context manager; execute/commit
# record statements for assertions.
# ─────────────────────────────────────────────────────────────────────────
class _FakeSession:
    def __init__(self, recorder):
        self._rec = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self._rec.append((str(stmt), params))

    async def commit(self):
        pass


class _FakeDB:
    def __init__(self, recorder):
        self._rec = recorder

    def get_session(self):
        return _FakeSession(self._rec)


class _FakeEngine:
    def __init__(self, recorder):
        self.db = _FakeDB(recorder)


def _run(coro):
    """Run a coroutine on a private loop WITHOUT mutating the process-wide
    'current event loop'. asyncio.run() sets the current loop to None on exit,
    which breaks later tests that use the deprecated asyncio.get_event_loop()
    (e.g. the WeatherBot suite). new_event_loop() never registers itself as the
    current loop, so global event-loop state is left exactly as we found it."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_bot(pos, recorder, *, daily=100.0, category_exposure=None,
              terminal_count=None):
    """Build a MirrorBot bypassing __init__, wired with just what
    _close_position_terminal touches."""
    bot = MirrorBot.__new__(MirrorBot)
    bot._open_positions = OrderedDict()
    if pos is not None:
        bot._open_positions["MKT:TOK"] = pos
    bot._slippage_fail_count = {}
    bot._slippage_backoff = {}
    bot._terminal_reject_count = dict(terminal_count or {})
    bot._exposure_lock = asyncio.Lock()
    bot._daily_exposure = daily
    bot._category_exposure = dict(category_exposure or {})
    bot.base_engine = _FakeEngine(recorder)
    return bot


# ─────────────────────────────────────────────────────────────────────────
class TestTerminalExitPatterns:
    """The pattern set is the detection contract — wrong contents either miss
    real terminal markets (bot stays frozen) or close live positions wrongly."""

    def test_constant_exists(self):
        assert hasattr(mb_mod, "MIRROR_TERMINAL_EXIT_PATTERNS"), (
            "MIRROR_TERMINAL_EXIT_PATTERNS missing — Bug 21 detection contract gone."
        )

    def test_resolved_pattern_present(self):
        # Resolved markets pin the token to 0.0/1.0 → CLOB "invalid price (X.0)".
        assert "invalid price" in mb_mod.MIRROR_TERMINAL_EXIT_PATTERNS

    def test_delisted_pattern_present(self):
        # Delisted: "the orderbook <token_id> does not exist".
        assert "does not exist" in mb_mod.MIRROR_TERMINAL_EXIT_PATTERNS

    def test_balance_not_treated_as_terminal(self):
        # "insufficient" balance is NOT terminal — that path is _live_sell_balance_guard's
        # and may be a transient on-chain sync gap. Closing on it would orphan a
        # live holding the wallet still owns.
        for p in mb_mod.MIRROR_TERMINAL_EXIT_PATTERNS:
            assert "insufficient" not in p, (
                "An 'insufficient'-balance pattern would let a transient balance "
                "sync gap orphan a live position."
            )

    def test_bare_invalid_not_present(self):
        # Must be the specific 'invalid price', NOT bare 'invalid' — otherwise
        # 'invalid signature'/'invalid nonce' (transient) would close positions.
        assert "invalid" not in mb_mod.MIRROR_TERMINAL_EXIT_PATTERNS, (
            "Bare 'invalid' is too broad — would match transient signature/nonce errors."
        )

    def test_confirm_threshold_is_two(self):
        assert hasattr(mb_mod, "_MIRROR_TERMINAL_REJECT_CONFIRM")
        assert mb_mod._MIRROR_TERMINAL_REJECT_CONFIRM == 2, (
            "Confirm threshold must be 2: one transient blip must not close a "
            "live position, but genuinely-terminal markets must clear within ~2 scans."
        )


class TestTerminalDetection:
    """The exact error strings observed on the live bot must classify correctly;
    transient/retryable failures must NOT."""

    @staticmethod
    def _is_terminal(err: str) -> bool:
        # Route through the real classifier so these assertions track production.
        return MirrorBot._classify_exit_failure(err, "") == "terminal"

    def test_invalid_price_one_is_terminal(self):
        # 0x9bce19 — resolved YES winner (raw CLOB permanent-reject result)
        assert self._is_terminal(
            "invalid price (1.0), min: 0.001 - max: 0.999")

    def test_invalid_price_zero_is_terminal(self):
        # 0x13c91c7a — resolved NO loser
        assert self._is_terminal(
            "invalid price (0.0), min: 0.001 - max: 0.999")

    def test_orderbook_missing_is_terminal(self):
        # 0x78a736 — delisted (wrapped by order_gateway retry-exhaustion prefix)
        assert self._is_terminal(
            "All 3 retries exhausted: the orderbook 44257657 does not exist")

    def test_rate_limit_not_terminal(self):
        assert not self._is_terminal("HTTP 429: rate limit exceeded")

    def test_timeout_not_terminal(self):
        assert not self._is_terminal("request timeout after 5s")

    def test_balance_zero_not_terminal(self):
        # Must defer to _live_sell_balance_guard, never close on this.
        assert not self._is_terminal(
            "not enough balance / allowance: balance: 0")

    def test_empty_error_not_terminal(self):
        assert not self._is_terminal("")


class TestExitFailureClassification:
    """_classify_exit_failure is the pure decision the exit loop depends on:
    terminal → close, slippage → back off, inconclusive → leave streak intact."""

    def test_is_staticmethod(self):
        assert isinstance(
            inspect.getattr_static(MirrorBot, "_classify_exit_failure"), staticmethod)

    def test_invalid_price_is_terminal(self):
        assert MirrorBot._classify_exit_failure("invalid price (1.0)", "") == "terminal"

    def test_orderbook_missing_is_terminal(self):
        assert MirrorBot._classify_exit_failure(
            "the orderbook 44257657 does not exist", "") == "terminal"

    def test_slippage_fail_code_is_slippage(self):
        assert MirrorBot._classify_exit_failure("price moved", "slippage") == "slippage"

    def test_kill_switch_escalated_is_inconclusive(self):
        # CRUX of the deadlock fix: a kill-switch-blocked attempt never reached the
        # CLOB, so it is NOT terminal (don't close) and NOT slippage. The caller
        # must preserve the terminal streak on this verdict.
        assert MirrorBot._classify_exit_failure(
            "Circuit breaker ESCALATED — in-process kill switch active "
            "(consecutive_reopens=48, auto-clear in up to 1800s)", "") == "inconclusive"

    def test_kill_switch_engaged_is_inconclusive(self):
        assert MirrorBot._classify_exit_failure(
            "Kill switch engaged (multi-layer)", "") == "inconclusive"

    def test_cascade_active_is_inconclusive(self):
        assert MirrorBot._classify_exit_failure(
            "Cascade active (order skipped)", "") == "inconclusive"

    def test_terminal_takes_precedence_over_slippage_code(self):
        # A resolved market is dead regardless of any fail_code — close it.
        assert MirrorBot._classify_exit_failure("invalid price (1.0)", "slippage") == "terminal"


class TestClosePositionTerminalHelper:
    """Structural shape of the close helper."""

    def test_helper_exists(self):
        assert hasattr(MirrorBot, "_close_position_terminal")

    def test_helper_is_async(self):
        assert asyncio.iscoroutinefunction(MirrorBot._close_position_terminal)

    def test_helper_marker_present(self):
        src = inspect.getsource(MirrorBot._close_position_terminal)
        assert "Bug 21" in src

    def test_helper_marks_status_closed(self):
        src = inspect.getsource(MirrorBot._close_position_terminal)
        assert "status = 'closed'" in src, (
            "Helper must mark the DB row closed — otherwise it reloads as "
            "status='open' on restart and re-enters the doomed-SELL cycle."
        )

    def test_helper_does_not_call_place_order(self):
        # The whole point: do NOT issue the SELL.
        src = inspect.getsource(MirrorBot._close_position_terminal)
        assert "place_order" not in src

    def test_helper_references_redemption(self):
        src = inspect.getsource(MirrorBot._close_position_terminal)
        assert "mirror_redemption_pending" in src and "mirror_terminal_position_closed" in src


class TestClosePositionTerminalBehavior:
    """End-to-end behavior of the close helper against a fake DB/session."""

    def test_removes_from_open_positions(self):
        pos = {"size": 2.15, "entry_price": 0.47, "category": "sports", "current_price": 1.0}
        rec = []
        bot = _make_bot(pos, rec)
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 2.15, reason="resolved", redeemable=True))
        assert "MKT:TOK" not in bot._open_positions

    def test_decrements_daily_exposure(self):
        pos = {"size": 2.0, "entry_price": 0.50, "category": "sports", "current_price": 1.0}
        rec = []
        bot = _make_bot(pos, rec, daily=100.0)
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 2.0, reason="resolved", redeemable=True))
        # cost = 2.0 * 0.50 = 1.0
        assert abs(bot._daily_exposure - 99.0) < 1e-9

    def test_decrements_category_exposure(self):
        pos = {"size": 2.0, "entry_price": 0.50, "category": "sports", "current_price": 1.0}
        rec = []
        bot = _make_bot(pos, rec, category_exposure={"sports": 10.0})
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 2.0, reason="resolved", redeemable=True))
        assert abs(bot._category_exposure["sports"] - 9.0) < 1e-9

    def test_exposure_never_goes_negative(self):
        pos = {"size": 100.0, "entry_price": 0.50, "category": "sports", "current_price": 0.0}
        rec = []
        bot = _make_bot(pos, rec, daily=5.0, category_exposure={"sports": 2.0})
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 100.0, reason="resolved", redeemable=False))
        assert bot._daily_exposure == 0.0
        assert bot._category_exposure["sports"] == 0.0

    def test_issues_db_close_update(self):
        pos = {"size": 2.0, "entry_price": 0.50, "category": "sports", "current_price": 1.0}
        rec = []
        bot = _make_bot(pos, rec)
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 2.0, reason="resolved", redeemable=True))
        assert any("status = 'closed'" in stmt for stmt, _ in rec), (
            "No UPDATE ... status='closed' was issued — DB row stays open and reloads."
        )

    def test_clears_terminal_reject_counter(self):
        pos = {"size": 2.0, "entry_price": 0.50, "category": "sports", "current_price": 1.0}
        rec = []
        bot = _make_bot(pos, rec, terminal_count={"MKT:TOK": 2})
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 2.0, reason="resolved", redeemable=True))
        assert "MKT:TOK" not in bot._terminal_reject_count

    def test_winner_logs_redemption_pending(self):
        pos = {"size": 2.15, "entry_price": 0.47, "category": "sports", "current_price": 1.0}
        rec = []
        bot = _make_bot(pos, rec)
        with mock.patch.object(mb_mod, "logger") as mlog:
            _run(bot._close_position_terminal(
                "MKT:TOK", pos, "MKT", "TOK", 2.15, reason="resolved", redeemable=True))
        events = [c.args[0] for c in mlog.warning.call_args_list if c.args]
        assert "mirror_redemption_pending" in events, (
            "Resolved WINNER must surface an operator-actionable redemption line "
            "(auto-redemption is ABI-walled)."
        )

    def test_loser_logs_terminal_closed(self):
        pos = {"size": 1.53, "entry_price": 0.65, "category": "sports", "current_price": 0.0}
        rec = []
        bot = _make_bot(pos, rec)
        with mock.patch.object(mb_mod, "logger") as mlog:
            _run(bot._close_position_terminal(
                "MKT:TOK", pos, "MKT", "TOK", 1.53, reason="resolved", redeemable=False))
        events = [c.args[0] for c in mlog.warning.call_args_list if c.args]
        assert "mirror_terminal_position_closed" in events
        assert "mirror_redemption_pending" not in events, (
            "A losing/worthless position must NOT be flagged for redemption."
        )

    def test_db_failure_is_nonfatal(self):
        """A DB error during close must not raise — the in-memory drop already
        happened and raising would crash the exit loop for other positions."""
        pos = {"size": 2.0, "entry_price": 0.50, "category": "sports", "current_price": 1.0}

        class _BoomSession(_FakeSession):
            async def execute(self, stmt, params=None):
                raise RuntimeError("pool exhausted")

        class _BoomDB:
            def get_session(self):
                return _BoomSession([])

        bot = _make_bot(pos, [])
        bot.base_engine = type("E", (), {"db": _BoomDB()})()
        # Should complete without raising.
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 2.0, reason="resolved", redeemable=True))
        assert "MKT:TOK" not in bot._open_positions


class TestExitFailureBranchWiring:
    """The terminal-rejection handling must be wired into the exit loop AND the
    pre-existing slippage path must survive untouched."""

    def test_classifier_called_in_exit_loop(self):
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert "_classify_exit_failure" in src, (
            "Exit loop does not call _classify_exit_failure — Bug 21 fix not wired "
            "in; resolved/delisted SELLs would still retry every scan."
        )

    def test_patterns_used_by_classifier(self):
        src = inspect.getsource(MirrorBot._classify_exit_failure)
        assert "MIRROR_TERMINAL_EXIT_PATTERNS" in src

    def test_close_helper_called_from_exit_loop(self):
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert "_close_position_terminal" in src

    def test_confirm_threshold_gates_close(self):
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert "_MIRROR_TERMINAL_REJECT_CONFIRM" in src and "_terminal_reject_count" in src, (
            "Close must be gated on the consecutive-confirm counter, not fire on "
            "the first rejection."
        )

    def test_slippage_path_preserved(self):
        # Rule 4 (no silent behavior change): the S160 slippage backoff must
        # still exist as an independent branch.
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert 'elif _failure_kind == "slippage":' in src, (
            "Slippage backoff branch was lost — terminal handling must be added "
            "ALONGSIDE it, not replace it."
        )
        assert "mirror_slippage_backoff" in src

    def test_inconclusive_branch_preserves_terminal_streak(self):
        """The trailing (inconclusive) branch must NOT pop _terminal_reject_count —
        otherwise a kill-switch-blocked scan resets progress and terminal cleanup
        deadlocks. This is the difference between the fix working under live CB
        pressure and the bot staying frozen."""
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert '"inconclusive"' in src, "inconclusive branch marker missing"
        # Isolate just the inconclusive branch body: from its marker to the end of
        # the per-position try (the next `except Exception`). Slicing further would
        # wrongly catch the post-loop zero-size cleanup's legitimate pop(_zk).
        branch = src.split('"inconclusive"', 1)[1].split("except Exception", 1)[0]
        assert "_terminal_reject_count.pop" not in branch, (
            "Inconclusive branch pops the terminal streak — an interleaved "
            "kill-switch block would reset progress and deadlock cleanup."
        )

    def test_winner_loser_classified_by_current_price(self):
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert "_redeemable = _cp >= 0.5" in src, (
            "Resolved winner/loser must be split on current_price (~1.0 won / "
            "~0.0 lost) to decide redeemability."
        )


class TestInitialization:
    """_terminal_reject_count must be initialized so the exit path never raises
    AttributeError (the failure mode the S159 slippage-dict comment warns about)."""

    def test_init_creates_terminal_reject_count(self):
        src = inspect.getsource(MirrorBot.__init__)
        assert "self._terminal_reject_count" in src, (
            "_terminal_reject_count not initialized in __init__ — first terminal "
            "rejection would raise AttributeError and crash the exit loop."
        )
