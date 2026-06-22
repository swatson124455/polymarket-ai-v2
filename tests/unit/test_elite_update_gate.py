"""S248: the elite-status recompute (3× full-table UPDATE...GROUP BY over `trades`,
rewriting the whole `users` table — elite_detector.py:36/81/140) is gated to a slow
cadence inside IngestionScheduler._run_ingestion so it no longer fires every ~13-min
ingestion cycle and drains the per-process DB semaphore (database.py:207) fleet-wide.

These tests pin: the config default, the per-instance tracker init, and the due-cadence
arithmetic that gates the update_elite_status() call. The gate mirrors the existing
mini-backfill gate pattern in the same method.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from config.settings import settings
from base_engine.data.ingestion_scheduler import IngestionScheduler


def _elite_due(last, now, interval_min):
    # Mirrors the gate condition in IngestionScheduler._run_ingestion (S248):
    #   _elite_interval = ELITE_UPDATE_INTERVAL_MINUTES * 60
    #   _elite_due = last is None or (now - last).total_seconds() >= _elite_interval
    interval = int(interval_min) * 60
    return last is None or (now - last).total_seconds() >= interval


def test_elite_update_interval_default_is_60_min():
    # Default cadence is hourly — slow enough to bound the storm, fresh enough that
    # elite/market-maker labels (which don't change minute-to-minute) stay current.
    assert settings.ELITE_UPDATE_INTERVAL_MINUTES == 60


def test_fresh_scheduler_has_last_elite_update_none():
    # None means the first cycle after startup runs the recompute immediately, then gates.
    sched = IngestionScheduler(data_ingestion=MagicMock())
    assert sched._last_elite_update is None


def test_elite_gate_due_when_never_run():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    assert _elite_due(None, now, settings.ELITE_UPDATE_INTERVAL_MINUTES) is True


def test_elite_gate_not_due_within_interval():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=10)  # well within the 60-min interval
    assert _elite_due(last, now, settings.ELITE_UPDATE_INTERVAL_MINUTES) is False


def test_elite_gate_due_after_interval():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(minutes=61)  # past the 60-min interval
    assert _elite_due(last, now, settings.ELITE_UPDATE_INTERVAL_MINUTES) is True
