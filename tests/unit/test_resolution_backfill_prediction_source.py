"""S228: resolution backfill must discover markets from prediction_log.

Defect: run_resolution_backfill Phase 2 discovered markets to resolution-check
from traded_markets (2a), on-chain trades (2b) and open live positions only.
Markets a bot merely LOGGED predictions on — the overwhelming majority for
prediction-heavy bots like WeatherBot (2026-07-11: 132 of 185 window markets
untracked; daily markets ended 3-4 days earlier still resolved=f) — were never
checked, so markets.resolution stayed NULL and prediction_log rows were never
labeled. Calibration measurement and the S222 verification gate starve.

Fix: Phase 2c — prediction-log-driven discovery with a dedicated budget
(prediction_log_limit, default 150), ended-markets only, most-recently-ended
first, deduped against the trade-driven lists.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from base_engine.data.resolution_backfill import run_resolution_backfill

PRED_ONLY_MARKET_ID = "778899"  # numeric Gamma id → resolved via client.get_market

CLOSED_YES_MARKET = {
    "id": PRED_ONLY_MARKET_ID,
    "closed": True,
    "outcomePrices": '["1", "0"]',
    "endDateISO": "2026-07-10T00:00:00Z",
    "question": "Will the highest temperature in Hong Kong be 31°C on July 7?",
}


def _result(rows):
    r = MagicMock()
    r.fetchall.return_value = rows
    r.scalar_one_or_none.return_value = 0
    r.scalar.return_value = 0
    return r


def _make_db(pred_rows):
    """DB mock whose session routes each discovery query to a canned result.

    Every trade-driven discovery source (phase 1 missing, 2a traded_markets,
    2b trades, positions) returns EMPTY — the market exists ONLY via the
    prediction_log discovery query.
    """
    executed_sql = []

    async def _exec(sql, params=None):
        s = " ".join(str(sql).split())
        executed_sql.append(s)
        if "FROM prediction_log" in s and "m.end_date_iso < NOW()" in s:
            return _result(pred_rows)  # phase 2c — the new discovery source
        return _result([])  # every other query: empty

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=_exec)
    session.commit = AsyncMock()

    db = MagicMock()
    db.session_factory = object()
    db.get_session = MagicMock(return_value=session)
    db.save_market_resolution = AsyncMock()
    db.mark_market_resolved = AsyncMock()
    db.bulk_insert_markets = AsyncMock()
    for _bf in (
        "backfill_prediction_log_resolution",
        "backfill_mirror_rejected_signals_resolution",
        "backfill_prediction_log_from_closed_trades",
        "backfill_paper_trades_resolution",
        "backfill_trade_events_resolution",
        "backfill_shadow_resolution",
        "backfill_positions_resolution",
    ):
        setattr(db, _bf, AsyncMock(return_value=0))
    return db, executed_sql


def _make_client(market):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get_market = AsyncMock(return_value=market)
    return client


class TestS228PredictionLogDiscovery:
    @pytest.mark.asyncio
    async def test_prediction_only_market_gets_resolution_checked(self):
        """A market present ONLY in prediction_log must be fetched and resolved.

        Pre-fix: no discovery query touches prediction_log, the market is never
        fetched, save_market_resolution is never called — markets.resolution
        stays NULL forever and the prediction_log grader has nothing to copy.
        """
        db, _ = _make_db(pred_rows=[(PRED_ONLY_MARKET_ID, None)])
        client = _make_client(CLOSED_YES_MARKET)

        await run_resolution_backfill(db, client, delay_seconds=0, log_progress=False)

        client.get_market.assert_awaited()
        fetched_ids = [c.args[0] for c in client.get_market.await_args_list]
        assert PRED_ONLY_MARKET_ID in fetched_ids, (
            "prediction_log-only market was never resolution-checked — "
            "the S228 discovery gap is back"
        )
        db.save_market_resolution.assert_awaited()
        saved = db.save_market_resolution.await_args_list[0].args
        assert saved[0] == PRED_ONLY_MARKET_ID
        assert saved[2] == "YES", f"outcome-prices [1,0] must resolve YES, got {saved[2]!r}"

    @pytest.mark.asyncio
    async def test_prediction_log_limit_zero_disables_discovery(self):
        """prediction_log_limit=0 must skip the new discovery source entirely."""
        db, executed_sql = _make_db(pred_rows=[(PRED_ONLY_MARKET_ID, None)])
        client = _make_client(CLOSED_YES_MARKET)

        await run_resolution_backfill(
            db, client, prediction_log_limit=0, delay_seconds=0, log_progress=False
        )

        assert not any(
            "FROM prediction_log" in s and "m.end_date_iso < NOW()" in s
            for s in executed_sql
        ), "limit=0 must not run the prediction_log discovery query"
        db.save_market_resolution.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discovery_query_is_bounded_and_ended_only(self):
        """The discovery SQL must bound the scan (14-day window, LIMIT) and
        only consider ended markets — an unbounded scan of prediction_log or
        rechecking still-open markets would burn the API budget for nothing."""
        db, executed_sql = _make_db(pred_rows=[])
        client = _make_client(None)

        await run_resolution_backfill(db, client, delay_seconds=0, log_progress=False)

        pred_sql = [
            s for s in executed_sql
            if "FROM prediction_log" in s and "m.end_date_iso < NOW()" in s
        ]
        assert pred_sql, "prediction_log discovery query never ran at default limit"
        s = pred_sql[0]
        assert "INTERVAL '14 days'" in s
        assert "LIMIT" in s.upper()
        assert "resolution IS NULL" in s
        # S228 follow-up: markets with NULL end_date_iso (35 of 44 residual
        # unresolved on 2026-07-11, CLOB-verified closed+settled) must qualify
        # once their latest prediction is >48h old — a bare `end_date < NOW()`
        # filter silently strands them forever.
        assert "m.end_date_iso IS NULL" in s, (
            "NULL-end-date markets are excluded from discovery again — "
            "they strand unresolved forever (S228 follow-up regression)"
        )
        assert "48 hours" in s and "latest_pred" in s
        assert "NULLS LAST" in s, "dated backlog must drain before NULL-end-date stragglers"
