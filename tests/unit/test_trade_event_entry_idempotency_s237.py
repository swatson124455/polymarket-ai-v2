"""S237 HIGH-3: ENTRY trade_event idempotency key must allow legitimate re-entries.

The ENTRY idempotency key (the P&L authority) used to be
    f"{bot}:{market}:{token}:{side}:ENTRY"
with no time component, so a legitimate RE-ENTRY into the same (market, token, side) after
an exit + cooldown produced the same key → the WHERE NOT EXISTS dedup silently dropped the
second ENTRY, leaving an open position with no matching ENTRY row.

The fix adds a MINUTE-resolution bucket. This guards both halves:
  - concurrent same-(market,token,side) attempts in one scan (and immediate idempotent
    retries) land in the SAME minute → SAME key → still deduped (S141 intent preserved);
  - a re-entry minutes/hours later (cooldowns are >= 30 min) → DIFFERENT minute → DIFFERENT
    key → recorded as its own lot.

Targets the vendored copy WeatherBot runs (bots/weather/engine/base_engine/data/database.py).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bots.weather.engine.base_engine.data.database import Database


async def _entry_idem_key(event_time):
    """Run insert_trade_event(ENTRY) against a fully-mocked session; return the
    idempotency_key it passed to the INSERT."""
    r_setlocal = MagicMock()
    r_fk = MagicMock()
    r_fk.fetchone = MagicMock(return_value=(1,))      # market exists → no FK auto-heal
    r_insert = MagicMock()
    r_insert.fetchone = MagicMock(return_value=(123,))  # RETURNING sequence_num
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=[r_setlocal, r_fk, r_insert])
    session.commit = AsyncMock()

    db = MagicMock()
    db.session_factory = MagicMock()
    db.get_session = MagicMock(return_value=session)

    await Database.insert_trade_event(
        db, event_type="ENTRY", bot_name="WeatherBot", market_id="m1",
        side="YES", size=10.0, price=0.5, token_id="tok", event_time=event_time,
    )
    # The INSERT is the last execute call; its 2nd positional arg is the params dict.
    params = session.execute.call_args_list[-1].args[1]
    return params["idempotency_key"]


class TestEntryIdempotencyKey:
    @pytest.mark.asyncio
    async def test_same_minute_dedups_but_reentry_is_distinct(self):
        base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=2)

        k_first = await _entry_idem_key(base)
        k_same_minute = await _entry_idem_key(base + timedelta(seconds=30))   # concurrent / retry
        k_reentry = await _entry_idem_key(base + timedelta(minutes=35))       # re-entry after cooldown

        # S141 preserved: concurrent same-(market,token,side) attempts share a key → deduped.
        assert k_first == k_same_minute
        # HIGH-3 fix: a later re-entry gets a distinct key → its ENTRY is recorded.
        assert k_first != k_reentry
        # The key carries the minute bucket and the ENTRY discriminator.
        assert k_first.endswith(base.strftime("%Y%m%d%H%M"))
        assert ":ENTRY:" in k_first

    @pytest.mark.asyncio
    async def test_non_entry_key_unchanged(self):
        """Non-ENTRY events keep the order_id/correlation-based key (no time bucket)."""
        r_setlocal = MagicMock()
        r_fk = MagicMock()
        r_fk.fetchone = MagicMock(return_value=(1,))
        r_size = MagicMock()
        r_size.fetchone = MagicMock(return_value=(10.0, 0.0))  # EXIT over-size guard: entry=10, exit=0
        r_insert = MagicMock()
        r_insert.fetchone = MagicMock(return_value=(124,))
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(side_effect=[r_setlocal, r_fk, r_size, r_insert])
        session.commit = AsyncMock()
        db = MagicMock()
        db.session_factory = MagicMock()
        db.get_session = MagicMock(return_value=session)

        await Database.insert_trade_event(
            db, event_type="EXIT", bot_name="WeatherBot", market_id="m1",
            side="SELL", size=10.0, price=0.6, token_id="tok",
            order_id="ord-1", event_time=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        params = session.execute.call_args_list[-1].args[1]
        assert params["idempotency_key"] == "WeatherBot:m1:SELL:ord-1"
