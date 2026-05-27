"""S230 Bug 13 + Bug 15: CLOB balance/allowance cache refresh.

Bug history:
  - Bug 13 (2026-05-26): Polymarket's matching engine caches per-funder
    balance/allowance state. After operator-side deposits/conversions/
    redemptions, that cache lags actual on-chain pUSD state. S230 smoke
    test: 3 live BUYs rejected with "balance: 0" despite the deposit wallet
    holding $23.14993 pUSD on-chain. Manual call to update_balance_allowance
    refreshed the cache. Bug 13 added a once-per-session refresh on first
    live trade attempt.
  - Bug 15 (2026-05-27 live re-flip): once-per-session model broke in the
    field. Refresh fired on an early-rejected signal at 01:05:24; cache
    went stale by the time the first real order placement attempted at
    01:11:41 (6m 17s later). Every subsequent live order in the session
    rejected with "balance: 0" because the once-flag had locked further
    refresh attempts out. Fix: replace bool flag with monotonic timestamp
    + _CLOB_CACHE_TTL_SECONDS gate. Refresh re-fires every TTL seconds.

Fix shape (current):
  - clob_adapter.py: _refresh_balance_allowance_sync() + async
    ClobAdapter.refresh_balance_allowance() — unchanged from Bug 13.
  - mirror_bot.py module-level: _CLOB_CACHE_TTL_SECONDS = 30.0
  - mirror_bot.py __init__: self._clob_cache_refreshed_at: Optional[float] = None
  - mirror_bot.py _refresh_clob_cache_once_if_live: paper no-ops; live
    refreshes only if last refresh was >TTL ago. Timestamp updated BEFORE
    I/O to prevent concurrent re-entry from multi-firing. Updated even on
    failure to cap retry rate at ~2 attempts/min worst-case.
  - Method name retained ("_refresh_clob_cache_once_if_live") despite
    "once" being a misnomer — per CLAUDE.md Rule 2, preserve signatures to
    minimize blast radius. Both call sites unchanged.

Cross-bot blast radius:
  - clob_adapter.py: unchanged from Bug 13.
  - mirror_bot.py: MirrorBot only.
  - Paper mode: no CLOB calls. Helper returns immediately.
  - Performance: at most one /balance-allowance/update HTTP call per
    _CLOB_CACHE_TTL_SECONDS (30s) per live MirrorBot instance.

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
    """__init__ initializes the cache-refresh timestamp (Bug 15 update)."""

    def test_clob_cache_refreshed_at_attr_in_init(self):
        src = inspect.getsource(mb_mod.MirrorBot.__init__)
        assert "_clob_cache_refreshed_at" in src, (
            "MirrorBot.__init__ must initialize self._clob_cache_refreshed_at "
            "(Bug 15 monotonic-timestamp). Without it the helper will "
            "AttributeError on first trade attempt."
        )
        assert "_clob_cache_refreshed_at: Optional[float] = None" in src or \
               "_clob_cache_refreshed_at = None" in src, (
            "Init must default to None — no refresh has happened yet, and "
            "the helper compares against None to detect first-fire."
        )

    def test_old_bool_flag_removed(self):
        """The Bug 13 bool flag must be removed so it can't shadow the
        timestamp during code review. If both attributes exist, future
        readers will be confused about which is authoritative."""
        src = inspect.getsource(mb_mod.MirrorBot.__init__)
        assert "_clob_cache_refreshed: bool = False" not in src, (
            "Bug 13's bool flag must be removed in favor of the Bug 15 "
            "timestamp. Having both is ambiguous and risks dead-state bugs."
        )


class TestBug15CacheTtlConstant:
    """Module-level TTL constant exists and is sane."""

    def test_ttl_constant_defined(self):
        assert hasattr(mb_mod, "_CLOB_CACHE_TTL_SECONDS"), (
            "mirror_bot._CLOB_CACHE_TTL_SECONDS missing — refresh helper "
            "needs a TTL to gate re-fires."
        )

    def test_ttl_constant_is_positive_and_under_5min(self):
        ttl = mb_mod._CLOB_CACHE_TTL_SECONDS
        assert isinstance(ttl, (int, float)) and ttl > 0, (
            f"_CLOB_CACHE_TTL_SECONDS must be positive number, got {ttl!r}"
        )
        # Polymarket cache observed stale within ~6 min; TTL must be well
        # under that to keep cache warm before the next order attempts.
        assert ttl <= 300, (
            f"_CLOB_CACHE_TTL_SECONDS={ttl} exceeds 5 min — Polymarket "
            "cache went stale within 6 min in the S230 live re-flip "
            "incident. Refresh must fire more often than that."
        )


class TestBug13RefreshHelperOnMirrorBot:
    """_refresh_clob_cache_once_if_live helper exists with TTL gating."""

    def test_helper_exists(self):
        assert hasattr(mb_mod.MirrorBot, "_refresh_clob_cache_once_if_live"), (
            "MirrorBot._refresh_clob_cache_once_if_live missing — without "
            "this, exit and entry paths cannot invoke the cache refresh. "
            "Method name retained from Bug 13 for grep continuity (Rule 2)."
        )

    def test_helper_gates_by_ttl(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        assert "_CLOB_CACHE_TTL_SECONDS" in src, (
            "Helper must gate on _CLOB_CACHE_TTL_SECONDS — not on a "
            "once-per-session bool. Bug 15: once-flag fired on early-rejected "
            "signal, leaving real first order with stale cache."
        )
        assert "_clob_cache_refreshed_at" in src, (
            "Helper must read/write self._clob_cache_refreshed_at to track "
            "last refresh time."
        )

    def test_helper_uses_monotonic_clock(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        assert "_time.monotonic()" in src, (
            "Helper must use _time.monotonic() — wall clock can jump "
            "backwards (NTP adjustments) and would cause incorrect TTL "
            "gating. monotonic is the safe choice for elapsed-time checks."
        )

    def test_helper_skips_paper_mode(self):
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        assert "is_paper_trading_active()" in src, (
            "Helper must check is_paper_trading_active() — paper mode "
            "doesn't use CLOB, so refresh is meaningless."
        )

    def test_helper_updates_timestamp_before_io(self):
        """Setting the timestamp BEFORE the I/O call prevents concurrent
        callers (race) from all firing the refresh at the same time. Also
        ensures a failed refresh doesn't trigger a tight-loop retry storm
        at signal rate (~2.6/sec at S230 peak)."""
        src = inspect.getsource(mb_mod.MirrorBot._refresh_clob_cache_once_if_live)
        ts_assign_idx = src.find("self._clob_cache_refreshed_at = now")
        # Match the actual call site, not the docstring/comment mentions
        io_call_idx = src.find("adapter.refresh_balance_allowance(")
        assert ts_assign_idx != -1, "Timestamp must be assigned in helper"
        assert io_call_idx != -1, "I/O call must be in helper"
        assert ts_assign_idx < io_call_idx, (
            "Timestamp assignment must come BEFORE the I/O call — guards "
            "against concurrent re-entry AND tight-loop hammering on "
            "endpoint failure."
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
