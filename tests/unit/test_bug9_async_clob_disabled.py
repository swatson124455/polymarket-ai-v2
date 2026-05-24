"""S228 Bug 9: AsyncClobClient disabled (V1-import incompatibility under V2).

Bug history:
  - S226 migrated clob_adapter.py to py_clob_client_v2 (CLOB V2 retired V1
    auth 2026-04). _get_clob_client() builds a py_clob_client_v2.ClobClient.
  - async_clob_client.py was not updated — still imports the V1 SDK at file
    top (py_clob_client.client.ClobClient et al.).
  - When ClobAdapter.place_order took the async fast-path
    (_get_async_client → AsyncClobClient), _build_post_order_request
    isinstance-checked the V2 client against the V1 ClobClient class →
    always False → returned None → place_order returned
    {success: False, error: 'CLOB client or request build failed'}.
  - Surfaced S228 live flip #3 (2026-05-24): every order produced fake
    "Order placed" events (Bug 10 misclassified the failure as success).
    Pre-authorized rollback fired under 60s. $0 real capital touched.

Fix:
  - ClobAdapter._get_async_client now returns None unconditionally.
    place_order falls back to _place_order_sync via run_in_executor,
    which uses the V2 SDK directly and is already V2-correct.
  - Loses async fast-path perf (one thread hop per order via executor),
    preserves correctness. async_clob_client.py left in tree as latent
    code until a separate session ports it to V2.

These tests detect future regressions via behavioral + source-grep,
mirroring the S217/S218/S227 Bug 7/Bug 8 pattern.
"""
from __future__ import annotations

from base_engine.execution import clob_adapter as ca_mod


class TestAsyncClientDisabled:
    """Bug 9: _get_async_client must return None unconditionally."""

    def test_get_async_client_returns_none(self):
        """Direct behavioral check — no inspection of internals."""
        adapter = ca_mod.ClobAdapter()
        assert adapter._get_async_client() is None, (
            "ClobAdapter._get_async_client returned non-None — Bug 9 fix "
            "reverted. Async fast-path will dispatch to AsyncClobClient which "
            "imports V1 SDK and silently fails under V2."
        )


class TestS228Bug9SourceRegression:
    """Source-grep regression tests mirroring S227 Bug 7 pattern."""

    def test_s228_bug9_marker_present(self):
        """Production source must contain the S228 Bug 9 marker."""
        import inspect
        src = inspect.getsource(ca_mod)
        assert "S228 Bug 9" in src, (
            "S228 Bug 9 marker missing — fix may have been reverted. "
            "Without it, AsyncClobClient will be re-enabled and orders "
            "will silently fail under V2."
        )

    def test_get_async_client_has_unconditional_return_none(self):
        """The function body must contain an unconditional return None
        (no try/except construction returning an AsyncClobClient instance)."""
        import inspect
        from base_engine.execution.clob_adapter import ClobAdapter
        src = inspect.getsource(ClobAdapter._get_async_client)
        # The method body should end with `return None` and not call
        # AsyncClobClient(). The marker text already asserts the why.
        assert "return None" in src, "Method must explicitly return None"
        assert "AsyncClobClient()" not in src, (
            "Method must NOT instantiate AsyncClobClient under the Bug 9 fix. "
            "If you need it, port async_clob_client.py to py_clob_client_v2 first."
        )
