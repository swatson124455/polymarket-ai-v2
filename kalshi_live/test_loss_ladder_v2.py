"""TIERED LOSS LADDER (E: "$3 then $5 then out") + TAKER-CROSS BEHAVIORAL GOVERNOR (A) +
25%-of-capital FAMILY CAP (H) — operator-named 2026-07-31."""
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _pos(t="T1", pos="0.50", realized="0.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "0.25",
            "realized_pnl_dollars": realized}


def _fp1(monkeypatch):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


def _state(tmp_path):
    p = os.path.join(str(tmp_path), "quoter_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _arm(monkeypatch):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    monkeypatch.setattr(q, "MKT_OUT_LOSS_USD", 5.0)


class TestTieredLadder:
    def test_three_dollar_day_loss_is_exit_only_not_out(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        c2 = MockClient(mode="live", positions=[_pos(realized="-3.50")])
        row2 = _run(monkeypatch, c2, str(tmp_path))
        assert row2.get("loss_exitonly") == 1 and c2.created == []
        st = _state(tmp_path)
        assert st.get("mkt_out") == [], "a $3-4.99 day is exit-only, NOT a permanent ban"
        assert "T1" in (st.get("mkt_strike_hist") or {}), "the $3 rung records a strike"

    def test_five_dollar_day_loss_is_out_forever(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        row2 = _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-6.00")]),
                    str(tmp_path))
        assert row2.get("mkt_out") == 1
        assert _state(tmp_path).get("mkt_out") == ["T1"]
        # NEXT DAY: realized day-delta resets, the ban must NOT
        st = _state(tmp_path)
        st["mkt_realized_day"] = "2020-01-01"           # force a day roll
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump(st, fh)
        c3 = MockClient(mode="live", positions=[_pos(realized="-6.00")])
        row3 = _run(monkeypatch, c3, str(tmp_path))
        assert row3.get("loss_exitonly") == 0, "fresh day, no new trip"
        assert c3.created == [], "an OUT market must never quote again"
        assert _state(tmp_path).get("mkt_out") == ["T1"]

    def test_grandfather_migration_bans_prior_struck_markets(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_strike_hist": {"T1": ["2026-07-30", "2026-07-31"]}}, fh)
        c = MockClient(mode="live", positions=[_pos(realized="0.00")])
        _run(monkeypatch, c, str(tmp_path))
        assert c.created == [], "one-strike-out era bans are grandfathered"
        assert _state(tmp_path).get("mkt_out") == ["T1"]

    def test_migration_runs_once_operator_clear_sticks(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        # operator cleared mkt_out but the old strike hist remains: an EXISTING (even empty)
        # mkt_out key must be respected, never re-seeded from history
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_strike_hist": {"T1": ["2026-07-30"]}, "mkt_out": []}, fh)
        c = MockClient(mode="live", positions=[_pos(realized="0.00")])
        _run(monkeypatch, c, str(tmp_path))
        assert len(c.created) > 0, "operator clear must not be undone by re-migration"


class TestTakerCrossGovernor:
    def test_three_paid_exits_plus_loss_trips(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setattr(q, "TAKER_GOV_CROSSES", 3)
        monkeypatch.setattr(q, "TAKER_GOV_LOSS_USD", 1.0)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        st = _state(tmp_path)
        st["mkt_taker_xn"] = {"T1": 3}
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump(st, fh)
        # realized only -$1.50: under the $3 rung, but 3 paid exits + loss -> behavioral trip
        c2 = MockClient(mode="live", positions=[_pos(realized="-1.50")])
        row2 = _run(monkeypatch, c2, str(tmp_path))
        assert row2.get("loss_exitonly") == 1, "taker>=3 AND real<=-$1 must trip"
        assert c2.created == []

    def test_paid_exits_without_loss_do_not_trip(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        monkeypatch.setattr(q, "TAKER_GOV_CROSSES", 3)
        monkeypatch.setattr(q, "TAKER_GOV_LOSS_USD", 1.0)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        st = _state(tmp_path)
        st["mkt_taker_xn"] = {"T1": 5}
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump(st, fh)
        c2 = MockClient(mode="live", positions=[_pos(realized="-0.20")])
        row2 = _run(monkeypatch, c2, str(tmp_path))
        assert row2.get("loss_exitonly") == 0, "paid exits at ~break-even are not toxicity"
        assert len(c2.created) > 0

    def test_taker_counter_resets_at_day_roll(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_realized_day": "2020-01-01", "mkt_taker_xn": {"T1": 7}}, fh)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        assert _state(tmp_path).get("mkt_taker_xn") == {}, "paid-exit counts are per-day"

    def test_cross_capped_feeds_the_counter(self, monkeypatch):
        monkeypatch.setattr(q, "public_get", lambda p: {
            "orderbook_fp": {"yes_dollars": [["0.50", "500"]],
                             "no_dollars": [["0.45", "500"]]}})
        q._TAKER_XN.clear()
        c = MockClient(mode="live", positions=[_pos(pos="10.00")])
        q._taker_cross_capped(c, "T1", 10, True, tries=1)
        assert q._TAKER_XN.get("T1") == 1, "every filled IOC pass counts one paid exit"
        q._TAKER_XN.clear()


class TestFamilyCap25Pct:
    def test_series_cap_is_quarter_of_capital(self, monkeypatch):
        monkeypatch.setattr(q, "SERIES_MAX_USD", 100.0)
        monkeypatch.setattr(q, "SERIES_PCT", 0.25)
        q._TOTAL_CAP_EFF[0] = 311.0
        try:
            assert abs(q._series_cap() - 77.75) < 1e-9
            q._TOTAL_CAP_EFF[0] = 500.0
            assert q._series_cap() == 100.0, "env stays a static ceiling"
            q._TOTAL_CAP_EFF[0] = None
            assert q._series_cap() == 100.0, "no equity observed -> env alone"
            monkeypatch.setattr(q, "SERIES_PCT", 0.0)
            q._TOTAL_CAP_EFF[0] = 311.0
            assert q._series_cap() == 100.0, "PCT=0 -> static env cap only"
        finally:
            q._TOTAL_CAP_EFF[0] = None

    def test_dynamic_cap_gates_entry(self, monkeypatch):
        """cap_desired must skip a sibling once the family holds 25% of capital — the
        operator-asked verification that the family cap sits in the ENTER formula."""
        monkeypatch.setattr(q, "SERIES_MAX_USD", 0.0)   # no static ceiling: pure 25% rule
        monkeypatch.setattr(q, "SERIES_PCT", 0.25)
        monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", 10000.0)
        q._TOTAL_CAP_EFF[0] = 100.0                     # capital $100 -> family budget $25
        try:
            desired = {
                "FAM-A": [{"side": "yes", "price_dollars": 0.50, "count": 40, "reason": "join"}],
                "FAM-B": [{"side": "yes", "price_dollars": 0.50, "count": 40, "reason": "join"}],
            }
            kept, _ = q.cap_desired(desired, {"FAM-A": 100.0, "FAM-B": 90.0})
            assert "FAM-A" in kept and "FAM-B" not in kept, \
                "second $20 sibling must be skipped once the $25 family budget is full"
        finally:
            q._TOTAL_CAP_EFF[0] = None


class TestM3TripSnapshot:
    """M3 (operator 2026-07-31): "$3 timeout, then open up, and if $5 then out for good" --
    losses realized DURING the timeout (our forced exits) never escalate to the $5 rung;
    a one-shot burn already at/past $5 at trip time still goes OUT immediately."""

    def test_timeout_exits_do_not_escalate_to_permanent(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        # cycle 2: -$3.20 -> timeout (snapshot frozen at -3.20)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-3.20")]),
             str(tmp_path))
        # cycle 3: forced exits realize down to -$6 WHILE tripped -> must NOT go permanent
        row3 = _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-6.00")]),
                    str(tmp_path))
        assert row3.get("loss_exitonly") == 1
        assert _state(tmp_path).get("mkt_out") == [],             "exit costs during the timeout must not trigger the permanent ban"

    def test_one_shot_burn_still_goes_out_immediately(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-25.00")]),
             str(tmp_path))
        assert _state(tmp_path).get("mkt_out") == ["T1"],             "a burn already past $5 at trip time is OUT (MLABELSHARE class)"

    def test_after_reopen_fresh_counting_can_ban(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-3.20")]),
             str(tmp_path))
        st = _state(tmp_path)
        st["mkt_realized_day"] = "2020-01-01"            # timeout expires: day rolls
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump(st, fh)
        # day-roll cycle re-baselines at the current lifetime value (-3.20)...
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-3.20")]),
             str(tmp_path))
        # ...then the reopened market burns a fresh -$5.10 -> out for good
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-8.30")]),
             str(tmp_path))
        assert _state(tmp_path).get("mkt_out") == ["T1"],             "post-reopen losses count fresh: $5 after reopening = out for good"

    def test_snapshots_reset_at_day_roll(self, monkeypatch, tmp_path):
        _arm(monkeypatch)
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_realized_day": "2020-01-01",
                       "mkt_trip_snap": {"T9": -3.2}}, fh)
        _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]),
             str(tmp_path))
        assert _state(tmp_path).get("mkt_trip_snap") == {},             "trip snapshots are per-day state and must not accumulate"
