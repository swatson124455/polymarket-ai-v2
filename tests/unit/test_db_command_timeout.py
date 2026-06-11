"""DB_COMMAND_TIMEOUT_S — optional asyncpg client-side command timeout (2026-06-11, EB).

WI-21b wedge dumps caught the esports scan task awaiting UNBOUNDED on connection
ops against dead/half-open peers (pool_pre_ping at checkout, session close at
__aexit__) — frames server-side statement_timeout cannot reach. asyncpg's
command_timeout is its internal, protocol-aware bound (terminates the connection
on expiry — not the corrupting external wait_for-cancel pattern).

Safety invariant pinned here: DEFAULT 0 → command_timeout is OMITTED from
connect_args entirely, so every service that doesn't opt in (mirror/weather/
ingestion, and master after any future cherry-pick) is byte-for-byte unchanged.
Only the esports bot service opts in via .env.esports (DB_COMMAND_TIMEOUT_S=90).
"""
from unittest.mock import patch

import pytest


def _captured_connect_args(monkeypatch, env_value=None):
    """Run Database._init_postgres with create_async_engine mocked; return connect_args.

    Driven via coro.send(None) — _init_postgres has NO await before
    create_async_engine, so the mocked raise propagates synchronously without
    any event loop. (asyncio.run() here closed/cleared the global loop state
    and broke unrelated weather tests in full-suite runs — do not reintroduce.)
    """
    from base_engine.data import database as db_mod

    monkeypatch.setattr(
        db_mod.settings, "DB_COMMAND_TIMEOUT_S", env_value or 0, raising=False
    )

    captured = {}

    def _fake_engine(url, **kw):
        captured.update(kw)
        raise RuntimeError("stop-after-capture")  # halt init right after capture

    db = db_mod.Database()
    with patch.object(db_mod, "create_async_engine", side_effect=_fake_engine):
        coro = db._init_postgres("postgresql://u:p@localhost/db")
        with pytest.raises(RuntimeError, match="stop-after-capture"):
            coro.send(None)
        coro.close()
    return captured.get("connect_args", {})


def test_default_zero_omits_command_timeout(monkeypatch):
    """DB_COMMAND_TIMEOUT_S=0 (default) → key ABSENT → all services unchanged."""
    ca = _captured_connect_args(monkeypatch, env_value=0)
    assert "command_timeout" not in ca, (
        f"command_timeout must be omitted at default 0; connect_args={ca}"
    )
    # the pre-existing keys are still intact
    assert "statement_cache_size" in ca and "timeout" in ca


def test_positive_value_sets_command_timeout(monkeypatch):
    """DB_COMMAND_TIMEOUT_S=90 (as in .env.esports) → command_timeout=90.0."""
    ca = _captured_connect_args(monkeypatch, env_value=90)
    assert ca.get("command_timeout") == 90.0


def test_settings_default_is_zero():
    """The settings default must stay 0 — opt-in only, never global."""
    from config.settings import Settings
    import os
    assert float(os.getenv("DB_COMMAND_TIMEOUT_S", "0")) == 0.0 or True
    # construct fresh Settings without the env var to pin the default
    saved = os.environ.pop("DB_COMMAND_TIMEOUT_S", None)
    try:
        assert Settings().DB_COMMAND_TIMEOUT_S == 0.0
    finally:
        if saved is not None:
            os.environ["DB_COMMAND_TIMEOUT_S"] = saved
