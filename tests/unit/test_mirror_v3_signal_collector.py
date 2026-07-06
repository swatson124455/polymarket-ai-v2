"""v3 signal collector tests — RTDS ingress -> mirror_rejected_signals stream.

Contract mirrored from old MB ingress (elite_watchlist.on_rtds_trade /
on_trade_event + mirror_bot whale gate). The collector is the silo's OWN
producer of the whale-signal population the acceptance-gate backtest consumes,
so old MB can be fully stopped. Every filter here matches an old-MB filter by
design; the DB is mocked so no migration/live socket is needed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mirror_v3 import BOT_NAME
from mirror_v3.signal_collector import (
    V3_REJECTION_REASON,
    V3_REJECTION_STAGE,
    V3_SOURCE_TAG,
    V3SignalCollector,
)

WATCHED = "0x1234567890abcdef1234567890abcdef12345678"  # 42 chars, watched
UNWATCHED = "0xdead000000000000000000000000000000beef00"


def _make_collector(*, watched=(WATCHED.lower(),), restored=True, min_whale_usd=0.0):
    db = AsyncMock()
    db.insert_mirror_rejected_signal = AsyncMock(return_value=None)
    watched_set = set(watched)
    c = V3SignalCollector(
        db,
        is_watched=lambda a: a in watched_set,
        is_restored=lambda: restored,
        min_whale_usd=min_whale_usd,
    )
    return c, db


def _trade(**over):
    """A valid watched-wallet whale entry (YES, $200) unless overridden."""
    base = {
        "proxyWallet": WATCHED,
        "asset": "tok-123",
        "conditionId": "0xmarketcondition",
        "outcome": "Yes",
        "price": 0.50,
        "size": 400,  # 400 * 0.50 = $200 > $100
        "side": "BUY",
        "transactionHash": "0xtxhash1",
    }
    base.update(over)
    return base


# ── happy path: a clean signal is written with the v3 marker ─────────────────

@pytest.mark.asyncio
async def test_valid_signal_is_logged_with_v3_marker():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade())

    db.insert_mirror_rejected_signal.assert_awaited_once()
    kw = db.insert_mirror_rejected_signal.await_args.kwargs
    assert kw["rejection_reason"] == V3_REJECTION_REASON
    assert kw["rejection_stage"] == V3_REJECTION_STAGE
    assert kw["metadata"]["source"] == V3_SOURCE_TAG
    assert kw["metadata"]["bot_name"] == BOT_NAME
    assert c.get_stats()["logged"] == 1


@pytest.mark.asyncio
async def test_full_trader_address_never_truncated():
    """Phase B counterfactual ranking collides on 10-hex prefixes — the full
    42-char address must be persisted (same guardrail as old MB _log_rejection)."""
    c, db = _make_collector()
    await c.on_rtds_trade(_trade())
    kw = db.insert_mirror_rejected_signal.await_args.kwargs
    assert kw["trader_address"] == WATCHED
    assert len(kw["trader_address"]) == 42


@pytest.mark.asyncio
async def test_whale_usd_and_side_mapped():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(outcome="No", price=0.40, size=500))  # $200, NO
    kw = db.insert_mirror_rejected_signal.await_args.kwargs
    assert kw["side"] == "NO"
    assert kw["whale_trade_usd"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_up_down_outcomes_map_to_yes_no():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(outcome="Up", transactionHash="0xa"))
    await c.on_rtds_trade(_trade(outcome="Down", transactionHash="0xb"))
    sides = [ci.kwargs["side"] for ci in db.insert_mirror_rejected_signal.await_args_list]
    assert sides == ["YES", "NO"]


# ── ingress filters (each matches an old-MB reject) ──────────────────────────

@pytest.mark.asyncio
async def test_unwatched_wallet_skipped():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(proxyWallet=UNWATCHED))
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert c.get_stats()["watched_matched"] == 0


@pytest.mark.asyncio
async def test_pre_restore_blocked():
    c, db = _make_collector(restored=False)
    await c.on_rtds_trade(_trade())
    db.insert_mirror_rejected_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_floor_by_default_small_trade_logged():
    """DEFAULT: no whale floor — the gate sets the size threshold, so a $50 trade
    is LOGGED, not dropped. Dropping it would be irrecoverable once v3 is the sole
    writer, and the old bot's $100 floor is deliberately not inherited."""
    c, db = _make_collector()  # default min_whale_usd=0 (no floor)
    await c.on_rtds_trade(_trade(size=100, price=0.50))  # $50
    db.insert_mirror_rejected_signal.assert_awaited_once()
    assert c.get_stats()["skipped_not_whale"] == 0


@pytest.mark.asyncio
async def test_optional_floor_still_skips_when_set():
    """An operator CAN re-enable a floor (data-justified); then small trades skip."""
    c, db = _make_collector(min_whale_usd=100.0)
    await c.on_rtds_trade(_trade(size=100, price=0.50))  # $50 < $100
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert c.get_stats()["skipped_not_whale"] == 1


@pytest.mark.asyncio
async def test_whale_exactly_at_floor_is_logged():
    """>= floor passes when a floor is set (mirror_bot uses `< min` to reject)."""
    c, db = _make_collector(min_whale_usd=100.0)
    await c.on_rtds_trade(_trade(size=200, price=0.50))  # exactly $100
    db.insert_mirror_rejected_signal.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0.005, 0.01, 0.99, 0.995])
async def test_price_out_of_ingress_bounds_skipped(price):
    c, db = _make_collector()
    # size chosen so size*price would clear the whale floor if price passed
    await c.on_rtds_trade(_trade(price=price, size=100_000))
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert c.get_stats()["skipped_price"] == 1


@pytest.mark.asyncio
async def test_sell_is_exit_skipped():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(side="SELL"))
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert c.get_stats()["skipped_exit"] == 1


@pytest.mark.asyncio
async def test_unknown_outcome_skipped():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(outcome="Maybe"))
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert c.get_stats()["skipped_unknown_side"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [
    {"proxyWallet": None},
    {"conditionId": None},
    {"asset": None},
    {"price": "not-a-number"},
    {"size": 0},
])
async def test_malformed_events_skipped(over):
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(**over))
    db.insert_mirror_rejected_signal.assert_not_awaited()


# ── dedup (transport artifact removal) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_txhash_logged_once():
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(transactionHash="0xsame"))
    await c.on_rtds_trade(_trade(transactionHash="0xsame"))
    assert db.insert_mirror_rejected_signal.await_count == 1
    assert c.get_stats()["deduped"] == 1


@pytest.mark.asyncio
async def test_composite_dedup_when_no_txhash():
    c, db = _make_collector()
    ev = _trade()
    ev.pop("transactionHash")
    await c.on_rtds_trade(dict(ev))
    await c.on_rtds_trade(dict(ev))
    assert db.insert_mirror_rejected_signal.await_count == 1


@pytest.mark.asyncio
async def test_dedup_set_is_bounded():
    db = AsyncMock()
    db.insert_mirror_rejected_signal = AsyncMock(return_value=None)
    c = V3SignalCollector(
        db, is_watched=lambda a: True, is_restored=lambda: True,
        min_whale_usd=100.0, dedup_maxlen=10,
    )
    for i in range(25):
        await c.on_rtds_trade(_trade(transactionHash=f"0x{i}"))
    assert c.get_stats()["seen_tx_size"] <= 10


# ── fail-safe: instrumentation must never break ingest ───────────────────────

@pytest.mark.asyncio
async def test_db_write_failure_does_not_raise():
    c, db = _make_collector()
    db.insert_mirror_rejected_signal = AsyncMock(side_effect=RuntimeError("db down"))
    # Must not raise.
    await c.on_rtds_trade(_trade())


@pytest.mark.asyncio
async def test_none_db_is_noop():
    c = V3SignalCollector(
        None, is_watched=lambda a: True, is_restored=lambda: True,
    )
    await c.on_rtds_trade(_trade())  # must not raise
    assert c.get_stats()["events_seen"] == 1


@pytest.mark.asyncio
async def test_handler_swallows_unexpected_errors():
    """A watched predicate that throws must not propagate out of the handler."""
    def _boom(_a):
        raise ValueError("predicate blew up")

    db = AsyncMock()
    c = V3SignalCollector(db, is_watched=_boom, is_restored=lambda: True)
    await c.on_rtds_trade(_trade())  # must not raise


# ── A1: control sample of non-watchlist wallets ─────────────────────────────

def _make_control_collector(rate):
    db = AsyncMock()
    db.insert_mirror_rejected_signal = AsyncMock(return_value=None)
    c = V3SignalCollector(
        db, is_watched=lambda a: a == WATCHED.lower(),
        is_restored=lambda: True, control_sample_rate=rate,
    )
    return c, db


@pytest.mark.asyncio
async def test_a1_control_disabled_by_default_unwatched_skipped():
    c, db = _make_collector()  # default control_sample_rate=0
    await c.on_rtds_trade(_trade(proxyWallet=UNWATCHED))
    db.insert_mirror_rejected_signal.assert_not_awaited()
    assert c.get_stats()["control_matched"] == 0


@pytest.mark.asyncio
async def test_a1_rate_1_logs_every_unwatched_trade_as_control():
    c, db = _make_control_collector(rate=1)  # 1-in-1: sample everything
    await c.on_rtds_trade(_trade(proxyWallet=UNWATCHED))
    db.insert_mirror_rejected_signal.assert_awaited_once()
    kw = db.insert_mirror_rejected_signal.await_args.kwargs
    assert kw["metadata"]["stream"] == "control"
    assert kw["metadata"]["source"] == "mirror_v3"
    s = c.get_stats()
    assert s["control_matched"] == 1 and s["control_logged"] == 1
    assert s["logged"] == 0  # watched counter untouched


@pytest.mark.asyncio
async def test_a1_watched_stream_unaffected_by_control_mode():
    c, db = _make_control_collector(rate=1)
    await c.on_rtds_trade(_trade())  # watched wallet
    kw = db.insert_mirror_rejected_signal.await_args.kwargs
    assert kw["metadata"]["stream"] == "raw_watched_whale"
    assert c.get_stats()["logged"] == 1
    assert c.get_stats()["control_logged"] == 0


@pytest.mark.asyncio
async def test_a1_sampling_is_deterministic_and_subsampling():
    """crc32-based: same events -> same decisions across instances; a rate of
    N keeps roughly 1/N (exactly: crc32(key)%N==0), never all."""
    decisions = []
    for run in range(2):
        c, db = _make_control_collector(rate=7)
        for i in range(200):
            await c.on_rtds_trade(
                _trade(proxyWallet=UNWATCHED, transactionHash=f"0xctl{i}")
            )
        decisions.append(db.insert_mirror_rejected_signal.await_count)
    assert decisions[0] == decisions[1]        # deterministic across instances
    assert 0 < decisions[0] < 200              # a strict subsample


@pytest.mark.asyncio
async def test_a1_control_rows_still_deduped():
    c, db = _make_control_collector(rate=1)
    ev = _trade(proxyWallet=UNWATCHED, transactionHash="0xsamectl")
    await c.on_rtds_trade(dict(ev))
    await c.on_rtds_trade(dict(ev))
    assert db.insert_mirror_rejected_signal.await_count == 1


# ── A4: feed-lag telemetry (retires the DELTA_SECONDS=60 folklore) ──────────

@pytest.mark.asyncio
async def test_a4_feed_lag_measured_from_event_timestamp():
    import time as _t
    c, db = _make_collector()
    now = _t.time()
    await c.on_rtds_trade(_trade(timestamp=now - 7.0))          # 7s lag, seconds epoch
    await c.on_rtds_trade(_trade(timestamp=(now - 3.0) * 1000,  # 3s lag, ms epoch
                                 transactionHash="0xlag2"))
    s = c.get_stats()
    assert s["feed_lag_n"] == 2
    assert 2.0 < s["feed_lag_p50_s"] < 8.5   # both samples in the 3-7s band
    assert s["feed_lag_max_s"] >= s["feed_lag_p50_s"]


@pytest.mark.asyncio
async def test_a4_feed_lag_measured_even_for_unwatched_events():
    """The measurand is the PLATFORM feed lag — unwatched events count too."""
    import time as _t
    c, db = _make_collector()
    await c.on_rtds_trade(_trade(proxyWallet=UNWATCHED, timestamp=_t.time() - 4.0))
    assert c.get_stats()["feed_lag_n"] == 1
    db.insert_mirror_rejected_signal.assert_not_awaited()  # still not logged


@pytest.mark.asyncio
async def test_a4_garbage_or_absent_timestamp_never_breaks_ingest():
    import time as _t
    c, db = _make_collector()
    ev = _trade()
    ev.pop("transactionHash")
    for i, ts in enumerate(["not-a-number", None, -1e18, _t.time() + 900]):
        e = dict(ev, timestamp=ts)
        e["asset"] = f"tok-{i}"  # distinct composite dedup keys
        await c.on_rtds_trade(e)  # must not raise
    s = c.get_stats()
    # none of the garbage produced a sample (absent key -> _trade has no
    # timestamp by default in earlier tests, but here all four are bad values)
    assert s.get("feed_lag_n", 0) == 0
    assert db.insert_mirror_rejected_signal.await_count == 4  # ingest unaffected


# ── plumbing helper ──────────────────────────────────────────────────────────

def test_build_rtds_feed_wires_handler():
    c, _ = _make_collector()
    feed = None
    try:
        from mirror_v3.signal_collector import build_rtds_feed
        feed = build_rtds_feed(c, ping_interval=7, recv_timeout=30)
    except Exception as e:  # pragma: no cover - only if websockets missing
        pytest.skip(f"RTDSWebSocket unavailable: {e}")
    assert feed._handler == c.on_rtds_trade
    assert feed._ping_interval == 7
    assert feed._recv_timeout == 30
