"""S238: ENTRY trade_event silent non-write → phantom position (position 208926).

Forensics (2026-06-01): a WeatherBot position was created by confirm_position but its
ENTRY never landed in trade_events (the P&L authority). insert_trade_event returned a
bare `None`, which the paper-trade persistence path treated as warn-and-proceed — so the
position stood with no matching ENTRY and the reconciler chased the divergence all day.

Two fixes, two layers:

  Fix C (insert_trade_event): on a zero-row ENTRY INSERT, disambiguate a benign idempotent
  skip (a row with this exact minute-bucketed key already exists → return its sequence_num)
  from a TRUE non-write (no such row → loud ERROR, return None). `None` from the ENTRY path
  now means UNAMBIGUOUSLY "not written".

  Fix B (_persist_buy_entry): treat that unambiguous None as a retryable failure (re-run the
  idempotent gather), and on persistent failure emit a loud, durable phantom_unrecovered
  error instead of breaking silently.

Targets the vendored copy WeatherBot runs
(bots/weather/engine/base_engine/{data/database.py,execution/paper_trading.py}).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bots.weather.engine.base_engine.data.database as db_module
import bots.weather.engine.base_engine.execution.paper_trading as pt_module
from bots.weather.engine.base_engine.data.database import Database
from bots.weather.engine.base_engine.execution.paper_trading import PaperTradingEngine


def _entry_session(insert_row, dupe_row=None):
    """Mocked async session for an ENTRY insert. execute() side-effects, in order:
    [SET LOCAL, FK check (market exists), INSERT ... RETURNING, (dupe SELECT)].
    The dupe SELECT only runs when the INSERT returns no row (fix C path)."""
    r_setlocal = MagicMock()
    r_fk = MagicMock()
    r_fk.fetchone = MagicMock(return_value=(1,))           # market exists → no FK auto-heal
    r_insert = MagicMock()
    r_insert.fetchone = MagicMock(return_value=insert_row)  # RETURNING sequence_num (or None)
    effects = [r_setlocal, r_fk, r_insert]
    if insert_row is None:
        r_dupe = MagicMock()
        r_dupe.fetchone = MagicMock(return_value=dupe_row)  # existing row (dedup) or None (failure)
        effects.append(r_dupe)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=effects)
    session.commit = AsyncMock()
    return session


def _entry_db(session):
    db = MagicMock()
    db.session_factory = MagicMock()
    db.get_session = MagicMock(return_value=session)
    return db


class TestFixC_EntryReturnDisambiguation:
    @pytest.mark.asyncio
    async def test_successful_insert_returns_seq_no_dupe_query(self):
        """Happy path unchanged: INSERT returns a row → return its seq, no extra query."""
        session = _entry_session(insert_row=(123,))
        with patch.object(db_module, "logger", MagicMock()):
            seq = await Database.insert_trade_event(
                _entry_db(session), event_type="ENTRY", bot_name="WeatherBot",
                market_id="m1", side="YES", size=10.0, price=0.5, token_id="tok",
            )
        assert seq == 123
        assert session.execute.call_count == 3            # no 4th (dupe) query on success

    @pytest.mark.asyncio
    async def test_zero_rows_with_existing_row_is_benign_dedup(self):
        """Idempotent skip: INSERT writes nothing but the key already exists →
        return the EXISTING seq (NOT None), so the caller treats it as recorded."""
        session = _entry_session(insert_row=None, dupe_row=(999,))
        with patch.object(db_module, "logger", MagicMock()) as ml:
            seq = await Database.insert_trade_event(
                _entry_db(session), event_type="ENTRY", bot_name="WeatherBot",
                market_id="m1", side="YES", size=10.0, price=0.5, token_id="tok",
            )
        assert seq == 999                                  # benign dedup → existing seq
        assert session.execute.call_count == 4             # ran the dupe disambiguation query
        ml.error.assert_not_called()                       # not a failure

    @pytest.mark.asyncio
    async def test_zero_rows_no_existing_row_is_failure(self):
        """True non-write (the S238 phantom): INSERT writes nothing AND no row exists →
        return None and emit a loud ERROR so it is diagnosable, not silent."""
        session = _entry_session(insert_row=None, dupe_row=None)
        with patch.object(db_module, "logger", MagicMock()) as ml:
            seq = await Database.insert_trade_event(
                _entry_db(session), event_type="ENTRY", bot_name="WeatherBot",
                market_id="m1", side="YES", size=10.0, price=0.5, token_id="tok",
            )
        assert seq is None                                 # unambiguous "not written"
        ml.error.assert_called_once()
        assert ml.error.call_args.args[0] == "trade_event_entry_wrote_zero_rows"


def _persist_self(event_results):
    """Mock PaperTradingEngine self for _persist_buy_entry. insert_paper_trade always
    succeeds (returns None like the real method); insert_trade_event yields event_results."""
    mock_self = MagicMock()
    mock_self.db = MagicMock()
    mock_self.db.insert_paper_trade = AsyncMock(return_value=None)
    mock_self.db.insert_trade_event = AsyncMock(side_effect=list(event_results))
    mock_self._pending_correlation_ids = set()
    return mock_self


async def _call_persist(mock_self):
    await PaperTradingEngine._persist_buy_entry(
        mock_self, trade_id="t1", market_id="m1", token_id="tok", bot_name="WeatherBot",
        db_side="YES", size=10.0, price=0.5, confidence=0.8, correlation_id="c1",
        latency_ms=5.0, submitted_at=None, model_version=1, model_name="mdl", event_data={},
    )


class TestFixB_PersistBuyEntryCompensation:
    @pytest.mark.asyncio
    async def test_immediate_success_no_retry(self):
        """ENTRY lands first try (or benign dedup → non-None) → one attempt, no error."""
        mock_self = _persist_self([789])
        with patch.object(pt_module, "logger", MagicMock()) as ml, \
                patch.object(pt_module.asyncio, "sleep", AsyncMock()) as sleep:
            await _call_persist(mock_self)
        assert mock_self.db.insert_trade_event.call_count == 1
        sleep.assert_not_called()
        ml.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_transient_failure_then_success(self):
        """None (failure) then a seq → retries once, succeeds, no phantom error."""
        mock_self = _persist_self([None, 456])
        with patch.object(pt_module, "logger", MagicMock()) as ml, \
                patch.object(pt_module.asyncio, "sleep", AsyncMock()) as sleep:
            await _call_persist(mock_self)
        assert mock_self.db.insert_trade_event.call_count == 2
        assert mock_self.db.insert_paper_trade.call_count == 2   # idempotent UPSERT re-run
        assert sleep.call_count == 1
        ml.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_persistent_failure_emits_loud_durable_error(self):
        """None on every attempt → 3 tries, then a loud phantom_unrecovered ERROR
        (no silent break leaving the reconciler to chase it)."""
        mock_self = _persist_self([None, None, None])
        with patch.object(pt_module, "logger", MagicMock()) as ml, \
                patch.object(pt_module.asyncio, "sleep", AsyncMock()):
            await _call_persist(mock_self)
        assert mock_self.db.insert_trade_event.call_count == 3
        ml.error.assert_called_once()
        assert ml.error.call_args.args[0] == "trade_event_entry_phantom_unrecovered"
