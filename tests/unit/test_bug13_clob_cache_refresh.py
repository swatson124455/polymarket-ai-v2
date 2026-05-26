"""S230 Bug 13: CLOB balance/allowance cache refresh on first live trade.

Bug history:
  - Polymarket's matching engine caches per-funder balance/allowance state
    internally. After operator-side deposits/conversions/redemptions, that
    cache lags actual on-chain pUSD state.
  - S230 smoke test (2026-05-26): 3 live BUYs rejected with
    "not enough balance / allowance: ... balance: 0" despite the deposit
    wallet holding $23.14993 pUSD on-chain (verified via check_pusd_balance
    helper + direct RPC). All V2 spender allowances were MAX.
  - Manual call to clob.update_balance_allowance(COLLATERAL) refreshed the
    cache and CLOB then reported correct $23.14993 balance.

Fix shape:
  - clob_adapter.py: add _refresh_balance_allowance_sync() helper + async
    ClobAdapter.refresh_balance_allowance() method wrapping run_in_executor.
  - mirror_bot.py __init__: sentinel self._clob_cache_refreshed = False
  - mirror_bot.py: helper _refresh_clob_cache_once_if_live() — calls the
    adapter once per session, skips paper mode, marks flag regardless of
    outcome (non-fatal). Called from both _check_and_execute_exits (exit
    path) and _execute_mirror_trade (entry path) before any place_order.

Cross-bot blast radius:
  - clob_adapter.py: additive. New function + new ClobAdapter method.
    Other bots calling ClobAdapter unaffected unless they opt in by
    calling the new method.
  - mirror_bot.py: MirrorBot only.
  - Paper mode: no CLOB calls. Helper sets the flag and returns without
    contacting Polymarket.
  - Performance: one /balance-allowance/update HTTP call per session on
    first live trade attempt. Idempotent thereafter.

Tests are source-grep / structural — mirrors test_bug11c_sell_balance_guard.py
and test_bug12_mode_flip_guard.py patterns. Functional integration tests
would require a full MirrorBot bootstrap with DB + base_engine, out of
scope for unit file.
"""
from __future__ import annotations

import inspect

from base_engine.execution import clob_adapter as ca_mod
from bots import mirror_bot as mb_mod


class TestBug13ClobAdapterRefreshHelper:
    """clob_adapter.py: refresh_balance_allowance helpers exist."""

    def test_sync_helper_exists(self):
        assert hasattr(ca_mod, "_refresh_balance_allowance_sync"), (
            "clob_adapter._refresh_balance_allowance_sync missing — without "
            "this, the bot cannot force CLOB to refresh its cached balance/"
            "allowance state, and first BUY post-restart will hit "
            "'balance: 0' (S230 smoke test surfaced this)."
        )

    def test_async_method_exists_on_adapter(self):
        assert hasattr(ca_mod.ClobAdapter, "refresh_balance_allowance"), (
            "ClobAdapter.refresh_balance_allowance async method missing — "
            "without it, bots can't easily refresh CLOB cache from async "
            "code paths (which is where MirrorBot lives)."
        )

    def test_async_method_calls_sync_in_executor(self):
        src = inspect.getsource(ca_mod.ClobAdapter.refresh_balance_allowance)
        assert "run_in_executor" in src, (
            "refresh_balance_allowance must wrap the sync helper via "
            "run_in_executor — py-clob-client-v2 is sync. Without the "
            "executor wrap, the event loop blocks on HTTP I/O."
        )
        assert "_refresh_balance_allowance_sync" in src

    def test_sync_helper_handles_failure_nonfatal(self):
        src = inspect.getsource(ca_mod._refresh_balance_allowance_sync)
        assert "except" in src, (
            "Refresh failures must NOT raise — they're non-fatal. The bot "
            "should proceed even if Polymarket's /balance-allowance/update "
            "endpoint is unreachable. Failure is logged but absorbed."
        )
        assert "return False" in src, (
            "Sync helper must return False on failure for caller to log "
            "without raising."
        )


class TestBug13MirrorBotInit:
    """__init__ initializes the cache-refreshed sentinel."""

    def test_clob_cache_refreshed_attr_in_init(self):
        src = inspect.getsource(mb_mod.MirrorBot.__init__)
        assert "_clob_cache_refreshed" in src, (
            "MirrorBot.__init__ must initialize self._clob_cache_refreshed. "
            "Without the attribute, the helper will AttributeError on first "
            "trade attempt."
        )
        assert "_clob_cache_refreshed: bool = False" in src or \
               "_clob_cache_refreshed = False" in src, (
            "Init must default to False — refresh hasn't happened yet."
        )


class TestBug13RefreshHelperOnMirrorBot:
    """_refresh_clob_cache_once_if_live helper exists with correct shape."""

    def test_helper_exists(self):
        assert hasattr(mb_mod.MirrorBot, "_refresh_clob_cache_once_if_live"), (
            "MirrorBot._refresh_clob_cache_once_if_live missing — without "
            "this, exit and entry paths cannot invoke the cache refresh."
        )

    def test_helper_is_idempotent(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        assert "if self._clob_cache_refreshed:" in src, (
            "Helper must early-return when already refreshed — idempotent "
            "to avoid spamming the /balance-allowance/update endpoint."
        )

    def test_helper_skips_paper_mode(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        assert "is_paper_trading_active()" in src, (
            "Helper must check is_paper_trading_active() — paper mode "
            "doesn't use CLOB, so refresh is meaningless."
        )

    def test_helper_marks_flag_regardless(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        # The finally block (or equivalent) must set _clob_cache_refreshed = True
        # so we don't retry forever if Polymarket is down.
        assert "finally:" in src or src.count("self._clob_cache_refreshed = True") >= 1, (
            "Helper must mark _clob_cache_refreshed=True regardless of "
            "outcome (success, failure, exception). Otherwise a CLOB outage "
            "causes the bot to retry the refresh every scan cycle."
        )

    def test_helper_uses_collateral_asset_type(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        assert "COLLATERAL" in src, (
            "Helper must refresh asset_type=COLLATERAL — that's the pUSD-side "
            "cache. CONDITIONAL would refresh CTF token allowance, which is "
            "a different concern (Bug 11C epsilon already handles SELL-side)."
        )


class TestBug13WiredIntoExitPath:
    """_check_and_execute_exits calls the refresh helper before place_order."""

    def test_exit_path_calls_refresh(self):
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        assert "_refresh_clob_cache_once_if_live" in src, (
            "_check_and_execute_exits must call _refresh_clob_cache_once_if_live "
            "before any place_order. Without this, the first live exit attempt "
            "(typical post-restart sequence) hits stale-cache 'balance: 0' on "
            "the SELL side too."
        )

    def test_exit_refresh_before_bug12_guard(self):
        # Stale-cache refresh should be the FIRST gate so we don't waste time
        # evaluating modes/guards if the CLOB session is unreachable.
        src = inspect.getsource(mb_mod.MirrorBot._check_and_execute_exits)
        bug13_idx = src.find("_refresh_clob_cache_once_if_live")
        bug12_idx = src.find("bug12_mode_flip_detected_exits_skipped")
        assert bug13_idx != -1 and bug12_idx != -1, "Both guards must be present"
        assert bug13_idx < bug12_idx, (
            "Bug 13 cache refresh should fire BEFORE Bug 12 mode-flip guard. "
            "Cheap I/O check first; flag-comparison second."
        )


class TestBug13WiredIntoEntryPath:
    """_execute_mirror_trade calls the refresh helper before gate evaluation."""

    def test_entry_path_calls_refresh(self):
        src = inspect.getsource(mb_mod.MirrorBot._execute_mirror_trade)
        assert "_refresh_clob_cache_once_if_live" in src, (
            "_execute_mirror_trade must call _refresh_clob_cache_once_if_live "
            "before any place_order. Without this, the first live entry "
            "attempt hits stale-cache 'balance: 0' (S230 smoke test "
            "surfaced this — 3 of 3 BUYs failed for this reason)."
        )
