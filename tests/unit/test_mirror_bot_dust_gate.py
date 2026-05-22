"""S217: MirrorBot dust-gate coupling tests.

The dust gate floor is computed as max(_abs_floor, min(_ceiling, _cap)) where
_cap = self.bankroll.max_bet_usd. This couples the floor to the bankroll
cap so the historical bug (max_bet_usd=$1 with hardcoded $25 floor silently
throttled MB for 6+ days) cannot recur.

Tests pull the formula out of mirror_bot.py into a pure helper to validate
the constraint relationship without needing the full bot/coordinator stack.
The helper mirrors the production logic at bots/mirror_bot.py ~line 3317.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ── Pure helper mirroring production logic ────────────────────────────────


def _dust_floor(bankroll, abs_floor: float = 0.10, ceiling: float = 25.0) -> float:
    """Replica of the production formula at bots/mirror_bot.py ~line 3317.
    Tests this in isolation so we don't have to spin up the full bot."""
    if bankroll is None:
        raise RuntimeError("mirror_dust_gate_bankroll_uninitialized")
    cap = float(bankroll.max_bet_usd)
    return max(abs_floor, min(ceiling, cap))


def _trade_passes(trade_usd: float, floor: float) -> bool:
    """Mirrors the rejection check at line 3320: passes iff trade_usd >= floor."""
    return trade_usd >= floor


def _mock_bankroll(max_bet_usd: float):
    b = MagicMock()
    b.max_bet_usd = max_bet_usd
    return b


# ── Dust-gate coupling tests ──────────────────────────────────────────────


class TestDustGateCoupling:
    """S217: floor follows cap; historical $25-floor bug cannot recur at $1 cap."""

    def test_dust_gate_couples_to_cap_at_one_dollar(self):
        """max_bet=$1 → floor=$1 → $1 trade passes (cap-equals-floor edge case)."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        floor = _dust_floor(bankroll)
        assert floor == 1.0
        assert _trade_passes(1.0, floor)
        assert not _trade_passes(0.99, floor)

    def test_dust_gate_couples_to_cap_at_25(self):
        """max_bet=$25 → floor=$25 → $5 rejects, $25 passes."""
        bankroll = _mock_bankroll(max_bet_usd=25.0)
        floor = _dust_floor(bankroll)
        assert floor == 25.0
        assert not _trade_passes(5.0, floor)
        assert _trade_passes(25.0, floor)

    def test_dust_gate_couples_to_cap_at_300_production(self):
        """max_bet=$300 → floor=$25 (ceiling clamp) → $5 rejects, $30 passes."""
        bankroll = _mock_bankroll(max_bet_usd=300.0)
        floor = _dust_floor(bankroll)
        assert floor == 25.0  # clamped to ceiling, NOT cap
        assert not _trade_passes(5.0, floor)
        assert _trade_passes(30.0, floor)

    def test_dust_gate_no_bankroll_raises(self):
        """bankroll=None → RuntimeError raised, no silent fallback to old $25 default."""
        with pytest.raises(RuntimeError, match="mirror_dust_gate_bankroll_uninitialized"):
            _dust_floor(None)

    def test_dust_gate_abs_floor_protects_zero_cap(self):
        """max_bet=$0 (degenerate) → floor=$0.10 (abs floor protects against div-by-zero downstream)."""
        bankroll = _mock_bankroll(max_bet_usd=0.0)
        floor = _dust_floor(bankroll)
        assert floor == 0.10  # abs_floor wins
        assert not _trade_passes(0.05, floor)
        assert _trade_passes(0.10, floor)

    def test_dust_gate_cap_transition_at_runtime(self):
        """Bankroll cap changes mid-run → next evaluation uses new floor.
        Important because the wallet-derived bankroll updates capital live but
        max_bet_usd is policy-constant; however the dust gate reads max_bet_usd
        fresh on each invocation, so any runtime change is picked up."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        assert _dust_floor(bankroll) == 1.0
        bankroll.max_bet_usd = 25.0
        assert _dust_floor(bankroll) == 25.0
        bankroll.max_bet_usd = 300.0
        assert _dust_floor(bankroll) == 25.0  # ceiling clamp


# ── Negative tests: production code does NOT reference MIRROR_MIN_TRADE_USD ──


class TestNoHardcodedFloorRegression:
    """S217: ensure mirror_bot.py no longer reads MIRROR_MIN_TRADE_USD at the
    dust-gate site. If anyone re-introduces it, this test fires."""

    def test_dust_gate_source_does_not_reference_legacy_setting(self):
        """The block around the dust-gate log call must no longer reference
        MIRROR_MIN_TRADE_USD. (Comments/docstrings elsewhere in the file may
        still mention it for historical context — this only checks the active
        code site.)"""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        # Find the active dust-gate site
        marker = "S217: Dust floor coupled to bankroll cap"
        assert marker in src, "S217 dust-gate marker not found — was the patch reverted?"
        # Ensure within ~30 lines of the marker we don't read the legacy setting
        idx = src.index(marker)
        window = src[idx:idx + 2000]
        assert "MIRROR_MIN_TRADE_USD" not in window, (
            "Legacy MIRROR_MIN_TRADE_USD reference reintroduced near S217 dust-gate. "
            "The hardcoded $25 floor must not return."
        )


# ── S227 clamp-up tests ────────────────────────────────────────────────────


def _clamp_up_to_floor(size: float, price: float, floor: float) -> tuple[float, float, bool]:
    """Replica of the S227 clamp-up logic at bots/mirror_bot.py.
    Returns (new_size, new_trade_usd, clamped_flag)."""
    trade_usd = size * price
    if trade_usd < floor and price > 0:
        new_size = floor / price
        return new_size, new_size * price, True
    return size, trade_usd, False


class TestDustGateClampUp:
    """S227: dust gate clamps size UP to floor instead of rejecting.

    Operator spec: the gate's only purpose is ensuring orders meet CLOB
    minimum; rejection was the wrong semantic (dampeners shrink
    max_bet=$1 trades below $1 → entries mathematically blocked).
    Now: clamp size up so trade_value = floor, continue to order, log
    mirror_force_min_clamped for shadow tracking.
    """

    def test_clamp_up_at_one_dollar_cap(self):
        """cap=$1 -> floor=$1; sub-floor trade gets clamped to exactly $1."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        floor = _dust_floor(bankroll)
        size_in = 1.46  # 1.46 * 0.5 = 0.73 trade (below $1)
        price = 0.50
        new_size, new_trade, clamped = _clamp_up_to_floor(size_in, price, floor)
        assert clamped is True
        assert new_trade == 1.0, f"clamped trade should equal floor, got {new_trade}"
        assert new_size == 2.0

    def test_clamp_no_op_when_at_or_above_floor(self):
        """Trade already at or above floor -> no clamp, no flag."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        floor = _dust_floor(bankroll)
        size_in = 2.0  # 2.0 * 0.50 = $1.00 trade
        new_size, new_trade, clamped = _clamp_up_to_floor(size_in, 0.50, floor)
        assert clamped is False
        assert new_size == 2.0
        assert new_trade == 1.0

    def test_clamp_preserves_s218_invariant_at_one_dollar(self):
        """After clamp-up, size * price <= bankroll.max_bet_usd holds (S218 invariant)."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        floor = _dust_floor(bankroll)
        _, new_trade, _ = _clamp_up_to_floor(0.5, 0.50, floor)  # 0.25 trade in
        assert new_trade <= bankroll.max_bet_usd, (
            f"S218 invariant violated: clamped {new_trade} > cap {bankroll.max_bet_usd}"
        )

    def test_clamp_at_production_300_cap(self):
        """cap=$300 -> floor=$25 (ceiling clamp); sub-$25 trade -> clamped to $25."""
        bankroll = _mock_bankroll(max_bet_usd=300.0)
        floor = _dust_floor(bankroll)
        assert floor == 25.0
        _, new_trade, clamped = _clamp_up_to_floor(10.0, 0.50, floor)  # in: $5 trade
        assert clamped is True
        assert new_trade == 25.0

    def test_clamp_zero_price_skipped(self):
        """price=0 edge case: no clamp (can't compute floor/0)."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        floor = _dust_floor(bankroll)
        new_size, new_trade, clamped = _clamp_up_to_floor(1.0, 0.0, floor)
        assert clamped is False
        assert new_size == 1.0
        assert new_trade == 0.0


class TestS227SourceRegression:
    """S227: ensure production mirror_bot.py uses clamp-up, not rejection."""

    def test_s227_clamp_marker_present(self):
        """Production source must contain the S227 clamp-up marker."""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        assert "S227: Clamp size UP" in src, (
            "S227 clamp-up marker not found — was the patch reverted?"
        )

    def test_s227_force_min_clamped_event_present(self):
        """Production source must emit mirror_force_min_clamped at the dust-gate site."""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        assert "mirror_force_min_clamped" in src, (
            "mirror_force_min_clamped log event missing — shadow-track logging gone."
        )

    def test_s227_no_dust_skipped_at_dust_gate(self):
        """Within the dust-gate block, mirror_dust_skipped reject-and-return must
        be gone. (Other log events / strings elsewhere in the file are fine.)"""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        marker = "S217: Dust floor coupled to bankroll cap"
        idx = src.index(marker)
        window = src[idx:idx + 2000]
        assert "mirror_dust_skipped" not in window, (
            "mirror_dust_skipped still present at dust-gate site — "
            "S227 clamp-up patch reverted? Should emit mirror_force_min_clamped."
        )
