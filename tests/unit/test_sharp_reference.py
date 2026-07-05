"""Sharp-line reference tests — no-vig math, point-in-time pick, gate rule, parser."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bots.mirror_backtest.sharp_reference import (
    LineEntry, OddsPapiClient, make_sharp_edge_rule, no_vig_two_way,
    pick_point_in_time, whale_side_sharp_prob,
)


class TestNoVig:
    def test_removes_overround_sums_to_one(self):
        a, b = no_vig_two_way(1.90, 2.10)
        assert a + b == pytest.approx(1.0)
        assert a > b  # shorter odds => higher prob

    def test_fair_coin(self):
        a, b = no_vig_two_way(2.0, 2.0)
        assert a == pytest.approx(0.5) and b == pytest.approx(0.5)

    def test_degenerate_odds_none(self):
        assert no_vig_two_way(1.0, 2.0) is None
        assert no_vig_two_way(0.5, 2.0) is None


class TestPointInTime:
    def _series(self):
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        return [LineEntry(1.8, t0 + timedelta(minutes=m)) for m in (0, 5, 10)]

    def test_picks_latest_at_or_before(self):
        s = self._series()
        got = pick_point_in_time(s, datetime(2026, 5, 1, 12, 7, 0))
        assert got.created_at == datetime(2026, 5, 1, 12, 5, 0)

    def test_exact_match_included(self):
        s = self._series()
        got = pick_point_in_time(s, datetime(2026, 5, 1, 12, 10, 0))
        assert got.created_at == datetime(2026, 5, 1, 12, 10, 0)

    def test_no_lookahead_before_first(self):
        s = self._series()
        assert pick_point_in_time(s, datetime(2026, 5, 1, 11, 59, 0)) is None

    def test_empty_none(self):
        assert pick_point_in_time([], datetime(2026, 5, 1)) is None


class TestWhaleSideMapping:
    def test_yes_side(self):
        assert whale_side_sharp_prob("YES", 0.62) == pytest.approx(0.62)

    def test_no_side(self):
        assert whale_side_sharp_prob("NO", 0.62) == pytest.approx(0.38)

    def test_home_is_no(self):
        # if the book's "home" maps to the market's NO token
        assert whale_side_sharp_prob("YES", 0.62, home_is_yes=False) == pytest.approx(0.38)

    def test_bad_side_none(self):
        assert whale_side_sharp_prob("BUY", 0.62) is None


class TestGateRule:
    def test_tails_when_sharp_says_cheap(self):
        rule = make_sharp_edge_rule(min_edge=0.03, fee=0.02)
        # whale paid 0.45, sharp implies 0.55 -> edge = .55-.45-.02 = .08 >= .03
        d = rule({"price": 0.45, "sharp_prob": 0.55})
        assert d is not None and d.size_usd > 0

    def test_skips_when_no_edge(self):
        rule = make_sharp_edge_rule(min_edge=0.03, fee=0.02)
        # whale paid 0.52, sharp 0.55 -> edge = .01 < .03
        assert rule({"price": 0.52, "sharp_prob": 0.55}) is None

    def test_skips_uncovered_signal(self):
        rule = make_sharp_edge_rule()
        assert rule({"price": 0.45}) is None            # no sharp_prob
        assert rule({"sharp_prob": 0.55}) is None        # no price

    def test_rule_never_sees_outcome(self):
        # sanity: the rule ignores any resolution field even if present
        rule = make_sharp_edge_rule(min_edge=0.03, fee=0.02)
        d = rule({"price": 0.45, "sharp_prob": 0.55, "resolution": "NO"})
        assert d is not None  # decision is edge-driven, not outcome-driven


class TestOddsPapiClient:
    def test_key_from_arg_not_hardcoded(self):
        assert OddsPapiClient(api_key="abc").configured is True
        assert OddsPapiClient(api_key="").configured is False

    def test_parse_line_series_known_shape(self):
        payload = {"players": {"pinnacle": [
            {"price": 1.80, "createdAt": "2026-05-01T12:00:00Z"},
            {"price": 1.85, "createdAt": "2026-05-01T12:05:00Z"},
            {"price": 0.5, "createdAt": "2026-05-01T12:06:00Z"},   # <=1.0 dropped
            {"price": 1.9, "createdAt": None},                      # no ts dropped
        ]}}
        series = OddsPapiClient.parse_line_series(payload)
        assert [e.price for e in series] == [1.80, 1.85]
        assert series[0].created_at < series[1].created_at

    def test_parse_empty_payload(self):
        assert OddsPapiClient.parse_line_series({}) == []
        assert OddsPapiClient.parse_line_series({"players": "bad"}) == []

    @pytest.mark.asyncio
    async def test_live_fetch_refuses_until_wired(self):
        with pytest.raises(NotImplementedError):
            await OddsPapiClient(api_key="x").fetch_historical_odds("cs2", "123")
