"""
FEATURE_PRECOMPUTE_ENABLED gate (2026-06-10, EB session).

base_engine.start() starts the feature-precompute loop (_feature_precompute_loop)
only when `settings.FEATURE_PRECOMPUTE_ENABLED` is true. The esports service sets
FEATURE_PRECOMPUTE_ENABLED=false in .env.esports because neither EsportsBot nor
EsportsBotV2 consumes the PE feature cache (both use their own models), and
batch_precompute_all_features fans out one task per market — ~2,500 live tasks
observed at EVERY scan-stall wedge (WI-21b dump: _precompute_one x2474 parked on
its Semaphore(2)), with the 30+ min batch restarted every 60s. Default true keeps
mirror/ensemble services unchanged.

Mirrors tests/unit/test_elite_batch_gate.py (the b9e4caf precedent). Pins:
default-true safety, env parse, and the source-level gate (a revert makes the
.env.esports flag a silent no-op). The KS-regime loop must NOT be gated — it is
unrelated and lightweight. Live effect verified post-deploy via the
"Feature pre-compute loop disabled" log line + task_count in WI-21b dumps.
"""
import pytest


class TestFeaturePrecomputeEnabledFlag:
    def test_default_true(self):
        """No env var set → True, so mirror/ensemble keep the loop."""
        from config.settings import settings
        assert getattr(settings, "FEATURE_PRECOMPUTE_ENABLED", "MISSING") is True

    def test_env_false_disables(self, monkeypatch):
        """FEATURE_PRECOMPUTE_ENABLED=false (as in .env.esports) → False."""
        monkeypatch.setenv("FEATURE_PRECOMPUTE_ENABLED", "false")
        from config.settings import Settings
        assert Settings().FEATURE_PRECOMPUTE_ENABLED is False

    def test_env_true_enables(self, monkeypatch):
        monkeypatch.setenv("FEATURE_PRECOMPUTE_ENABLED", "true")
        from config.settings import Settings
        assert Settings().FEATURE_PRECOMPUTE_ENABLED is True

    def test_start_gate_reads_setting(self):
        """start() must gate the precompute loop on FEATURE_PRECOMPUTE_ENABLED."""
        import inspect
        from base_engine.base_engine import BaseEngine
        src = inspect.getsource(BaseEngine.start)
        assert "FEATURE_PRECOMPUTE_ENABLED" in src, (
            "start() no longer gates the feature-precompute loop — "
            ".env.esports FEATURE_PRECOMPUTE_ENABLED=false would be a silent no-op"
        )

    def test_ks_regime_loop_not_gated(self):
        """The B12 KS-regime loop is independent of the precompute gate.

        Source-level: the KS task creation must not sit inside the
        FEATURE_PRECOMPUTE_ENABLED branch. Cheap proxy: the disabled-path log
        line and the KS start line both exist, and the KS start appears AFTER
        the gate's else-branch text (i.e., outside the gated if/else)."""
        import inspect
        from base_engine.base_engine import BaseEngine
        src = inspect.getsource(BaseEngine.start)
        assert "_ks_regime_detection_loop" in src
        gate_else = src.index("Feature pre-compute loop disabled")
        ks_start = src.index("_ks_regime_detection_loop()")
        assert ks_start > gate_else, (
            "KS-regime loop creation must come after (outside) the precompute "
            "gate's else branch — gating it too would silently kill B12"
        )
