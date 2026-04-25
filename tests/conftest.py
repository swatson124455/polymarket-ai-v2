"""
Pytest configuration. Sets project-local temp dir when system temp is unusable (e.g. sandbox).
Provides shared fixtures for unit and integration tests.
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Use project-local temp dir when system temp may be inaccessible (sandbox, permissions)
_project_root = Path(__file__).resolve().parent.parent

# Ensure project root is on sys.path so top-level packages (esports_v2, etc.) are importable
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_pytest_tmp = _project_root / ".pytest_tmp"
_pytest_tmp.mkdir(parents=True, exist_ok=True)
# Override temp dirs so pytest/capture use project dir (avoids FileNotFoundError when system temp inaccessible)
os.environ["TMPDIR"] = str(_pytest_tmp)
os.environ["TEMP"] = str(_pytest_tmp)
os.environ["TMP"] = str(_pytest_tmp)

import pytest
import structlog
pytest_plugins = ('pytest_asyncio',)

# Configure structlog for tests to match production config (main.py).
# Without this, tests run with structlog's DEFAULT configuration which includes
# `format_exc_info` in the processor chain — this conflicts with ConsoleRenderer
# and emits "Remove format_exc_info from your processor chain" UserWarnings.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),  # Handles exc_info + stack info; no format_exc_info needed
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO = 20
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,  # False in tests so config is applied fresh each test session
)


# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """Mock Database instance with common async methods stubbed."""
    db = AsyncMock()
    db.session_factory = MagicMock()
    db.get_session = MagicMock(return_value=AsyncMock())
    db.get_trades_since = AsyncMock(return_value=[])
    db.get_bot_metrics = AsyncMock(return_value={"trades_won": 0, "total_pnl": 0.0})
    db.get_all_bots_metrics = AsyncMock(return_value=[])
    db.get_clv_diagnostic = AsyncMock(return_value={"clv": 0.0, "count": 0})
    db.reconcile_pnl = AsyncMock(return_value={"total_discrepancy": 0.0, "bots": {}})
    db.bulk_insert_markets = AsyncMock()
    db.bulk_insert_prices = AsyncMock()
    db.save_market_resolution = AsyncMock()
    db.backfill_positions_resolution = AsyncMock(return_value=0)
    db.backfill_paper_trades_resolution = AsyncMock(return_value=0)
    db.backfill_prediction_log_resolution = AsyncMock(return_value=0)
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=0)
    db.backfill_trade_events_resolution = AsyncMock(return_value=0)
    db.load_esports_team_aliases = AsyncMock(return_value={})
    db.log_unmatched_prediction = AsyncMock(return_value=None)
    db.bulk_upsert_team_aliases = AsyncMock(return_value=0)
    db.record_empty_price_fetch = AsyncMock()
    db.get_recent_resolved_predictions = AsyncMock(return_value=[])
    db.get_recent_brier_from_prediction_log = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_settings(monkeypatch):
    """Override settings for test isolation."""
    from config.settings import settings
    # Set safe defaults for testing
    monkeypatch.setattr(settings, "SIMULATION_MODE", True)
    monkeypatch.setattr(settings, "RISK_MAX_POSITION_SIZE_USD", 10.0)
    monkeypatch.setattr(settings, "RISK_MAX_TOTAL_EXPOSURE_USD", 50.0)
    return settings


@pytest.fixture
def mock_base_engine(mock_db):
    """Mock BaseEngine with commonly-needed attributes."""
    engine = AsyncMock()
    engine.db = mock_db
    engine.running = True
    engine.event_bus = None
    engine.signal_ingestion = None
    engine.trade_flow_analyzer = None
    engine.google_trends = None
    engine.kill_switch = None
    engine.risk_manager = AsyncMock()
    engine.risk_manager.calculate_position_size = AsyncMock(return_value=10.0)
    engine.get_markets = AsyncMock(return_value=[])
    engine.get_predictions = AsyncMock(return_value=None)
    engine.filter_markets_for_trading = MagicMock(return_value=[])
    engine.place_order = AsyncMock(return_value={"success": True, "order_id": "test-123"})
    engine.register_bot_for_price_events = MagicMock()
    return engine


@pytest.fixture
def sample_market():
    """A realistic Polymarket market dict for testing."""
    return {
        "id": "0xabc123",
        "condition_id": "0xabc123",
        "question": "Will BTC exceed $100k by March 2026?",
        "slug": "btc-100k-march-2026",
        "category": "crypto",
        "liquidity": 50000.0,
        "volume": 120000.0,
        "resolved": False,
        "resolution": None,
        "yes_token_id": "tok_yes_001",
        "no_token_id": "tok_no_001",
        "tokens": [
            {"tokenId": "tok_yes_001", "outcomePrice": 0.65, "outcome": "Yes"},
            {"tokenId": "tok_no_001", "outcomePrice": 0.35, "outcome": "No"},
        ],
    }


@pytest.fixture
def sample_trade():
    """A realistic trade dict."""
    return {
        "id": "trade-001",
        "market_id": "0xabc123",
        "token_id": "tok_yes_001",
        "side": "BUY",
        "size": 10.0,
        "price": 0.65,
        "timestamp": "2026-02-10T12:00:00Z",
    }
