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
