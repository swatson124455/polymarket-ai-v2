"""S218: MirrorBot flat-sizing clamp + final-clamp invariant tests.

Two coupled fixes paired with the S217 dust-gate coupling:

  1. Source clamp (Option B). At [bots/mirror_bot.py ~line 3124], the flat-size
     config `MIRROR_FLAT_POSITION_SIZE_USD` was driving `size = _flat_usd / price`
     directly, ignoring `bankroll.max_bet_usd`. Result: with shadow-live cap=$1
     and flat=$30, entries landed at ~$4 (after downstream dampeners) instead of
     ≤ $1. The fix clamps `_flat_usd = min(_flat_usd_cfg, _cap_usd)` at the
     source, symmetric to the S217 dust-gate ceiling.

  2. Final-clamp invariant (Option C). Right before the dust gate, asserts
     `size * price ≤ bankroll.max_bet_usd`. Under monotonic-non-increasing
     downstream dampeners (risk_mult ≤1.0, NO dampener ≤0.75, M9/daily caps),
     this is a silent no-op when B is in place. The warning log only fires if
     some future code path bypasses B — making future regressions observable
     rather than silent.

Tests follow the S217 dust-gate pattern: pure helpers mirroring the production
logic, plus a regression test grepping the source so the fix can't be reverted
without a test failure.
"""
from __future__ import annotations

import logging

import pytest
from unittest.mock import MagicMock


# ── Pure helpers mirroring production logic ──────────────────────────────


def _flat_size(bankroll, flat_usd_cfg: float, price: float) -> tuple[float, float, float]:
    """Replica of the production B-clamp at bots/mirror_bot.py ~line 3124.
    Returns (size_shares, effective_flat_usd, cap_usd)."""
    if bankroll is None:
        raise RuntimeError("mirror_sizing_bankroll_uninitialized")
    cap_usd = float(bankroll.max_bet_usd)
    flat_usd = min(flat_usd_cfg, cap_usd)
    if flat_usd > 0 and price > 0:
        size = flat_usd / price
    else:
        size = 0.0
    return size, flat_usd, cap_usd


def _final_clamp(size: float, price: float, cap_usd: float, logger=None, market_id: str = "0xtest") -> float:
    """Replica of the production C-invariant just before the dust gate.
    Returns the post-clamp size (==input when no clamp needed)."""
    size_usd = size * price
    if size_usd > cap_usd:
        if logger is not None:
            logger.warning("mirror_size_capped_at_invariant",
                           extra={"pre_cap_usd": round(size_usd, 2),
                                  "cap_usd": round(cap_usd, 2),
                                  "market_id": str(market_id)[:16]})
        return cap_usd / price
    return size


def _mock_bankroll(max_bet_usd: float):
    b = MagicMock()
    b.max_bet_usd = max_bet_usd
    return b


# ── Source-clamp (Option B) tests ────────────────────────────────────────


class TestFlatSizingSourceClamp:
    """S218 Option B: flat-size config clamped to bankroll.max_bet_usd at the source."""

    def test_flat_clamped_to_cap_at_one_dollar_shadow_live(self):
        """cap=$1, flat_cfg=$30 → effective flat=$1, size=2.0 shares at price=0.5.
        Reproduces the shadow-live shape: any flat-config bypass is killed at source."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        size, flat, cap = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.50)
        assert flat == 1.0, f"flat should clamp to cap, got {flat}"
        assert cap == 1.0
        assert size == 2.0  # 1.0 / 0.50

    def test_flat_unchanged_at_production_cap(self):
        """cap=$300, flat_cfg=$30 → effective flat=$30 (cap is the ceiling, flat is binding).
        Ensures the clamp doesn't regress production sizing — when cap > flat, flat wins."""
        bankroll = _mock_bankroll(max_bet_usd=300.0)
        size, flat, cap = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.50)
        assert flat == 30.0
        assert cap == 300.0
        assert size == 60.0  # 30 / 0.50

    def test_flat_equals_cap_no_op(self):
        """cap=$30, flat_cfg=$30 → effective flat=$30 (no clamp needed)."""
        bankroll = _mock_bankroll(max_bet_usd=30.0)
        size, flat, cap = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.50)
        assert flat == 30.0
        assert cap == 30.0
        assert size == 60.0

    def test_flat_zero_price_zero_size(self):
        """price=0 → size=0 (would hit size-zero rejection downstream)."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        size, _, _ = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.0)
        assert size == 0.0

    def test_flat_bankroll_none_raises(self):
        """bankroll=None → RuntimeError, no silent fallback. Same shape as S217 dust-gate."""
        with pytest.raises(RuntimeError, match="mirror_sizing_bankroll_uninitialized"):
            _flat_size(None, flat_usd_cfg=30.0, price=0.50)

    def test_flat_sizing_at_zero_cap_returns_zero_size(self):
        """Degenerate cap=$0 → _flat_usd=min($30, $0)=$0 → size=0. No division-by-zero,
        no spurious trade. Defensive against operator setting max_bet_usd=0 to disable
        a bot via cap rather than service-level flag (per review chain Concern 5)."""
        bankroll = _mock_bankroll(max_bet_usd=0.0)
        size, flat, cap = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.50)
        assert flat == 0.0
        assert cap == 0.0
        assert size == 0.0  # downstream size-zero check at mirror_bot.py:3313 rejects

    def test_flat_runtime_cap_change_picked_up(self):
        """Bankroll cap changes mid-run → next sizing call uses new cap.
        Important because wallet-derived bankroll could theoretically shift max_bet_usd
        (currently policy-constant but defensive against future change)."""
        bankroll = _mock_bankroll(max_bet_usd=1.0)
        _, flat1, _ = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.50)
        assert flat1 == 1.0
        bankroll.max_bet_usd = 300.0
        _, flat2, _ = _flat_size(bankroll, flat_usd_cfg=30.0, price=0.50)
        assert flat2 == 30.0


# ── Final-clamp invariant (Option C) tests ───────────────────────────────


class TestFinalClampInvariant:
    """S218 Option C: silent no-op when sizing chain respects cap; warning + clamp when
    a future bypass produces size*price > cap. Defense in depth with observability."""

    def test_invariant_no_op_when_below_cap(self):
        """Normal case: size*price < cap → returns input size unchanged, no log."""
        logger = MagicMock()
        size = _final_clamp(size=2.0, price=0.50, cap_usd=1.0, logger=logger)
        # 2.0 * 0.50 = 1.0 == cap (boundary: not strictly greater, no clamp)
        assert size == 2.0
        logger.warning.assert_not_called()

    def test_invariant_no_op_when_strictly_under_cap(self):
        """Size strictly under cap → no clamp, no log."""
        logger = MagicMock()
        size = _final_clamp(size=1.5, price=0.50, cap_usd=1.0, logger=logger)
        # 1.5 * 0.50 = 0.75 < 1.0
        assert size == 1.5
        logger.warning.assert_not_called()

    def test_invariant_clamps_when_above_cap(self):
        """Bypass synthesis: size*price > cap → clamp to cap/price, warning log fires.
        This is the regression-detection signal."""
        logger = MagicMock()
        size = _final_clamp(size=10.0, price=0.50, cap_usd=1.0, logger=logger, market_id="0xfeedbeef")
        # 10.0 * 0.50 = 5.0 > 1.0 → clamp to 1.0 / 0.50 = 2.0
        assert size == 2.0
        logger.warning.assert_called_once()
        call_args = logger.warning.call_args
        assert call_args.args[0] == "mirror_size_capped_at_invariant"
        assert call_args.kwargs["extra"]["pre_cap_usd"] == 5.0
        assert call_args.kwargs["extra"]["cap_usd"] == 1.0

    def test_invariant_clamps_at_production_cap_with_oversize_bypass(self):
        """Synthetic over-cap at production scale: cap=$300, but size*price=$500.
        Clamps to $300 worth of shares."""
        logger = MagicMock()
        size = _final_clamp(size=1000.0, price=0.50, cap_usd=300.0, logger=logger)
        # 1000 * 0.50 = 500 > 300 → 300 / 0.50 = 600
        assert size == 600.0
        logger.warning.assert_called_once()

    def test_invariant_no_log_when_logger_none(self):
        """Logger=None defensive path (test convenience): clamp still happens, no exception."""
        # No assertion needed beyond no-exception
        size = _final_clamp(size=10.0, price=0.50, cap_usd=1.0, logger=None)
        assert size == 2.0


# ── Source regression: production code references the S218 markers ──────


class TestSourceRegression:
    """S218: greps mirror_bot.py to ensure the source clamp and invariant are
    actually wired — guards against accidental revert."""

    def test_source_clamp_marker_present(self):
        """Source clamp comment must appear in production code."""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        assert "S218: Flat-size clamped to bankroll.max_bet_usd" in src, (
            "S218 source-clamp marker missing — was Option B reverted?"
        )

    def test_invariant_marker_present(self):
        """Final-clamp invariant comment + log emitter must appear in production code."""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        assert "S218: Final-clamp invariant" in src, (
            "S218 invariant marker missing — was Option C reverted?"
        )
        assert "mirror_size_capped_at_invariant" in src, (
            "Invariant warning log emitter missing — Option C is silent without it."
        )

    def test_flat_uses_min_against_cap(self):
        """The exact min() expression coupling _flat_usd to _cap_usd must be present."""
        import inspect
        from bots import mirror_bot
        src = inspect.getsource(mirror_bot)
        assert "min(_flat_usd_cfg, _cap_usd)" in src, (
            "Source-clamp expression `min(_flat_usd_cfg, _cap_usd)` missing — "
            "the flat-size bypass returns without this line."
        )
