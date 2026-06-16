"""S245 #2: phantom-position strand-loop close.

Bug history:
  - Root #2 of the S245 phantom-positions investigation. When a SELL exit is
    rejected by _live_sell_balance_guard (deposit wallet holds fewer outcome
    tokens than the position size), the exit paths did a bare `continue` /
    `return False` and left the DB row status='open' forever. Each scan
    re-evaluated it, the guard rejected again, repeat — the "strand-loop".
    Under marketable FOK these same phantom exits surface as live CLOB
    `invalid token id` 400s (the 2026-06-16 03:14→03:47 regression).

Fix (bots/mirror_bot.py, MirrorBot only):
  - _confirm_zero_ctf_balance(token_id): narrower companion to the guard.
    Returns True ONLY on a CONFIRMED dust balance (< 0.01 token) — a genuine
    phantom. Fails CLOSED (False) in paper mode, on RPC/exception, on None,
    and on any non-dust (partial) balance, so a transient read failure or a
    real partial holding can never trigger a wrongful terminal close.
  - Both exit dispatch sites (_check_and_execute_exits self-driven loop and
    _execute_mirror_trade RTDS SELL) route a confirmed-zero to the existing,
    tested _close_position_terminal(reason='phantom_zero_balance',
    redeemable=False) before continuing / returning.

Cross-bot blast radius:
  - mirror_bot.py only. No shared module, no signature change. The guard's
    own contract (and its source-inspection tests) is untouched.
  - Paper mode unaffected: _confirm_zero_ctf_balance short-circuits to False.
"""
from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict
from unittest import mock

from bots import mirror_bot as mb_mod
from bots.mirror_bot import MirrorBot


class _CaptureDB:
    """Fake DB for the RESOLUTION-emit hardening tests (mirrors the harness in
    test_bug21_terminal_exit_close.py). The SELECT for (markets.resolution,
    positions.is_paper) returns `meta` via fetchone(); insert_trade_event
    records each call's kwargs into `.emitted`."""

    def __init__(self, meta):
        self._meta = meta  # (resolution, is_paper) tuple, or None for no-row
        self.emitted = []

    def get_session(self):
        meta = self._meta

        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt, params=None):
                class _R:
                    def fetchone(_self):
                        return meta

                return _R()

            async def commit(self):
                pass

        return _S()

    async def insert_trade_event(self, **kwargs):
        self.emitted.append(kwargs)
        return 123


def _terminal_bot(pos, meta):
    """MirrorBot wired with just what _close_position_terminal touches, plus a
    capture DB so the RESOLUTION emit is observable."""
    bot = MirrorBot.__new__(MirrorBot)
    bot._open_positions = OrderedDict({"MKT:TOK": pos})
    bot._slippage_fail_count = {}
    bot._slippage_backoff = {}
    bot._terminal_reject_count = {}
    bot._exposure_lock = asyncio.Lock()
    bot._daily_exposure = 100.0
    bot._category_exposure = {}
    cap = _CaptureDB(meta)
    bot.base_engine = type("E", (), {"db": cap})()
    return bot, cap


def _run(coro):
    """Run a coroutine on a private loop without mutating process-wide loop
    state (mirrors the helper in test_bug21_terminal_exit_close.py)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f


def _async_raise(exc):
    async def _f(*a, **k):
        raise exc
    return _f


def _bare_bot():
    """_confirm_zero_ctf_balance touches no instance state beyond `self`, so a
    bypassed-__init__ instance is sufficient."""
    return MirrorBot.__new__(MirrorBot)


# ─────────────────────────────────────────────────────────────────────────
class TestConfirmZeroCtfBalance:
    """The decision that gates a destructive terminal close. Every non-dust,
    non-confirmed outcome MUST return False (fail closed)."""

    def test_paper_mode_returns_false_without_rpc(self):
        bot = _bare_bot()
        called = {"n": 0}

        async def _spy(*a, **k):
            called["n"] += 1
            return 0.0

        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=True), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance", new=_spy):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is False
        # Must short-circuit before any RPC in paper mode.
        assert called["n"] == 0

    def test_confirmed_zero_returns_true(self):
        bot = _bare_bot()
        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=False), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance",
                        new=_async_return(0.0)):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is True

    def test_dust_below_epsilon_returns_true(self):
        bot = _bare_bot()
        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=False), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance",
                        new=_async_return(0.009)):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is True

    def test_partial_holding_returns_false(self):
        """A partial balance (real sellable tokens) must NOT be terminal-closed."""
        bot = _bare_bot()
        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=False), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance",
                        new=_async_return(4.0)):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is False

    def test_at_epsilon_boundary_returns_false(self):
        """bal == 0.01 is not < 0.01 — a holding exactly at the epsilon is kept."""
        bot = _bare_bot()
        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=False), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance",
                        new=_async_return(0.01)):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is False

    def test_none_balance_returns_false(self):
        """RPC down / config missing → None → fail closed."""
        bot = _bare_bot()
        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=False), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance",
                        new=_async_return(None)):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is False

    def test_exception_returns_false(self):
        """A raising balance check must not crash the exit path and must fail closed."""
        bot = _bare_bot()
        with mock.patch.object(mb_mod, "is_paper_trading_active", return_value=False), \
             mock.patch("base_engine.execution.clob_adapter.check_ctf_balance",
                        new=_async_raise(RuntimeError("rpc boom"))):
            assert _run(bot._confirm_zero_ctf_balance("TOK")) is False


class TestStrandLoopWiring:
    """Both exit dispatch sites must route a confirmed-zero balance to the
    terminal close — otherwise the strand-loop / FOK invalid-token-id regresses."""

    def test_confirm_helper_exists_and_is_async(self):
        assert hasattr(MirrorBot, "_confirm_zero_ctf_balance")
        assert asyncio.iscoroutinefunction(MirrorBot._confirm_zero_ctf_balance)

    def test_self_driven_exit_routes_to_terminal_close(self):
        src = inspect.getsource(MirrorBot._check_and_execute_exits)
        assert "_confirm_zero_ctf_balance" in src, (
            "Self-driven exit loop no longer routes a confirmed-zero balance to "
            "the terminal close — the strand-loop (S245 #2) has regressed."
        )
        assert "phantom_zero_balance" in src
        assert "_close_position_terminal" in src

    def test_rtds_exit_routes_to_terminal_close(self):
        src = inspect.getsource(MirrorBot._execute_mirror_trade)
        assert "_confirm_zero_ctf_balance" in src, (
            "RTDS SELL path no longer routes a confirmed-zero balance to the "
            "terminal close — the strand-loop (S245 #2) has regressed."
        )
        assert "phantom_zero_balance" in src
        assert "_close_position_terminal" in src

    def test_confirm_helper_fails_closed(self):
        """The helper must fail closed: paper mode, None, and exception paths
        all return False so a destructive close never fires on uncertainty."""
        src = inspect.getsource(MirrorBot._confirm_zero_ctf_balance)
        assert "is_paper_trading_active" in src
        assert "is None" in src
        # Dust threshold is the same 0.01 token epsilon the guard uses.
        assert "_PHANTOM_DUST_TOKENS = 0.01" in src

    def test_both_call_sites_pass_known_zero_balance(self):
        """Both phantom-close call sites must pass known_zero_balance=True so the
        RESOLUTION emit can never book a fictional payout-derived P&L."""
        for fn in (MirrorBot._check_and_execute_exits, MirrorBot._execute_mirror_trade):
            src = inspect.getsource(fn)
            assert "known_zero_balance=True" in src, (
                f"{fn.__name__} no longer passes known_zero_balance=True to the "
                "phantom terminal close — a 0-token phantom on a resolved market "
                "could book a fictional realized_pnl."
            )


class TestKnownZeroBalanceForcesNullPnl:
    """S245 #2 hardening: a confirmed-zero (phantom) close holds 0 tokens, so any
    payout-derived P&L is fictional. known_zero_balance=True must force NULL +
    a 'phantom_zero_balance' reconciliation flag regardless of the market's
    resolution/price state — while the default path keeps computing real P&L."""

    def test_resolved_winner_books_null_when_known_zero(self):
        # Held YES, market resolved YES at price 1.0 → the default path would
        # compute a positive winner P&L. known_zero_balance=True must NOT.
        pos = {"size": 10.0, "entry_price": 0.40, "side": "YES",
               "category": "sports", "current_price": 1.0}
        bot, cap = _terminal_bot(pos, ("YES", False))
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 10.0, reason="phantom_zero_balance",
            redeemable=False, known_zero_balance=True))
        assert len(cap.emitted) == 1
        ev = cap.emitted[0]
        assert ev["event_type"] == "RESOLUTION"
        assert ev["realized_pnl"] is None, (
            "0-token phantom on a resolved market must book NULL P&L, not a "
            "fictional payout-derived number."
        )
        assert ev["event_data"].get("reconciliation") == "phantom_zero_balance"
        assert "MKT:TOK" not in bot._open_positions  # still closed

    def test_price_pinned_books_null_when_known_zero(self):
        # WI-15 race: DB resolution NULL but current_price pinned at 1.0. Default
        # path would resolve a payout; known_zero_balance=True forces NULL.
        pos = {"size": 4.0, "entry_price": 0.30, "side": "YES",
               "category": "sports", "current_price": 1.0}
        bot, cap = _terminal_bot(pos, (None, False))
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 4.0, reason="phantom_zero_balance",
            redeemable=False, known_zero_balance=True))
        ev = cap.emitted[0]
        assert ev["realized_pnl"] is None
        assert ev["event_data"].get("reconciliation") == "phantom_zero_balance"

    def test_default_path_still_computes_real_pnl(self):
        # Regression: with known_zero_balance defaulted (False), a real resolved
        # winner must STILL compute its P&L — the hardening must not touch the
        # WI-6 / Bug-21 token-backed close path.
        pos = {"size": 10.0, "entry_price": 0.40, "side": "YES",
               "category": "sports", "current_price": 1.0}
        bot, cap = _terminal_bot(pos, ("YES", False))
        _run(bot._close_position_terminal(
            "MKT:TOK", pos, "MKT", "TOK", 10.0, reason="resolved", redeemable=True))
        ev = cap.emitted[0]
        assert ev["realized_pnl"] is not None, (
            "Default path regressed — real resolved winners must still compute "
            "P&L; the hardening must be opt-in via known_zero_balance."
        )
        from config.settings import settings as _s
        _fee = float(getattr(_s, "TAKER_FEE_BPS", 150)) / 10000.0
        expected = round((1.0 - 0.40) * 10.0 - 1.0 * 10.0 * _fee, 4)
        assert abs(ev["realized_pnl"] - expected) < 1e-9
