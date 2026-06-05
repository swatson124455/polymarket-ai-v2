"""S230 Bug 16: NO-entry side translation for V2 per-outcome-token model.

Bug history:
  - 2026-05-27 S230 live re-flip: every live NO-side order rejected with
    "balance: 0, order amount: N" for 10 unique markets despite the deposit
    wallet holding $23.14993 pUSD. Initial hypothesis (cache staleness) was
    refuted: `get_balance_allowance(COLLATERAL)` returned the correct
    balance both BEFORE and AFTER a refresh, and Bug 15 (TTL re-refresh)
    deploy didn't change the symptom.
  - Root cause located at clob_adapter.py:85 — `_place_order_sync` translated
    side="NO" to "SELL". Under Polymarket V2 with per-outcome CTF tokens, a
    NO-side entry should be a BUY of the NO outcome token. Sending SELL
    triggered Polymarket's CTF token balance check, found 0 holdings of the
    NO token, returned "balance: 0".
  - Verified: 170/170 recent MB NO-side positions have token_id =
    markets.no_token_id (DB query 2026-05-27 02:33 UTC). The bot already
    passes the correct outcome token; only the side translation was wrong.

Fix shape:
  - clob_adapter.py:85 — map both YES and NO to BUY (token_id distinguishes
    outcome). SELL still passes through unchanged for exits. Invalid sides
    still return the existing error.
  - mirror_bot.py:1438 — exit_side = "SELL" (unchanged) — exits bypass the
    YES/NO branch entirely.

Four scenarios under V2:
  | bot side intent | bot passes token_id of | SDK side after translation |
  |-----------------|------------------------|----------------------------|
  | enter YES       | yes_token_id            | BUY                        |
  | enter NO        | no_token_id             | BUY (was SELL — Bug 16)    |
  | exit YES        | yes_token_id            | SELL                       |
  | exit NO         | no_token_id             | SELL                       |

Cross-bot blast radius:
  - clob_adapter.py: shared module. Only MirrorBot is in live mode currently
    (EB/WB in paper or via splinter releases). Other live consumers of
    clob_adapter would be affected — but no other bot exercises the YES/NO
    branch since they pass BUY/SELL directly.
  - No DB schema change. No env change. Behavior change ONLY in live mode
    on NO-side entries.

Tests are source-grep + one functional check via mocked SDK import. Source-
grep mirrors the test_bug13/test_bug12 pattern. The functional test catches
re-introduction of the bug via a runtime path — defense in depth, since
"NO → SELL" is the kind of regression that source-grep alone could miss if
someone refactored the conditional.
"""
from __future__ import annotations

import inspect
import sys
import unittest.mock as _mock

from base_engine.execution import clob_adapter as ca_mod


class TestBug16SourceGrep:
    """Static checks on _place_order_sync side translation."""

    def test_yes_translates_to_buy(self):
        src = inspect.getsource(ca_mod._place_order_sync)
        # The mapping branch must produce "BUY" when input is in {YES, NO}.
        # Loose check: the branch body assigns side_upper = "BUY".
        assert 'side_upper = "BUY"' in src, (
            "_place_order_sync must map YES/NO entries to BUY. The V2 SDK "
            "uses BUY = 'acquire shares of token_id, paying USDC'."
        )

    def test_no_does_not_translate_to_sell(self):
        src = inspect.getsource(ca_mod._place_order_sync)
        # The original buggy pattern: 'BUY' if side_upper == "YES" else "SELL"
        assert '"BUY" if side_upper == "YES" else "SELL"' not in src, (
            "Bug 16: the NO → SELL translation must NOT be present. Under V2 "
            "per-outcome-token model, NO entry = BUY of the NO outcome token "
            "(token_id distinguishes outcome). The old translation caused "
            "every live NO order to fail with 'balance: 0' (CTF token balance "
            "check)."
        )
        # Tighter: the side_upper assignment in the YES/NO branch must not
        # produce "SELL" for any input.
        assert 'else "SELL"' not in src, (
            "An 'else \"SELL\"' tail in the YES/NO branch reintroduces Bug 16. "
            "Both YES and NO must map to BUY."
        )

    def test_sell_exits_bypass_translation(self):
        src = inspect.getsource(ca_mod._place_order_sync)
        # The YES/NO branch is guarded by `if side_upper not in ("BUY", "SELL")`.
        # So SELL (and BUY) reach the OrderArgs construction unchanged.
        assert 'if side_upper not in ("BUY", "SELL"):' in src, (
            "The translation branch must be guarded by `if side_upper not in "
            "('BUY', 'SELL')`. SELL exits arrive from mirror_bot.py:1438 "
            "(exit_side = 'SELL') and must reach OrderArgs without rewrite — "
            "exits sell the token the bot owns."
        )

    def test_invalid_side_still_rejected(self):
        src = inspect.getsource(ca_mod._place_order_sync)
        assert 'return {"success": False, "error": f"Invalid side: {side}"}' in src, (
            "_place_order_sync must still reject unknown sides (not in "
            "{BUY, SELL, YES, NO}) with the existing error shape — callers "
            "rely on this."
        )


class TestBug16Functional:
    """Runtime check: feed each of the 4 scenarios through _place_order_sync
    with a mocked SDK and inspect the side actually passed to OrderArgs."""

    def _run_scenario(self, side: str) -> str:
        """Invoke _place_order_sync and return the side that reached OrderArgs.

        Mocks the SDK at sys.modules level so the lazy import inside the
        function picks up our fake. Returns the captured side string or
        raises if the call returned an error (so the test can assert).
        """
        captured = {}

        class _FakeOrderArgs:
            def __init__(self, token_id, price, size, side):
                captured["token_id"] = token_id
                captured["price"] = price
                captured["size"] = size
                captured["side"] = side

        class _FakeClient:
            def create_and_post_order(self, order_args):
                # Mimic V2 success response shape
                return {"orderID": "test-order-id"}

        fake_clob_types = _mock.MagicMock()
        fake_clob_types.OrderArgs = _FakeOrderArgs
        fake_py_clob_pkg = _mock.MagicMock()

        modules_patch = {
            "py_clob_client_v2": fake_py_clob_pkg,
            "py_clob_client_v2.clob_types": fake_clob_types,
        }

        with _mock.patch.dict(sys.modules, modules_patch), \
             _mock.patch.object(ca_mod, "_get_clob_client", return_value=_FakeClient()):
            result = ca_mod._place_order_sync(
                market_id="market_xyz",
                token_id="token_for_outcome",
                side=side,
                size=1.5,
                price=0.4,
            )

        if not result.get("success"):
            raise AssertionError(f"_place_order_sync returned error: {result}")
        return captured.get("side")

    def test_scenario_1_yes_entry_maps_to_buy(self):
        """YES entry: bot passes side='YES' + token_id=yes_token_id.
        SDK must receive BUY (acquire YES tokens, pay USDC)."""
        assert self._run_scenario("YES") == "BUY"

    def test_scenario_2_no_entry_maps_to_buy(self):
        """NO entry: bot passes side='NO' + token_id=no_token_id.
        SDK must receive BUY (acquire NO tokens, pay USDC). This is the
        Bug 16 regression check — old code sent SELL, causing 'balance: 0'."""
        assert self._run_scenario("NO") == "BUY"

    def test_scenario_3_yes_exit_passes_sell(self):
        """Exit a YES position: bot calls place_order with side='SELL' +
        token_id=yes_token_id (the YES tokens being sold). SDK must
        receive SELL unchanged — exit sells the owned token."""
        assert self._run_scenario("SELL") == "SELL"

    def test_scenario_4_no_exit_passes_sell(self):
        """Exit a NO position: bot calls place_order with side='SELL' +
        token_id=no_token_id (the NO tokens being sold). Same SELL
        passthrough — the side='SELL' doesn't encode outcome."""
        # Same code path as scenario 3 — exits always pass side='SELL'
        # regardless of position outcome. token_id picks which token.
        assert self._run_scenario("SELL") == "SELL"

    def test_invalid_side_returns_error(self):
        """Unknown sides must still produce the existing error shape."""
        # Don't use _run_scenario (which raises on error) — call directly.
        with _mock.patch.dict(sys.modules, {
            "py_clob_client_v2": _mock.MagicMock(),
            "py_clob_client_v2.clob_types": _mock.MagicMock(),
        }), _mock.patch.object(ca_mod, "_get_clob_client", return_value=_mock.MagicMock()):
            result = ca_mod._place_order_sync(
                market_id="m", token_id="t", side="GARBAGE",
                size=1.0, price=0.5,
            )
        assert result["success"] is False
        assert "Invalid side" in result["error"]
