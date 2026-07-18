"""S232 Phase-2 defect tests — PWS-mesh nowcast signal.

Covers: debias application (block vs scalar), table staleness, local-day/qc
filtering, consensus min-PWS rule, crossing/overshoot conventions, flag-off
no-ops, entry admission (repriced / dropped-city / E_rem blocks), the $50
window cap surviving flat-sizing mode, and the overshoot SELL chain.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from bots.weather.engine.base_engine.weather import nowcast_mesh
from bots.weather.engine.base_engine.weather.market_mapper import (
    TemperatureBucket,
    WeatherMarketGroup,
)
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
from bots.weather.engine.config.settings import settings

KLGA = next(s for s in STATION_REGISTRY.values() if s.station_id == "KLGA")
TZ = ZoneInfo(KLGA.timezone)


# ── pure functions ──────────────────────────────────────────────────────────

def test_f_to_native_units():
    assert nowcast_mesh.f_to_native(212.0, "F") == 212.0
    assert abs(nowcast_mesh.f_to_native(212.0, "C") - 100.0) < 1e-9
    assert abs(nowcast_mesh.f_delta_to_native(1.0, "C") - 5.0 / 9.0) < 1e-9
    assert nowcast_mesh.f_delta_to_native(1.0, "F") == 1.0


def test_pws_offset_prefers_block_falls_back_scalar():
    entry = {"scalar": 2.0, "blocks": {"afternoon": [-0.5, 12]}}
    assert nowcast_mesh.pws_offset(entry, 14) == -0.5    # afternoon block
    assert nowcast_mesh.pws_offset(entry, 9) == 2.0      # morning → scalar
    assert nowcast_mesh.pws_offset(entry, 3) == 2.0      # outside blocks → scalar


def _write_table(tmp_path, generated=None, dropped=False, pws=None):
    doc = {
        "generated_utc": (generated or datetime.utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trail_days": 5,
        "drop_sd_f": 1.5,
        "cities": {
            "KLGA": {
                "pws": pws or {
                    "PWS1": {"n": 10, "scalar": 1.0, "blocks": {}},
                    "PWS2": {"n": 9, "scalar": -1.0, "blocks": {}},
                },
                "n_matched": 19,
                "residual_sd": 0.8,
                "dropped": dropped,
            }
        },
    }
    (tmp_path / "mesh_debias.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def test_load_debias_table_fresh_stale_missing(tmp_path):
    assert nowcast_mesh.load_debias_table(str(tmp_path)) is None       # missing
    _write_table(tmp_path)
    assert nowcast_mesh.load_debias_table(str(tmp_path)) is not None   # fresh
    _write_table(tmp_path, generated=datetime.utcnow() - timedelta(days=3))
    assert nowcast_mesh.load_debias_table(str(tmp_path)) is None       # stale


def _write_mesh(tmp_path, rows, local_date):
    path = tmp_path / f"pws_mesh_{local_date.strftime('%Y%m%d')}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_load_mesh_day_filters_sid_qc_and_local_date(tmp_path):
    local_date = datetime.now(TZ).date()
    ep = int(datetime.now(TZ).timestamp()) - 300
    # epoch on the WRONG local day (yesterday)
    ep_wrong = int((datetime.now(TZ) - timedelta(days=1)).timestamp())
    rows = [
        {"sid": "KLGA", "pws": "PWS1", "epoch": ep, "temp_f": 85.0, "qc": 1},
        {"sid": "KLGA", "pws": "PWS2", "epoch": ep, "temp_f": 83.0, "qc": 0},   # bad qc
        {"sid": "KORD", "pws": "PWS3", "epoch": ep, "temp_f": 70.0, "qc": 1},   # other sid
        {"sid": "KLGA", "pws": "PWS4", "epoch": ep_wrong, "temp_f": 99.0, "qc": 1},
    ]
    _write_mesh(tmp_path, rows, local_date)
    out = nowcast_mesh.load_mesh_day(str(tmp_path), "KLGA", TZ, local_date)
    assert out == [(ep, 85.0, "PWS1")]


def test_debiased_runmax_applies_offsets_and_min_pws(tmp_path):
    table = _write_table(tmp_path)
    city_tab = table["cities"]["KLGA"]
    ep = int(time.time()) - 120
    # PWS1 raw 85 (offset +1 → 84), PWS2 raw 83 (offset −1 → 84) → consensus 84
    series = [(ep, 85.0, "PWS1"), (ep, 83.0, "PWS2")]
    state = nowcast_mesh.debiased_runmax(series, city_tab, TZ, min_pws=2)
    assert state is not None
    assert abs(state["runmax_f"] - 84.0) < 1e-9
    assert state["last_n_pws"] == 2
    # a single-PWS bin never reaches min_pws → None
    assert nowcast_mesh.debiased_runmax([(ep, 85.0, "PWS1")], city_tab, TZ, min_pws=2) is None
    # unknown PWS (not in debias table) must NOT contribute
    assert nowcast_mesh.debiased_runmax(
        [(ep, 85.0, "PWS1"), (ep, 120.0, "GHOST")], city_tab, TZ, min_pws=2) is None


def test_bucket_crossed_and_overshot_conventions():
    # rounded-degree convention (round()): 83.6 → 84 enters 84-85
    assert nowcast_mesh.bucket_crossed(83.6, "range", 84.0, 85.0)
    assert not nowcast_mesh.bucket_crossed(83.4, "range", 84.0, 85.0)
    assert nowcast_mesh.bucket_crossed(84.2, "at_or_higher", 84.0, None)
    assert not nowcast_mesh.bucket_crossed(83.0, "at_or_higher", 84.0, None)
    assert not nowcast_mesh.bucket_crossed(84.0, "at_or_below", None, 84.0)  # unsupported type
    # overshoot: rounded max ABOVE high bound; only meaningful for range
    assert nowcast_mesh.bucket_overshot(85.6, "range", 85.0)
    assert not nowcast_mesh.bucket_overshot(85.4, "range", 85.0)
    assert not nowcast_mesh.bucket_overshot(99.0, "at_or_higher", None)


# ── bot integration ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.trade_coordinator = None
    engine.cache = None
    engine.db = None
    engine.risk_manager = MagicMock()
    engine.risk_manager.check_hard_stop_loss = MagicMock(
        return_value={"should_exit": False, "reason": "", "details": {}})
    engine.order_gateway = MagicMock()
    engine.order_gateway._open_position_markets = {"WeatherBot": set()}
    engine.order_gateway._position_details = {}
    engine.get_all_tradeable_markets = AsyncMock(return_value=[])
    engine.place_order = AsyncMock(return_value={"success": True, "order_id": "t1"})
    return engine


@pytest.fixture
def weather_bot(mock_engine):
    from bots.weather_bot import WeatherBot
    return WeatherBot(mock_engine)


def _mk_group(local_date, yes_price=0.30, low=84.0, high=85.0):
    bucket = TemperatureBucket(
        market_id="m-nowcast-1", token_id="tok-yes", no_token_id="tok-no",
        yes_price=yes_price, bucket_type="range",
        low_bound=low, high_bound=high, temp_unit="F",
    )
    return WeatherMarketGroup(
        city="New York", target_date=local_date, station=KLGA,
        buckets=[bucket], temp_unit="F",
    )


def _forecast(det_high):
    fc = MagicMock()
    fc.deterministic_high = det_high
    fc.model_spread = 1.5
    fc.ensemble_members = [det_high] * 30
    fc.forecast_delta = 0.0
    fc.lead_time_hours = 4.0
    return fc


def _feed(tmp_path, dropped=False):
    """Fresh debias table + a 2-PWS mesh bin consensus of 84.0F, now-ish."""
    _write_table(tmp_path, dropped=dropped)
    local_date = datetime.now(TZ).date()
    ep = int(time.time()) - 120
    _write_mesh(tmp_path, [
        {"sid": "KLGA", "pws": "PWS1", "epoch": ep, "temp_f": 85.0, "qc": 1},
        {"sid": "KLGA", "pws": "PWS2", "epoch": ep, "temp_f": 83.0, "qc": 1},
    ], local_date)
    return local_date


def _nowcast_env(monkeypatch, tmp_path, enabled=True):
    monkeypatch.setattr(settings, "WEATHER_NOWCAST_ENTRY_ENABLED", enabled, raising=False)
    monkeypatch.setattr(settings, "WEATHER_NOWCAST_FEED_DIR", str(tmp_path), raising=False)
    # neutralize wall-clock coupling: any local hour qualifies in tests
    monkeypatch.setattr(settings, "WEATHER_NOWCAST_MIN_LOCAL_HOUR", 0, raising=False)


@pytest.mark.asyncio
async def test_nowcast_scan_flag_off_is_noop(weather_bot, monkeypatch, tmp_path):
    _nowcast_env(monkeypatch, tmp_path, enabled=False)
    weather_bot._execute_weather_trade = AsyncMock()
    local_date = _feed(tmp_path)
    analyzed = [([], _mk_group(local_date), {})]
    await weather_bot._scan_nowcast_entries(analyzed)
    await weather_bot._evaluate_nowcast_invalidations(analyzed)
    weather_bot._execute_weather_trade.assert_not_called()
    weather_bot.base_engine.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_nowcast_scan_builds_capped_yes_entry(weather_bot, monkeypatch, tmp_path):
    _nowcast_env(monkeypatch, tmp_path)
    local_date = _feed(tmp_path)
    weather_bot._forecast_client.get_combined_forecast = AsyncMock(return_value=_forecast(84.4))
    weather_bot._execute_weather_trade = AsyncMock(return_value=True)
    await weather_bot._scan_nowcast_entries([([], _mk_group(local_date), {})])
    weather_bot._execute_weather_trade.assert_awaited_once()
    opp = weather_bot._execute_weather_trade.await_args.args[0]
    assert opp["side"] == "YES"                       # entry mandate: never BUY/SELL
    assert opp["model_override"] == "weather_nowcast_peak"
    assert opp["_st_size_override"] == 50.0           # full window remaining
    assert opp["_nowcast_window_remaining"] == 50.0
    assert opp["price"] == 0.30
    assert abs(opp["edge"] - (0.44 - 0.30)) < 1e-9


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["repriced", "dropped", "erem", "not_crossed"])
async def test_nowcast_scan_admission_blocks(case, weather_bot, monkeypatch, tmp_path):
    _nowcast_env(monkeypatch, tmp_path)
    local_date = _feed(tmp_path, dropped=(case == "dropped"))
    det_high = 87.5 if case == "erem" else 84.4       # E_rem 3.5F blocks
    yes_price = 0.50 if case == "repriced" else 0.30  # > 0.44 - 0.05 blocks
    low, high = (86.0, 87.0) if case == "not_crossed" else (84.0, 85.0)
    weather_bot._forecast_client.get_combined_forecast = AsyncMock(return_value=_forecast(det_high))
    weather_bot._execute_weather_trade = AsyncMock(return_value=True)
    await weather_bot._scan_nowcast_entries(
        [([], _mk_group(local_date, yes_price=yes_price, low=low, high=high), {})])
    weather_bot._execute_weather_trade.assert_not_called()


@pytest.mark.asyncio
async def test_nowcast_window_cap_survives_flat_mode(weather_bot, mock_engine, monkeypatch, tmp_path):
    """The $50 window cap must hold even though WEATHER_FLAT_SIZE_USD=100 is
    the deployed default (flat mode consumes-and-ignores _st_size_override)."""
    _nowcast_env(monkeypatch, tmp_path)
    local_date = _feed(tmp_path)
    group = _mk_group(local_date)
    # spread/executable-edge gates need a market-index entry
    mock_engine.order_gateway._market_index = {
        "m-nowcast-1": {"bestBid": 0.29, "bestAsk": 0.31, "volume": 100.0}}
    weather_bot.place_order = AsyncMock(return_value={"success": True, "order_id": "t2"})
    weather_bot._should_halt_severe_weather = MagicMock(return_value=None)
    weather_bot._get_severe_weather_boost = AsyncMock(return_value=1.0)
    opp = {
        "market_id": "m-nowcast-1", "token_id": "tok-yes", "side": "YES",
        "price": 0.30, "confidence": 0.44, "raw_confidence": 0.44,
        "model_prob": 0.44, "edge": 0.14, "abs_edge": 0.14,
        "city": "New York", "target_date": local_date.isoformat(),
        "lead_time_hours": 4.0, "ensemble_mean": 84.4, "model_spread": 1.5,
        "ensemble_count": 30, "resolution_boundary_risk": False,
        "market_type": "temperature", "forecast_delta": 0.0,
        "nbm_high_conviction": False, "bucket_type": "range",
        "model_override": "weather_nowcast_peak",
        "_st_size_override": 50.0, "_nowcast_window_remaining": 50.0,
    }
    ok = await weather_bot._execute_weather_trade(opp, group)
    assert ok is True
    weather_bot.place_order.assert_awaited_once()
    kwargs = weather_bot.place_order.await_args.kwargs
    assert kwargs["side"] == "YES"
    usd = kwargs["size"] * kwargs["price"]            # shares → USD
    assert usd <= 50.0 + 1e-6                         # cap held (flat = $100!)
    assert usd > 0


@pytest.mark.asyncio
async def test_nowcast_invalidation_sells_on_overshoot(weather_bot, mock_engine, monkeypatch, tmp_path):
    _nowcast_env(monkeypatch, tmp_path)
    local_date = _feed(tmp_path)                       # debiased runmax 84.0
    group = _mk_group(local_date, low=82.0, high=83.0)  # 84 > 83 → overshot
    cache = MagicMock()
    cache.redis = MagicMock()
    cache.redis.keys = AsyncMock(return_value=["weatherbot:nowcast_pos:m-nowcast-1"])
    cache.redis.delete = AsyncMock()
    cache.get = AsyncMock(return_value={
        "token_id": "tok-yes", "sid": "KLGA", "high_bound": 83.0,
        "bucket_type": "range", "temp_unit": "F",
        "target_date": local_date.isoformat()})
    cache.set = AsyncMock()
    mock_engine.cache = cache
    mock_engine.order_gateway._position_details = {
        "WeatherBot:m-nowcast-1": {"side": "YES", "price": 0.30, "size": 100.0}}
    await weather_bot._evaluate_nowcast_invalidations([([], group, {})])
    mock_engine.place_order.assert_awaited_once()
    kwargs = mock_engine.place_order.await_args.kwargs
    assert kwargs["side"] == "SELL"                    # exits SELL the held token
    assert kwargs["token_id"] == "tok-yes"
    assert kwargs["size"] == 100.0
    assert weather_bot._exit_reasons["m-nowcast-1"] == "NOWCAST_OVERSHOOT"
    assert "m-nowcast-1" in weather_bot._recently_exited
    cache.redis.delete.assert_awaited()                # tracking dropped on fill


@pytest.mark.asyncio
async def test_nowcast_invalidation_holds_without_fresh_price(weather_bot, mock_engine, monkeypatch, tmp_path):
    """Market absent from the current scan → no fresh price → HOLD, retry later."""
    _nowcast_env(monkeypatch, tmp_path)
    local_date = _feed(tmp_path)
    cache = MagicMock()
    cache.redis = MagicMock()
    cache.redis.keys = AsyncMock(return_value=["weatherbot:nowcast_pos:m-nowcast-1"])
    cache.redis.delete = AsyncMock()
    cache.get = AsyncMock(return_value={
        "token_id": "tok-yes", "sid": "KLGA", "high_bound": 83.0,
        "bucket_type": "range", "temp_unit": "F",
        "target_date": local_date.isoformat()})
    mock_engine.cache = cache
    mock_engine.order_gateway._position_details = {
        "WeatherBot:m-nowcast-1": {"side": "YES", "price": 0.30, "size": 100.0}}
    await weather_bot._evaluate_nowcast_invalidations([])   # empty scan
    mock_engine.place_order.assert_not_called()
