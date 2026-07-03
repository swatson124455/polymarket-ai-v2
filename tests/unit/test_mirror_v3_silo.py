"""mirror_v3 silo scaffold tests — env guard, 4-scope guards, ordering-safe restore."""
from __future__ import annotations

import asyncio

import pytest

from mirror_v3.env_guard import (
    LIVE_ACK_KEY, LIVE_ACK_PHRASE, EnvGuardError, assert_safe_env,
)
from mirror_v3.guards import BlockReason, OneBetPerMarketGuards, Side
from mirror_v3.state_restore import restore_state


SAFE_ENV = {
    "SIMULATION_MODE": "true",
    "CANARY_AUTO_ADVANCE": "false",
    "CANARY_STAGE": "0",
    "DATABASE_URL": "postgresql+asyncpg://x",
}


class TestEnvGuard:
    def test_safe_paper_env_passes(self):
        assert assert_safe_env(SAFE_ENV)["mode"] == "paper"

    def test_missing_key_is_boot_error_not_default(self):
        for k in SAFE_ENV:
            env = {kk: v for kk, v in SAFE_ENV.items() if kk != k}
            with pytest.raises(EnvGuardError, match="missing"):
                assert_safe_env(env)

    def test_empty_value_counts_as_missing(self):
        env = dict(SAFE_ENV, CANARY_AUTO_ADVANCE="  ")
        with pytest.raises(EnvGuardError, match="missing"):
            assert_safe_env(env)

    def test_live_without_ack_refused(self):
        env = dict(SAFE_ENV, SIMULATION_MODE="false")
        with pytest.raises(EnvGuardError, match="LIVE"):
            assert_safe_env(env)

    def test_live_with_exact_ack_allowed(self):
        env = dict(SAFE_ENV, SIMULATION_MODE="false")
        env[LIVE_ACK_KEY] = LIVE_ACK_PHRASE
        assert assert_safe_env(env)["mode"] == "LIVE"

    def test_live_with_wrong_ack_refused(self):
        env = dict(SAFE_ENV, SIMULATION_MODE="false")
        env[LIVE_ACK_KEY] = "yes"
        with pytest.raises(EnvGuardError):
            assert_safe_env(env)

    def test_auto_advance_true_always_refused(self):
        env = dict(SAFE_ENV, CANARY_AUTO_ADVANCE="true")
        with pytest.raises(EnvGuardError, match="auto"):
            assert_safe_env(env)

    def test_nonzero_canary_stage_in_paper_refused(self):
        env = dict(SAFE_ENV, CANARY_STAGE="4")
        with pytest.raises(EnvGuardError, match="CANARY_STAGE"):
            assert_safe_env(env)

    def test_all_violations_reported_together(self):
        env = dict(SAFE_ENV, CANARY_AUTO_ADVANCE="true", CANARY_STAGE="4")
        with pytest.raises(EnvGuardError) as ei:
            assert_safe_env(env)
        msg = str(ei.value)
        assert "auto" in msg and "CANARY_STAGE" in msg


class TestGuards:
    def test_fail_closed_before_restore(self):
        g = OneBetPerMarketGuards()
        ok, reason = g.can_enter("0xabc", "YES")
        assert not ok and reason is BlockReason.NOT_RESTORED

    def test_record_entry_before_restore_raises(self):
        g = OneBetPerMarketGuards()
        with pytest.raises(RuntimeError, match="fail-closed"):
            g.record_entry("0xabc", "YES")

    def _restored(self) -> OneBetPerMarketGuards:
        g = OneBetPerMarketGuards()
        g.mark_restored()
        return g

    def test_fresh_market_allowed(self):
        g = self._restored()
        assert g.can_enter("0xabc", "YES") == (True, None)

    def test_scope1_open_same_side(self):
        g = self._restored()
        g.record_entry("0xabc", "YES")
        assert g.can_enter("0xabc", "YES") == (False, BlockReason.OPEN_SAME_SIDE)

    def test_scope2_open_opposing(self):
        g = self._restored()
        g.record_entry("0xabc", "YES")
        assert g.can_enter("0xabc", "NO") == (False, BlockReason.OPEN_OPPOSING)

    def test_scope3_historical_same_after_close(self):
        g = self._restored()
        g.record_entry("0xabc", "YES")
        g.record_close("0xabc")
        assert g.can_enter("0xabc", "YES") == (False, BlockReason.HISTORICAL_SAME)

    def test_scope4_historical_opposing_after_close(self):
        g = self._restored()
        g.record_entry("0xabc", "YES")
        g.record_close("0xabc")
        assert g.can_enter("0xabc", "NO") == (False, BlockReason.HISTORICAL_OPPOSING)

    def test_one_bet_per_condition_id_net_effect(self):
        """The CLAUDE.md constraint: at most one bet per condition_id, ever."""
        g = self._restored()
        g.record_entry("0xabc", "NO")
        g.record_close("0xabc")
        for side in ("YES", "NO"):
            ok, _ = g.can_enter("0xabc", side)
            assert not ok
        # sibling condition_ids stay independent (neg-risk siblings unguarded
        # by design — per-event caps belong in sizing, not gating)
        assert g.can_enter("0xdef", "YES") == (True, None)

    def test_loaders_locked_after_restore(self):
        g = self._restored()
        with pytest.raises(RuntimeError, match="one-shot"):
            g.load_entered("0xabc", "YES")


class TestRestoreOrdering:
    """The v2 bug class: no window where half-restored guards approve entries."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_full_success_opens_guards(self):
        g = OneBetPerMarketGuards()

        async def entered():
            return [("0xaaa", "YES"), ("0xbbb", "NO")]

        async def open_pos():
            return [("0xccc", "YES")]

        async def exposure():
            return 12.5

        r = self._run(restore_state(g, entered, open_pos, exposure))
        assert r.restored and g.restored
        assert r.entered_loaded == 2 and r.open_loaded == 1
        assert r.daily_exposure_usd == pytest.approx(12.5)
        # restored state is live in the guards
        assert g.can_enter("0xaaa", "NO") == (False, BlockReason.HISTORICAL_OPPOSING)
        assert g.can_enter("0xccc", "YES") == (False, BlockReason.OPEN_SAME_SIDE)

    def test_entered_loader_failure_stays_closed(self):
        g = OneBetPerMarketGuards()

        async def entered():
            raise ConnectionError("db down")

        async def open_pos():
            return []

        async def exposure():
            return 0.0

        r = self._run(restore_state(g, entered, open_pos, exposure))
        assert not r.restored and not g.restored and "db down" in r.error
        assert g.can_enter("0xany", "YES") == (False, BlockReason.NOT_RESTORED)

    def test_LAST_loader_failure_stays_closed(self):
        """The exact v2 hole: earlier parts succeeded, a later part failed —
        v2 had already flipped the flag; v3 must still be closed."""
        g = OneBetPerMarketGuards()

        async def entered():
            return [("0xaaa", "YES")]

        async def open_pos():
            return [("0xbbb", "NO")]

        async def exposure():
            raise TimeoutError("late failure")

        r = self._run(restore_state(g, entered, open_pos, exposure))
        assert not r.restored and not g.restored
        assert r.entered_loaded == 1 and r.open_loaded == 1  # partial load happened
        assert g.can_enter("0xfresh", "YES") == (False, BlockReason.NOT_RESTORED)


class TestRunnerBootOrder:
    def test_main_refuses_poisoned_env_before_any_db_work(self, monkeypatch):
        """env guard must fire before Database is even importable-touched."""
        import mirror_v3.run as run_mod
        for k in SAFE_ENV:
            monkeypatch.delenv(k, raising=False)
        from mirror_v3.env_guard import EnvGuardError as EGE
        with pytest.raises(EGE):
            asyncio.run(run_mod.main())
