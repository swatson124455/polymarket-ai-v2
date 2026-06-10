"""B1 (S241) — category-scoped tradeable-market scans get an end_date_iso freshness clause.

Root cause: the liquidity-ordered LIMIT in _fetch_tradeable_markets had no end-date
awareness, so dead rows still flagged active+unresolved (10.5k NULL-dated + 1.8k
past-dated weather rows, measured 2026-06-10) crowded fresh markets out of the scan
set — WB's city universe collapsed to 1 city.

Contract under test (parametrized over BOTH trees — top-level engine = live WB hybrid,
silo engine = post-weather_main.py-cutover):
  - categories given + CATEGORY_SCAN_FRESHNESS_DAYS > 0 -> SQL contains the
    :fresh_cutoff clause, param is a naive-UTC datetime ~N days back, and the Redis
    cache key carries the freshness suffix.
  - categories given + knob = 0 -> clause absent (rollback path).
  - NO categories -> clause absent and cache key unchanged (non-category callers'
    query is byte-identical to before).
"""
import importlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_TREES = [
    ("base_engine.base_engine", "config.settings"),                                  # top-level — LIVE hybrid
    ("bots.weather.engine.base_engine.base_engine", "bots.weather.engine.config.settings"),  # silo — post-cutover
]


class _CaptureSession:
    """Mock session that records the SQL text + params passed to execute()."""

    def __init__(self):
        self.captured_sql = None
        self.captured_params = None

    async def execute(self, stmt, params=None):
        # First call captures the id-list query; later calls (Market objects) return empty.
        if self.captured_sql is None:
            self.captured_sql = str(stmt)
            self.captured_params = params
        result = MagicMock()
        result.fetchall.return_value = []
        result.scalars.return_value.all.return_value = []
        return result


def _make_engine(engine_mod):
    """Construct the engine (inert ctor) and wire a capture-session db; no cache."""
    eng = engine_mod.BaseEngine()
    sess = _CaptureSession()

    @asynccontextmanager
    async def _get_session():
        yield sess

    eng.db = SimpleNamespace(get_session=_get_session)
    eng.cache = None
    eng.unified_market_service = None

    # Empty id-list triggers the API fallback (engine.client is None in tests) —
    # stub it out; this test only cares about the SQL sent to the DB.
    async def _no_api(*args, **kwargs):
        return []

    eng.get_markets = _no_api
    return eng, sess


@pytest.fixture(params=_TREES, ids=["top_level", "silo"])
def tree(request):
    engine_mod = importlib.import_module(request.param[0])
    settings_mod = importlib.import_module(request.param[1])
    return engine_mod, settings_mod.settings


@pytest.mark.asyncio
async def test_category_query_has_freshness_clause(tree, monkeypatch):
    engine_mod, settings = tree
    monkeypatch.setattr(settings, "CATEGORY_SCAN_FRESHNESS_DAYS", 2, raising=False)
    eng, sess = _make_engine(engine_mod)

    await eng._fetch_tradeable_markets(0, ["weather"], 0.0)

    assert sess.captured_sql is not None
    assert ":fresh_cutoff" in sess.captured_sql
    assert "end_date_iso >= :fresh_cutoff" in sess.captured_sql
    cutoff = sess.captured_params["fresh_cutoff"]
    # Naive UTC datetime (matches NaiveUTCDateTime column), ~2 days back.
    assert isinstance(cutoff, datetime) and cutoff.tzinfo is None
    expected = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    assert abs((cutoff - expected).total_seconds()) < 60


@pytest.mark.asyncio
async def test_zero_knob_disables_clause(tree, monkeypatch):
    engine_mod, settings = tree
    monkeypatch.setattr(settings, "CATEGORY_SCAN_FRESHNESS_DAYS", 0, raising=False)
    eng, sess = _make_engine(engine_mod)

    await eng._fetch_tradeable_markets(0, ["weather"], 0.0)

    assert sess.captured_sql is not None
    assert "fresh_cutoff" not in sess.captured_sql
    assert "fresh_cutoff" not in (sess.captured_params or {})


@pytest.mark.asyncio
async def test_non_category_query_unchanged(tree, monkeypatch):
    engine_mod, settings = tree
    monkeypatch.setattr(settings, "CATEGORY_SCAN_FRESHNESS_DAYS", 2, raising=False)
    eng, sess = _make_engine(engine_mod)

    await eng._fetch_tradeable_markets(100, None, 0.0)

    assert sess.captured_sql is not None
    assert "fresh_cutoff" not in sess.captured_sql
    assert "categories" not in (sess.captured_params or {})
