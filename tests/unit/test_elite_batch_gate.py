"""
ELITE_BATCH_ENABLED gate (2026-06-03, EB session).

base_engine.start() starts the elite-direction batch-refresh loop
(_elite_batch_loop) only when `settings.ELITE_BATCH_ENABLED` is true.
The esports service sets ELITE_BATCH_ENABLED=false in .env.esports because
neither EsportsBot nor EsportsBotV2 consumes elite_direction — there the loop
only re-queries thousands of elite-trader rows every 60s and burns the small
esports DB pool. Default true keeps mirror/ensemble/ingestion services unchanged.

These tests pin the safety-critical invariant (default true → the elite-consuming
services never silently lose the loop) and the env-var parse used by .env.esports.
The gate's live effect on the esports service is verified post-deploy via the
"Elite direction batch refresh loop disabled" log line + esports scan funnel.
"""
import pytest


class TestEliteBatchEnabledFlag:
    def test_default_true(self):
        """No env var set → True, so mirror/ensemble/ingestion keep the loop."""
        from config.settings import settings
        assert getattr(settings, "ELITE_BATCH_ENABLED", "MISSING") is True

    def test_env_false_disables(self, monkeypatch):
        """ELITE_BATCH_ENABLED=false (as in .env.esports) → False."""
        monkeypatch.setenv("ELITE_BATCH_ENABLED", "false")
        from config.settings import Settings
        assert Settings().ELITE_BATCH_ENABLED is False

    def test_env_true_enables(self, monkeypatch):
        """ELITE_BATCH_ENABLED=true → True."""
        monkeypatch.setenv("ELITE_BATCH_ENABLED", "true")
        from config.settings import Settings
        assert Settings().ELITE_BATCH_ENABLED is True

    def test_start_gate_reads_setting(self):
        """base_engine.start() gates the loop on settings.ELITE_BATCH_ENABLED.

        Guards Risk C: if the gate is reverted, the .env.esports flag becomes a
        no-op and esports silently re-burns its pool. Source-level check because
        start() is too large to construct in a unit test.
        """
        import inspect
        from base_engine.base_engine import BaseEngine
        src = inspect.getsource(BaseEngine.start)
        assert "ELITE_BATCH_ENABLED" in src, (
            "start() no longer gates the elite-batch loop on ELITE_BATCH_ENABLED — "
            ".env.esports ELITE_BATCH_ENABLED=false would be a silent no-op"
        )
