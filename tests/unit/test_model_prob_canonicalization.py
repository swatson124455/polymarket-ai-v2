"""S245 — model_prob = P(YES) canonicalization regression matrix (BLOCKER fix #1).

Confirmed bug: the `model_prob` field had INCONSISTENT denomination across the 4
weather engines — temperature stored P(YES); wind/precip/snow stored P(side) for the
NO branch. The S-T/Kelly allocator (weather_bot.py:3282) and the executable-edge gate
(:4769) both derive P(side) for NO via (1.0 - model_prob), so the wind/precip/snow NO
branches DOUBLE-inverted -> p became P(YES) for a NO bet -> the `p <= price` guard
dropped essentially every such NO opportunity. GATED-BY-CONFIG: masked today by
thin_market_filter (those markets don't trade), fires on a MIN_LIQ change.

Fix = canonicalize model_prob = P(YES) at the producers:
  - wind   bots/weather_bot.py:2579                        round(1.0-model_prob,4) -> round(model_prob,4)
  - precip base_engine/weather/precipitation_engine.py:227 1.0-model_prob -> model_prob   (top-level)
  - precip bots/weather/engine/.../precipitation_engine.py:227 (runtime-live silo copy; snow reuses this)
plus an engine-boundary assertion at the allocator fan-in (weather_bot.py:3282).

`confidence` is a DIFFERENT field that correctly stays P(side) everywhere and is NOT
touched by this fix.

Matrix: each engine's NO-branch producer must emit model_prob = P(YES) (so the same
model state sizes identically across engines), and the boundary assertion must fire on
a P(side) regression. NEGATIVE CONTROL: revert any producer edit and the corresponding
engine test fails; remove the assertion and test_regressed_pside_no_opp_raises fails.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bots.weather_bot import WeatherBot
from bots.weather.engine.base_engine.weather.probability_engine import WeatherProbabilityEngine
from bots.weather.engine.base_engine.weather.precipitation_engine import (
    PrecipitationProbabilityEngine,
    PrecipBucket,
)


def _mock_engine():
    engine = MagicMock()
    engine.trade_coordinator = None
    engine.cache = None
    engine.db = None
    engine.risk_manager = MagicMock()
    engine.order_gateway = MagicMock()
    engine.order_gateway._open_position_markets = {"WeatherBot": set()}
    engine.get_all_tradeable_markets = AsyncMock(return_value=[])
    return engine


P_YES = 0.30        # model: 30% YES => NO is underpriced => NO opp expected
YES_PRICE = 0.55    # market YES price; NO-token price = 0.45


# ── Producer matrix: every engine's NO branch must emit model_prob = P(YES) ──

class TestProducersEmitPYesForNo:
    """Same model state (P(YES)=0.30) must yield model_prob=P(YES) for the NO opp on
    every engine, so the allocator/gate (which derive P(side) via 1-model_prob) size
    NO bets identically regardless of engine."""

    def test_temperature_no_branch_is_pyes(self):
        """CONTROL: temperature already stored P(YES) un-inverted. Passes pre- and
        post-fix; pins the canonical convention the other engines migrate to."""
        edges = WeatherProbabilityEngine().compute_edges(
            {"m1": P_YES}, {"m1": YES_PRICE}
        )
        no = [e for e in edges if e["side"] == "NO"]
        assert no, "expected a NO opp (model 0.30 < market 0.55)"
        assert no[0]["model_prob"] == pytest.approx(P_YES, abs=1e-4), (
            "temperature NO model_prob must be P(YES)=0.30 (un-inverted)"
        )

    def test_precipitation_no_branch_is_pyes(self):
        """FIX: precip NO branch must emit P(YES), not P(side). (Snow reuses this engine.)"""
        bucket = PrecipBucket(
            market_id="m1", token_id="t_yes", no_token_id="t_no",
            yes_price=YES_PRICE, bucket_type="range", low_bound=1.0, high_bound=2.0,
        )
        edges = PrecipitationProbabilityEngine().compute_edges(
            {"m1": P_YES}, [bucket], min_edge=0.08
        )
        no = [e for e in edges if e["side"] == "NO"]
        assert no, "expected a NO opp (no_edge = 0.70-0.45 = 0.25 > 0.08)"
        assert no[0]["model_prob"] == pytest.approx(P_YES, abs=1e-4), (
            "precip NO model_prob must be P(YES)=0.30 (the canonicalization fix)"
        )
        # confidence is the SEPARATE P(side) field and MUST remain inverted
        assert no[0]["confidence"] == pytest.approx(1.0 - P_YES, abs=1e-4), (
            "precip NO confidence must stay P(side)=0.70 — do NOT 'fix' it to match"
        )
        assert no[0]["price"] == pytest.approx(1.0 - YES_PRICE, abs=1e-4)

    def test_snow_routes_through_canonical_precip_engine(self):
        """Snow has NO separate producer; _analyze_snowfall_group reuses the
        precipitation engine, so the precip fix canonicalizes snow simultaneously."""
        bot = WeatherBot(_mock_engine())
        assert isinstance(bot._precip_engine, PrecipitationProbabilityEngine), (
            "snow must route through the (now-canonical) PrecipitationProbabilityEngine"
        )

    @pytest.mark.asyncio
    async def test_wind_no_branch_is_pyes(self):
        """FIX: wind NO branch (inline producer) must emit P(YES). Driven through the
        real _analyze_wind_group with a mocked ensemble (P(wind>=50) ~ 0 => strong NO)."""
        bot = WeatherBot(_mock_engine())
        bot._forecast_client = MagicMock()
        # mean ~10, std ~1.4 — P(wind >= 50) ~ 0 => P(YES) ~ 0 => NO opp
        bot._forecast_client.get_wind_ensemble = AsyncMock(return_value=[8, 9, 10, 11, 12] * 4)
        bot._get_min_edge = lambda *a, **k: 0.05
        bot._log_weather_prediction = AsyncMock()  # avoid DB
        bucket = SimpleNamespace(
            market_id="m1", token_id="t_yes", no_token_id="t_no", yes_price=YES_PRICE,
            bucket_type="at_or_higher", low_bound=50.0, high_bound=None,
        )
        group = SimpleNamespace(
            station="KTST", target_date=date(2026, 6, 14), city="Testville", buckets=[bucket],
        )
        opps = await bot._analyze_wind_group(group)
        no = [o for o in opps if o["side"] == "NO"]
        assert no, "expected a NO wind opp (P(wind>=50) ~ 0 << yes_price 0.55)"
        o = no[0]
        # Post-fix: model_prob=P(YES), confidence=P(side); for a NO opp P(YES) < P(NO).
        # Pre-fix the producer stored P(side) so model_prob == confidence (both fail below).
        assert o["model_prob"] < o["confidence"], (
            "wind NO model_prob must be P(YES) (< P(side) confidence); equality = pre-fix P(side)"
        )
        assert o["model_prob"] == pytest.approx(1.0 - o["confidence"], abs=0.011), (
            "wind NO model_prob must equal 1 - confidence (P(YES) vs P(side))"
        )


# ── Consumer + engine-boundary assertion at the Kelly fan-in ──

class TestAllocatorBoundaryInvariant:
    BUDGET = 1000.0

    def _no_opp(self, model_prob, price_no=0.45, market_id="m1"):
        return {
            "market_id": market_id, "token_id": "t_no", "side": "NO",
            "edge": 0.25, "price": price_no, "model_prob": model_prob,
            "market_prob": 1.0 - price_no, "abs_edge": 0.25,
            "confidence": 1.0 - model_prob, "market_type": "precipitation",
        }

    def test_canonical_no_opp_is_sized(self):
        """A canonical NO opp (model_prob=P(YES)=0.30, NO price 0.45) -> p=P(NO)=0.70 >
        price -> SIZED. This is the trade the bug used to drop."""
        opp = self._no_opp(model_prob=0.30)
        alloc = WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)
        assert alloc.get("m1", 0.0) > 0.0, (
            "canonical NO opp must receive an allocation (was dropped pre-fix)"
        )

    def test_regressed_pside_no_opp_raises(self):
        """The boundary assertion must FIRE on a producer regression: a NO opp whose
        model_prob is P(side)=0.70 (the pre-fix value) exceeds the implied P(YES) price
        (1-0.45=0.55) and trips the canary instead of silently dropping. NEGATIVE
        CONTROL for the assertion — fails if the assertion is removed."""
        opp = self._no_opp(model_prob=0.70)  # P(side), the pre-fix regression value
        with pytest.raises(AssertionError, match="denomination"):
            WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)

    def test_model_prob_out_of_range_raises(self):
        opp = self._no_opp(model_prob=1.5)
        with pytest.raises(AssertionError, match=r"out of \[0,1\]"):
            WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)

    def test_yes_opp_unaffected(self):
        """YES opp (model_prob=P(YES)=0.72 > price 0.55) sizes normally, no assertion."""
        opp = {
            "market_id": "m1", "token_id": "t_yes", "side": "YES",
            "edge": 0.17, "price": 0.55, "model_prob": 0.72,
            "market_prob": 0.55, "abs_edge": 0.17, "confidence": 0.72,
            "market_type": "temperature",
        }
        alloc = WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)
        assert alloc.get("m1", 0.0) > 0.0


class TestCalibratedEdgeAllocator:
    """S247: WEATHER_CALIBRATED_EDGE_ENABLED makes the S-T allocator size Kelly on the
    CALIBRATED P(side) (opp["confidence"]) instead of the raw over-dispersed model_prob.
    Backtest (S247): the raw temp model is over-dispersed (OOS AUC 0.54) → sizing on raw
    model_prob manufactures fake edges. Default OFF; these are negative-controlled."""

    BUDGET = 1000.0

    def _no_opp(self, model_prob, confidence, price_no=0.55):
        # NO opp: model_prob=P(YES) (passes the denomination canary since P(YES) < 1-price);
        # raw p=P(NO)=1-model_prob, calibrated p=confidence.
        return {
            "market_id": "m1", "token_id": "t_no", "side": "NO",
            "edge": 0.1, "price": price_no, "model_prob": model_prob,
            "market_prob": 1.0 - price_no, "abs_edge": 0.1,
            "confidence": confidence, "market_type": "temperature",
        }

    def test_flag_off_sizes_on_raw_model_prob(self, monkeypatch):
        """Flag OFF: raw p = 1-0.40 = 0.60 > price 0.55 → SIZED, even though the calibrated
        confidence (0.52 ≤ 0.55) would drop it. Negative control for the flag."""
        import bots.weather_bot as _wb
        monkeypatch.setattr(_wb.settings, "WEATHER_CALIBRATED_EDGE_ENABLED", False)
        opp = self._no_opp(model_prob=0.40, confidence=0.52)
        alloc = WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)
        assert alloc.get("m1", 0.0) > 0.0

    def test_flag_on_drops_when_calibrated_below_price(self, monkeypatch):
        """Flag ON: calibrated p = confidence 0.52 ≤ price 0.55 → DROPPED (raw 0.60 would
        have sized). Proves Kelly now keys off the calibrated prob."""
        import bots.weather_bot as _wb
        monkeypatch.setattr(_wb.settings, "WEATHER_CALIBRATED_EDGE_ENABLED", True)
        opp = self._no_opp(model_prob=0.40, confidence=0.52)
        alloc = WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)
        assert alloc.get("m1", 0.0) == 0.0

    def test_flag_on_boundary_assertion_still_fires(self, monkeypatch):
        """The model_prob denomination canary must stay active with the flag ON (it guards
        the producer, independent of which prob Kelly sizes on)."""
        import bots.weather_bot as _wb
        monkeypatch.setattr(_wb.settings, "WEATHER_CALIBRATED_EDGE_ENABLED", True)
        opp = self._no_opp(model_prob=0.70, confidence=0.30)  # P(side) regression value
        with pytest.raises(AssertionError, match="denomination"):
            WeatherBot._smoczynski_tomkins_allocate([opp], self.BUDGET)
