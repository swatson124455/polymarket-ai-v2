"""S227 Bug 7: regression test for CLOB V2 adapter wiring.

Bug history:
  - S226 migrated `clob_adapter.py` to py-clob-client-v2 (Polymarket retired
    V1 CLOB auth in 2026-04, returning HTTP 401 Unauthorized).
  - execution_engine.py:100 declares `self.clob_adapter = None  # Set by
    BaseEngine if CLOB creds available`, but BaseEngine never actually
    instantiated ClobAdapter() in production code.
  - Result: execution_engine.py:300 check `if self.clob_adapter and ...`
    always evaluated False; every live order fell through to V1 fallback
    (polymarket_client.place_order) which returned HTTP 401.
  - Surfaced during S227 live flip — pre-authorized rollback triggered.

Fix (S227 Bug 7):
  - BaseEngine.__init__ now constructs ClobAdapter() and assigns it to
    self.execution_engine.clob_adapter when DEPOSIT_WALLET_ADDRESS and
    CLOB_API_KEY are both present in settings.
  - V1 fallback preserved for bots without V2 config (paper-mode bots
    never reach execution_engine.place_order anyway — orders route to
    PaperTradingEngine).

These tests detect future regressions via source-grep, mirroring the
S217 dust-gate + S218 final-clamp regression-test pattern.
"""
from __future__ import annotations


class TestClobAdapterWiringSourceRegression:
    """S227 Bug 7: ensure BaseEngine wires ClobAdapter into execution_engine."""

    def test_base_engine_imports_clob_adapter(self):
        """BaseEngine source must reference ClobAdapter (import inside the
        wiring block, gated on settings to keep V1 fallback path intact)."""
        import inspect
        from base_engine import base_engine
        src = inspect.getsource(base_engine)
        assert "from base_engine.execution.clob_adapter import ClobAdapter" in src, (
            "ClobAdapter import missing from BaseEngine — Bug 7 fix reverted? "
            "V2 path will be dead code; live orders will fall to V1 polymarket_client (HTTP 401)."
        )

    def test_base_engine_assigns_clob_adapter_to_execution_engine(self):
        """BaseEngine must assign the constructed adapter to
        execution_engine.clob_adapter. Without this assignment, the V2 path
        at execution_engine.py:300 is dead code."""
        import inspect
        from base_engine import base_engine
        src = inspect.getsource(base_engine)
        assert "self.execution_engine.clob_adapter = " in src, (
            "BaseEngine no longer assigns to execution_engine.clob_adapter — "
            "Bug 7 fix reverted? V2 path will be dead code."
        )

    def test_s227_bug7_marker_present(self):
        """Production source must contain the S227 Bug 7 marker for grep-ability."""
        import inspect
        from base_engine import base_engine
        src = inspect.getsource(base_engine)
        assert "S227 Bug 7" in src, (
            "S227 Bug 7 marker missing — was the patch reverted?"
        )

    def test_clob_adapter_v2_wired_log_event_present(self):
        """Production source must emit clob_adapter_v2_wired log event on
        successful wiring. This is the operator-visible signal that the V2
        path is active for the bot at startup."""
        import inspect
        from base_engine import base_engine
        src = inspect.getsource(base_engine)
        assert "clob_adapter_v2_wired" in src, (
            "clob_adapter_v2_wired log event missing — operator startup-visibility "
            "for V2 wiring regressed."
        )

    def test_v1_fallback_warning_log_present(self):
        """When the V2 wiring path fails (adapter unavailable / construction
        raises), BaseEngine must emit a warning naming the V1 fallback —
        critical operational visibility per CLAUDE.md 'Can't Fully Verify' rule."""
        import inspect
        from base_engine import base_engine
        src = inspect.getsource(base_engine)
        assert "clob_adapter_v2_unavailable_falling_back_to_v1" in src or \
               "clob_adapter_v2_wiring_failed_falling_back_to_v1" in src, (
            "V1 fallback warning log missing — silent regression risk for V2 wiring failure."
        )

    def test_wiring_gated_on_deposit_wallet_and_api_key(self):
        """Wiring must be gated on DEPOSIT_WALLET_ADDRESS + CLOB_API_KEY to
        avoid touching bots without V2 config (preserves V1 fallback path
        for any bot not configured for V2 ops)."""
        import inspect
        from base_engine import base_engine
        src = inspect.getsource(base_engine)
        # Find the wiring block via the S227 marker
        marker = "S227 Bug 7"
        assert marker in src
        idx = src.index(marker)
        window = src[idx:idx + 2500]
        assert "DEPOSIT_WALLET_ADDRESS" in window, (
            "Wiring not gated on DEPOSIT_WALLET_ADDRESS — bots without V2 deposit "
            "wallet would attempt V2 path and get 'maker address not allowed' errors."
        )
        assert "CLOB_API_KEY" in window, (
            "Wiring not gated on CLOB_API_KEY — bots without V2 API creds would "
            "attempt V2 path and fail."
        )
