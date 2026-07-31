"""Wave 3 pins (operator-named 2026-07-31): I fill-cost feed refresh, J sweep veto +
trend cross, L3 series probe insurance. (K NETEV enable is env-only — gate behavior is
pinned in test_netev_gate.py; L1 dust exits already satisfied live by INV_TOLERANCE=1.)"""
import datetime as dt
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _pos(t="T1", pos="0.50", realized="0.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "0.25",
            "realized_pnl_dollars": realized}


def _state(tmp_path):
    return json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))


def _strand_setup(monkeypatch, tmp_path, extra_state=None):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 2.0)
    monkeypatch.setattr(q, "STRAND_CROSS_S", 15.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    monkeypatch.setattr(q, "SWEEP_VETO_TICKS", 3)
    old = (q.utcnow() - dt.timedelta(seconds=600)).isoformat()
    state = {"strand_grace": {"T1": old}}
    state.update(extra_state or {})
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump(state, fh)

    def _pg(p):
        if "orderbook" in p:
            return {"orderbook_fp": {"yes_dollars": [["0.40", "500"]],
                                     "no_dollars": [["0.55", "500"]]}}
        return {"incentive_programs": [], "next_cursor": ""}
    monkeypatch.setattr(q, "public_get", _pg)
    return MockClient(mode="live", positions=[_pos(pos="80.00")])


class TestSweepVetoAndTrendCross:
    def test_first_fast_move_defers_one_pass(self, monkeypatch, tmp_path):
        # prev exec-touch 0.50, current yes-bid 0.40 -> 10 ticks against long-yes -> SPIKE
        c = _strand_setup(monkeypatch, tmp_path,
                          {"strand_touch": {"T1": [0.50, 0]}})
        row = _run(monkeypatch, c, str(tmp_path))
        assert c.crosses == [], "a one-period spike must not be crossed into"
        assert row.get("exit_sweep_veto") == 1
        st = _state(tmp_path)
        assert st["strand_touch"]["T1"] == [0.40, 1], "fast-move streak recorded"
        stamp = st.get("strand_grace", {}).get("T1")
        assert stamp is not None, "clock must survive the veto: exit stays time-bounded"
        assert (q.utcnow() - q.parse_iso(stamp)).total_seconds() < 60,             "veto must RE-ARM the clock (a stale stamp would re-fire next cycle, not next period)"

    def test_second_consecutive_fast_move_crosses_immediately(self, monkeypatch, tmp_path):
        # streak already 1 and another 10-tick move -> TREND: pay now, skip the ladder
        c = _strand_setup(monkeypatch, tmp_path,
                          {"strand_touch": {"T1": [0.50, 1]}})
        row = _run(monkeypatch, c, str(tmp_path))
        assert len(c.crosses) >= 1, "a sustained trend must cross despite the expensive book"
        assert row.get("exit_trend_cross") == 1

    def test_quiet_book_unaffected(self, monkeypatch, tmp_path):
        # prev touch == current touch: no veto, normal calculator path (expensive -> ladder)
        c = _strand_setup(monkeypatch, tmp_path,
                          {"strand_touch": {"T1": [0.40, 0]}})
        row = _run(monkeypatch, c, str(tmp_path))
        assert c.crosses == [] and row.get("exit_sweep_veto") is None
        assert row.get("exit_ladder_stepped") == 1, "normal ladder path preserved"

    def test_disabled_restores_legacy(self, monkeypatch, tmp_path):
        c = _strand_setup(monkeypatch, tmp_path,
                          {"strand_touch": {"T1": [0.50, 0]}})
        monkeypatch.setattr(q, "SWEEP_VETO_TICKS", 0)
        row = _run(monkeypatch, c, str(tmp_path))
        assert row.get("exit_sweep_veto") is None, "0 = both arms off"


class TestSeriesProbeInsurance:
    def test_new_sibling_of_out_series_is_probe_sized(self, monkeypatch, tmp_path):
        _cfg(monkeypatch)
        monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": "KXBURN-NEW-1", "usd_day": 100.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"}])
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_out": ["KXBURN-OLD-1"]}, fh)
        c = MockClient(mode="live", positions=[])
        row = _run(monkeypatch, c, str(tmp_path))
        assert row.get("series_probe") == 1
        mine = [x for x in c.created if x["ticker"] == "KXBURN-NEW-1"]
        assert mine and all(x["count"] <= 5 for x in mine), \
            "fresh sibling of a burned series enters probe-sized"

    def test_unrelated_series_full_size(self, monkeypatch, tmp_path):
        _cfg(monkeypatch)
        monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": "KXOTHER-NEW-1", "usd_day": 100.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"}])
        with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
            json.dump({"mkt_out": ["KXBURN-OLD-1"]}, fh)
        c = MockClient(mode="live", positions=[])
        row = _run(monkeypatch, c, str(tmp_path))
        assert row.get("series_probe") is None
        mine = [x for x in c.created if x["ticker"] == "KXOTHER-NEW-1"]
        assert mine and any(x["count"] > 5 for x in mine)


class TestFillCostRefresh:
    def test_stale_feed_refreshes_and_fresh_feed_does_not(self, monkeypatch, tmp_path):
        out = os.path.join(str(tmp_path), "fill_costs.json")
        monkeypatch.setattr(q, "FILL_COST_PATH", out)
        monkeypatch.setattr(q, "FILLCOST_REFRESH_S", 3600.0)
        calls = []

        class _C(MockClient):
            def _get_paginated(self, path, key, params=None):
                calls.append(key)
                return {key: []}
        c = _C(mode="live")
        q._refresh_fill_costs(c)
        assert os.path.exists(out), "missing feed must be written"
        assert calls, "refresh must read positions+fills"
        n = len(calls)
        q._refresh_fill_costs(c)
        assert len(calls) == n, "fresh mtime -> no rewrite (hourly gate)"

    def test_refresh_failure_is_silent_and_keeps_stale_file(self, monkeypatch, tmp_path):
        out = os.path.join(str(tmp_path), "fill_costs.json")
        with open(out, "w") as fh:
            fh.write('{"schema":1,"markets":{}}')
        os.utime(out, (1, 1))                            # ancient mtime -> due
        monkeypatch.setattr(q, "FILL_COST_PATH", out)
        monkeypatch.setattr(q, "FILLCOST_REFRESH_S", 3600.0)

        class _Boom(MockClient):
            def _get_paginated(self, path, key, params=None):
                raise RuntimeError("api down")
        before = open(out).read()
        q._refresh_fill_costs(_Boom(mode="live"))        # must not raise
        assert open(out).read() == before, "failed refresh keeps the stale file intact"
