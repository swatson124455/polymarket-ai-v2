"""S245 #1: marketable fill-or-kill + record-on-fill in _place_order_sync.

Root cause (phantom positions): under GTC the CLOB returns success the moment it
ACCEPTS the order. A resting limit (status='live') or a marketable miss
(status='unmatched') both came back as success, so the caller booked a full-size
`open` position holding 0 tokens on-chain. Proven against Polymarket's documented
POST /order response: success=true does NOT mean filled — `status` is the truth
(matched=filled, live=resting, unmatched=killed, delayed=async).

Fix (opt-in via CLOB_MARKETABLE_FOK_ENABLED, enabled per-bot in .env.mirror):
  - place a FOK at a marketable limit (signal price crossed by <= CLOB_MARKETABLE_CAP_PCT)
  - book a position (success=True) ONLY on status=='matched'; unmatched/live/delayed
    return {success: False, not_filled: True} so no phantom is created
  - signal `price` stays the recorded cost basis (real fill <= the limit ceiling)
Default OFF keeps the legacy GTC behavior byte-for-byte (zero blast radius on deploy).
"""
from __future__ import annotations

import sys
import unittest.mock as _mock

import pytest

from base_engine.execution import clob_adapter as ca_mod
from config.settings import settings


def _run(side, *, fok, status="matched", cap=0.05, price=0.40, size=1.5):
    """Drive _place_order_sync with a mocked V2 SDK; return (result, captured)."""
    captured = {}

    class _FakeOrderArgs:
        def __init__(self, token_id, price, size, side):
            captured.update(token_id=token_id, price=price, size=size, side=side)

    class _FakeClient:
        def create_and_post_order(self, order_args, order_type=None):
            captured["order_type"] = order_type
            return {"orderID": "test-oid", "status": status}

    fake_clob_types = _mock.MagicMock()
    fake_clob_types.OrderArgs = _FakeOrderArgs
    # OrderType.FOK resolves to a sentinel via the MagicMock; we only assert it is
    # passed (not None) on the FOK path.
    modules_patch = {
        "py_clob_client_v2": _mock.MagicMock(),
        "py_clob_client_v2.clob_types": fake_clob_types,
    }
    with _mock.patch.object(settings, "CLOB_MARKETABLE_FOK_ENABLED", fok, create=True), \
         _mock.patch.object(settings, "CLOB_MARKETABLE_CAP_PCT", cap, create=True), \
         _mock.patch.dict(sys.modules, modules_patch), \
         _mock.patch.object(ca_mod, "_get_clob_client", return_value=_FakeClient()):
        result = ca_mod._place_order_sync(
            market_id="mkt", token_id="tok", side=side, size=size, price=price,
        )
    return result, captured


class TestFokEnabled:

    def test_matched_books_position(self):
        result, cap = _run("YES", fok=True, status="matched")
        assert result["success"] is True
        assert result["order_id"] == "test-oid"
        # cost basis stays the SIGNAL price (0.40), not the marketable ceiling
        assert result["price"] == 0.40
        assert cap["order_type"] is not None  # FOK path used

    def test_unmatched_does_not_book(self):
        """Killed FOK: must NOT report success (no phantom position)."""
        result, _ = _run("YES", fok=True, status="unmatched")
        assert result["success"] is False
        assert result.get("not_filled") is True
        assert result.get("status") == "unmatched"

    def test_live_does_not_book(self):
        """Resting (shouldn't happen for FOK, but defensive): not a fill."""
        result, _ = _run("YES", fok=True, status="live")
        assert result["success"] is False
        assert result.get("not_filled") is True

    def test_buy_limit_crosses_up(self):
        """BUY marketable limit = signal * (1 + cap), so it can reach the ask."""
        _, cap = _run("YES", fok=True, status="matched", cap=0.05, price=0.40)
        assert cap["price"] == pytest.approx(0.42, abs=1e-6)  # 0.40 * 1.05

    def test_sell_limit_crosses_down(self):
        """SELL marketable limit = signal * (1 - cap), so it can reach the bid."""
        _, cap = _run("SELL", fok=True, status="matched", cap=0.05, price=0.40)
        assert cap["price"] == pytest.approx(0.38, abs=1e-6)  # 0.40 * 0.95

    def test_limit_rounded_to_001_tick(self):
        """Polymarket is 0.01-tick — a 3-decimal limit is rejected by the CLOB.
        0.40 * 1.07 = 0.428 must round to 0.43, not 0.428."""
        _, cap = _run("YES", fok=True, status="matched", cap=0.07, price=0.40)
        assert cap["price"] == 0.43
        # and the value must have at most 2 decimal places
        assert round(cap["price"], 2) == cap["price"]


class TestFokDisabledIsLegacyGtc:

    def test_disabled_books_on_orderid_no_status_gate(self):
        """Default OFF: legacy GTC behavior — success on order_id, status ignored,
        order placed at the raw signal price, no order_type passed."""
        result, cap = _run("YES", fok=False, status="live")  # 'live' must still succeed
        assert result["success"] is True
        assert result["order_id"] == "test-oid"
        assert cap["price"] == 0.40          # raw signal price, no marketable cross
        assert cap["order_type"] is None      # GTC default — no order_type kwarg

    def test_disabled_unmatched_still_success(self):
        """Legacy path does not gate on status (preserves pre-S245 behavior)."""
        result, _ = _run("YES", fok=False, status="unmatched")
        assert result["success"] is True
