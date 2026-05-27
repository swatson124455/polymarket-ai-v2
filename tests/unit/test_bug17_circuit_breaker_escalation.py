"""S230 Bug 17: CircuitBreaker → in-process kill-switch escalation.

Bug history:
  - 2026-05-27 S230 live re-flip: MB live for ~78 min, 23 order events,
    10 unique markets, 0 fills, 13+ consecutive CircuitBreaker re-opens.
    No automated rollback fired. Operator manually flipped back to paper.
  - Diagnosis: the existing CircuitBreaker throttles failed CLOB calls
    (open → 60s cooldown → half-open probe → re-open on failure), but its
    escalation ladder terminates at "throttle forever." There is no
    transition from "stuck open" to "engage kill switch." Loss-based
    triggers in risk_manager.py can't fire with $0 fills. Canary
    auto-transition needs ≥50 resolved trades. consecutive_failures in
    base_bot.py only counts scan-loop exceptions, not order rejections.
  - Result: any failure mode causing 100% order rejection without
    exceptions would also slip past every safety net. Bug 16-shaped
    bugs (side translation, wallet drift, contract changes) would all
    leave the bot scanning + retrying indefinitely.

Fix shape:
  - CircuitBreaker tracks consecutive_reopens (HALF_OPEN → OPEN transitions
    since last record_success).
  - When consecutive_reopens >= escalation_threshold (default 10 — about
    10 minutes of pure failure at 60s cooldown), engage in-process kill
    switch: self.escalated = True with timestamp.
  - allow_request() returns False while escalated, until
    escalation_cooldown_seconds (default 1800s = 30 min) has elapsed.
  - Auto-clear after cooldown: self.escalated resets, normal state machine
    resumes. If the underlying issue persists, next probe re-escalates.
  - record_success() resets consecutive_reopens but does NOT auto-clear
    escalation — single-noise-success can't unwedge the bot.
  - ExecutionEngine.place_order differentiates the error message:
    "ESCALATED — in-process kill switch active" vs "OPEN — CLOB API
    temporarily unavailable".
  - CRITICAL log on engagement with consecutive_reopens, failure_count,
    cooldown_seconds context.

Operator-required tests (per S230 directive):
  - "Test the kill switch actually halts order placement... Paper-mode test:
    manually trigger the kill switch via the in-memory flag, send a
    synthetic order placement call, confirm it's blocked. Without that test,
    you have a safety net that might have its own wiring gap (the lesson
    of Bug 7 all over again)."

Cross-bot blast radius:
  - CircuitBreaker is per-ExecutionEngine; ExecutionEngine is per-BaseEngine;
    each bot (MirrorBot, EsportsBot, WeatherBot) runs in its own systemd
    service with its own Python process. Per-service isolation is
    automatic — escalation in MB cannot block EB/WB orders.
  - Existing CircuitBreaker constructor signature unchanged (new params
    have defaults). Existing callers in base_engine.py, execution_engine.py
    not impacted.

Cost: zero on healthy operation. Constant-time guard on every
allow_request(). One CRITICAL log per escalation event.
"""
from __future__ import annotations

import inspect
import time
import unittest.mock as _mock

import pytest

from base_engine.execution import execution_engine as ee_mod
from base_engine.execution.execution_engine import CircuitBreaker


class TestBug17CircuitBreakerInit:
    """New escalation fields exist with sane defaults."""

    def test_escalation_threshold_default(self):
        cb = CircuitBreaker()
        assert cb.escalation_threshold == 10, (
            "Default escalation_threshold should be 10 — about 10 minutes "
            "of pure failure at the 60s cooldown, conservative enough to "
            "avoid false-positive on a 5-min Polymarket hiccup."
        )

    def test_escalation_cooldown_default(self):
        cb = CircuitBreaker()
        assert cb.escalation_cooldown_seconds == 1800.0, (
            "Default escalation_cooldown_seconds should be 1800 (30 min). "
            "Long enough that a transient outage clears on its own; short "
            "enough that operator inattention doesn't strand the bot in "
            "blocked state for days."
        )

    def test_escalation_state_initial(self):
        cb = CircuitBreaker()
        assert cb.escalated is False
        assert cb.escalated_at == 0.0
        assert cb.consecutive_reopens == 0


class TestBug17ConsecutiveReopens:
    """consecutive_reopens tracks HALF_OPEN → OPEN transitions only."""

    def test_failure_from_closed_does_not_count_as_reopen(self):
        """The first failure_threshold failures take CLOSED → OPEN. That's
        not a "re-open" — it's the initial open. consecutive_reopens should
        only count probe failures."""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.consecutive_reopens == 0, (
            "Initial CLOSED → OPEN must not count as a re-open. "
            "consecutive_reopens counts HALF_OPEN → OPEN transitions."
        )

    def test_failure_from_half_open_increments_reopen(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0)
        # Take CLOSED → OPEN
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        # Force HALF_OPEN by letting allow_request flip the state
        assert cb.allow_request() is True  # cooldown=0 → immediate half-open
        assert cb.state == CircuitBreaker.HALF_OPEN
        # Probe fails → HALF_OPEN → OPEN, increments consecutive_reopens
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.consecutive_reopens == 1

    def test_success_resets_consecutive_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0)
        for _ in range(2):
            cb.record_failure()
        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()
        assert cb.consecutive_reopens == 1
        cb.record_success()
        assert cb.consecutive_reopens == 0


class TestBug17EscalationEngagement:
    """Engagement fires at the threshold and changes behavior."""

    def _force_reopen(self, cb: CircuitBreaker) -> None:
        """Take cb from CLOSED → OPEN → HALF_OPEN → OPEN (one re-open)."""
        # If already in OPEN, just probe and re-fail
        if cb.state == CircuitBreaker.OPEN:
            cb.allow_request()  # → HALF_OPEN (cooldown=0)
            cb.record_failure()
            return
        # CLOSED: fail enough to open, then re-open via probe
        for _ in range(cb.failure_threshold):
            cb.record_failure()
        cb.allow_request()
        cb.record_failure()

    def test_engagement_fires_at_threshold(self):
        cb = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0,
            escalation_threshold=3,
        )
        # Open the breaker first
        cb.record_failure()
        assert cb.consecutive_reopens == 0
        # Three re-opens → engage
        for i in range(3):
            cb.allow_request()  # HALF_OPEN
            cb.record_failure()  # → OPEN, increments reopens
            if i < 2:
                assert cb.escalated is False, f"escalated too early at reopen {i+1}"
        assert cb.consecutive_reopens == 3
        assert cb.escalated is True
        assert cb.escalated_at > 0

    def test_blocked_while_escalated_within_cooldown(self):
        """Operator-required: in-memory flag blocks allow_request()."""
        cb = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0,
            escalation_threshold=1, escalation_cooldown_seconds=600,
        )
        cb.record_failure()  # CLOSED → OPEN
        cb.allow_request()    # → HALF_OPEN
        cb.record_failure()  # → OPEN, reopens=1 → engage
        assert cb.escalated is True
        # While escalated and within cooldown, allow_request is False
        assert cb.allow_request() is False

    def test_engagement_does_not_re_fire_if_already_escalated(self):
        """A second threshold breach shouldn't re-log CRITICAL / reset
        timestamp — that would extend the cooldown indefinitely."""
        cb = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0,
            escalation_threshold=1, escalation_cooldown_seconds=600,
        )
        cb.record_failure()
        cb.allow_request()
        cb.record_failure()
        first_engaged_at = cb.escalated_at
        # Force allow_request to be called and return False (escalated)
        cb.allow_request()
        # The bot can't issue more record_failure since allow_request blocks
        # all callers — but defensively, simulate someone calling it anyway
        cb.record_failure()
        assert cb.escalated is True
        assert cb.escalated_at == first_engaged_at, (
            "Re-engagement must not bump escalated_at — that'd extend "
            "cooldown forever and defeat the auto-clear design."
        )


class TestBug17AutoClear:
    """After cooldown, escalation auto-clears and normal state resumes."""

    def test_auto_clears_after_cooldown(self, monkeypatch):
        """Use monkeypatch on time.monotonic to fast-forward."""
        clock = [1000.0]

        def fake_mono():
            return clock[0]

        monkeypatch.setattr(ee_mod.time, "monotonic", fake_mono)

        cb = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0,
            escalation_threshold=1, escalation_cooldown_seconds=60,
        )
        cb.record_failure()
        cb.allow_request()
        cb.record_failure()
        assert cb.escalated is True

        # Within cooldown
        clock[0] += 30
        assert cb.allow_request() is False
        assert cb.escalated is True

        # Past cooldown → auto-clear on next allow_request call
        clock[0] += 31  # now 61s past engagement
        cb.allow_request()
        assert cb.escalated is False, "Cooldown elapsed → must auto-clear"

    def test_success_does_not_auto_clear_escalation(self):
        """One success is noise. Cooldown is the only auto-clear path."""
        cb = CircuitBreaker(
            failure_threshold=1, cooldown_seconds=0,
            escalation_threshold=1, escalation_cooldown_seconds=600,
        )
        cb.record_failure()
        cb.allow_request()
        cb.record_failure()
        assert cb.escalated is True
        # Simulate a successful call (somehow leaks through, or test of
        # post-cooldown-but-no-block path)
        cb.record_success()
        assert cb.escalated is True, (
            "record_success() must NOT auto-clear escalated — a noise-success "
            "shouldn't unwedge a bot pre-cooldown."
        )
        # consecutive_reopens should reset though
        assert cb.consecutive_reopens == 0


class TestBug17BackwardCompat:
    """Pre-Bug-17 CircuitBreaker behavior unchanged when escalation never fires."""

    def test_existing_constructor_signature_works(self):
        """Old callers using only failure_threshold + cooldown_seconds must
        continue to work without code changes."""
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60.0)
        assert cb.failure_threshold == 5
        assert cb.cooldown_seconds == 60.0
        # Escalation defaults must be set
        assert cb.escalation_threshold == 10
        assert cb.escalation_cooldown_seconds == 1800.0

    def test_close_after_success_unchanged(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.failure_count == 0


class TestBug17ExecutionEngineErrorMessage:
    """ExecutionEngine differentiates the error message between OPEN and ESCALATED."""

    def test_source_has_both_branches(self):
        src = inspect.getsource(ee_mod.ExecutionEngine.place_order)
        assert "circuit_breaker.escalated" in src, (
            "place_order must check circuit_breaker.escalated to emit a "
            "different error message — operator log analysis depends on this "
            "to distinguish 'API hiccup' from 'pathological failure pattern'."
        )
        assert "ESCALATED" in src, (
            "Error message for escalation must say ESCALATED so logs/grep "
            "find the new failure mode."
        )
        assert "Circuit breaker OPEN" in src, (
            "Original 'Circuit breaker OPEN' branch must remain for the "
            "transient-API-down case."
        )


class TestBug17EndToEnd:
    """OPERATOR-REQUIRED: the kill-switch flag must actually halt order placement
    end-to-end via ExecutionEngine.place_order — not just at the CircuitBreaker
    unit level. Tests the wiring (the lesson of Bug 7).

    Constructs a minimal ExecutionEngine with mocked dependencies, forces the
    CircuitBreaker into escalated state, calls place_order, asserts blocked.
    """

    @pytest.mark.asyncio
    async def test_escalated_breaker_blocks_place_order(self):
        # Stub deps — we don't need real client/risk_manager/db for this test,
        # only the CircuitBreaker check matters. The check is the FIRST gate
        # in the order path (after kill_switch + wallet validation).
        fake_client = _mock.MagicMock()
        fake_risk = _mock.MagicMock()
        fake_db = _mock.MagicMock()

        with _mock.patch.object(ee_mod, "Account") as _acc, \
             _mock.patch.object(ee_mod, "ContractManager"):
            _acc.from_key.return_value = _mock.MagicMock(address="0xtest")
            engine = ee_mod.ExecutionEngine(
                client=fake_client,
                risk_manager=fake_risk,
                db=fake_db,
                private_key="0x" + "0" * 64,
            )

        # Force escalation
        cb = engine.circuit_breaker
        cb.escalated = True
        cb.escalated_at = time.monotonic()
        cb.consecutive_reopens = cb.escalation_threshold

        # Stub kill_switch as None and contract_manager as None to bypass
        # approval flow and reach the circuit_breaker check directly. The
        # circuit_breaker gate is what we're testing — wallet/approval
        # paths have their own test coverage.
        engine.kill_switch = None
        engine.contract_manager = None

        result = await engine.place_order(
            bot_name="MirrorBot",
            market_id="test_market",
            token_id="test_token",
            side="BUY",
            size=1.0,
            price=0.5,
            confidence=0.7,
            skip_position_update=True,  # matches OrderGateway call pattern
        )

        assert result.get("success") is False, (
            "Escalated circuit breaker must block place_order. If this "
            "fails, the in-process kill switch is wired but doesn't gate "
            "the order path — Bug 7 pattern."
        )
        assert "ESCALATED" in result.get("error", ""), (
            f"Block reason must mention ESCALATED so operator can grep. "
            f"Got: {result.get('error')!r}"
        )

    @pytest.mark.asyncio
    async def test_non_escalated_open_blocks_with_different_message(self):
        """Sanity: normal OPEN state still blocks but with the original
        message shape (no false-positive ESCALATED report)."""
        fake_client = _mock.MagicMock()
        fake_risk = _mock.MagicMock()
        fake_db = _mock.MagicMock()

        with _mock.patch.object(ee_mod, "Account") as _acc, \
             _mock.patch.object(ee_mod, "ContractManager"):
            _acc.from_key.return_value = _mock.MagicMock(address="0xtest")
            engine = ee_mod.ExecutionEngine(
                client=fake_client,
                risk_manager=fake_risk,
                db=fake_db,
                private_key="0x" + "0" * 64,
            )

        # Force OPEN without escalation
        cb = engine.circuit_breaker
        cb.state = CircuitBreaker.OPEN
        cb.last_failure_time = time.monotonic()
        cb.failure_count = cb.failure_threshold
        assert cb.escalated is False

        engine.kill_switch = None
        engine.contract_manager = None

        result = await engine.place_order(
            bot_name="MirrorBot",
            market_id="test_market",
            token_id="test_token",
            side="BUY",
            size=1.0,
            price=0.5,
            confidence=0.7,
            skip_position_update=True,  # matches OrderGateway call pattern
        )

        assert result.get("success") is False
        assert "OPEN" in result.get("error", "")
        assert "ESCALATED" not in result.get("error", ""), (
            "Normal OPEN must NOT emit ESCALATED in the message — "
            "false-positive would mislead operator into thinking the "
            "pathological-failure escalation fired when it didn't."
        )
