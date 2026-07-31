"""EXIT LOSS-MIN CALCULATOR pins (operator-named 2026-07-31 "make one for exits that is
beyond reproach") + the maker exit ladder it drives + the portfolio-tracking total cap
(operator: "base total capital on total portfolio until further notice").

The fee model is pinned against REAL PAID FEES (venue fill receipts, /portfolio/fills read
2026-07-31T15:09:52Z — 279/279 fills with fees matched the exact formula, worst diff $0.0000
over 922 lifetime fills)."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_exit_calc as xc                       # noqa: E402
from test_live_hardening import q, MockClient, _run, _cfg   # noqa: E402


def _state(tmp_path):
    p = os.path.join(str(tmp_path), "quoter_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


class TestFeeModel:
    def test_fee_matches_real_paid_receipts(self):
        # (count_fp, yes_price, fee_cost) copied verbatim from venue fill records
        for ct, p, paid in [(2.31, 0.26, 0.0312), (40.0, 0.05, 0.133),
                            (7.0, 0.78, 0.0841), (1.0, 0.31, 0.015)]:
            assert abs(xc.taker_fee(ct, p) - paid) < 1e-3, (ct, p, paid)

    def test_fee_is_exact_not_ceiled(self):
        # the "round up to the cent" variant predicts 0.04 here; the venue charged 0.0312
        assert abs(xc.taker_fee(2.31, 0.26) - 0.0311108) < 1e-6

    def test_fee_degenerate_inputs_are_zero(self):
        assert xc.taker_fee(0, 0.5) == 0.0
        assert xc.taker_fee(10, 0.0) == 0.0
        assert xc.taker_fee(10, 1.0) == 0.0


class TestCrossReceipt:
    def test_long_yes_executes_at_the_bid(self):
        r = xc.cross_receipt(80, True, 0.40, 0.45)
        assert r["exec_price"] == 0.40 and r["spread_ticks"] == 5
        assert abs(r["half_spread_usd"] - 0.5 * 0.05 * 80) < 1e-9
        assert abs(r["fee_usd"] - xc.taker_fee(80, 0.40)) < 1e-9
        assert abs(r["taker_cost_usd"] - (r["half_spread_usd"] + r["fee_usd"])) < 1e-3
        assert abs(r["per_ct_usd"] * 80 - r["taker_cost_usd"]) < 1e-2

    def test_long_no_executes_at_the_ask(self):
        r = xc.cross_receipt(10, False, 0.40, 0.45)
        assert r["exec_price"] == 0.45

    def test_unreadable_book_returns_none_never_a_guess(self):
        assert xc.cross_receipt(10, True, None, 0.45) is None
        assert xc.cross_receipt(10, True, 0.40, None) is None
        assert xc.cross_receipt(0, True, 0.40, 0.45) is None
        assert xc.cross_receipt(10, True, 0.50, 0.40) is None    # crossed book


class TestDecide:
    def _r(self, spread_ticks, cost):
        return {"spread_ticks": spread_ticks, "taker_cost_usd": cost}

    def test_one_tick_spread_crosses(self):
        assert xc.decide(self._r(1, 99.0), 0, 2, 0.25) == "cross"

    def test_cheap_cross_crosses(self):
        assert xc.decide(self._r(5, 0.20), 0, 2, 0.25) == "cross"

    def test_ladder_exhausted_crosses(self):
        assert xc.decide(self._r(5, 99.0), 2, 2, 0.25) == "cross"

    def test_expensive_wide_unexhausted_improves(self):
        assert xc.decide(self._r(5, 3.34), 1, 2, 0.25) == "improve"


class TestImprovedExitPricing:
    def test_step_moves_one_tick_inside(self):
        # long yes -> resting NO at no-bid 0.55; yes-bid 0.40 -> NO ask 0.59 bound
        assert q._improved_exit(0.55, 0.40, 1) == 0.56
        assert q._improved_exit(0.55, 0.40, 3) == 0.58

    def test_bound_never_crosses_post_only(self):
        assert q._improved_exit(0.55, 0.40, 10) == 0.59

    def test_tight_spread_stays_at_touch(self):
        assert q._improved_exit(0.55, 0.44, 1) == 0.55   # bound == touch -> no room

    def test_no_opposite_side_stays_at_touch(self):
        assert q._improved_exit(0.55, None, 2) == 0.55

    def test_zero_improve_is_identity(self):
        assert q._improved_exit(0.55, 0.40, 0) == 0.55

    def test_reducing_quotes_carries_the_improve(self):
        qs = q._reducing_quotes(0.40, 0.55, 10.0, 0.0, improve=1)
        assert qs and qs[0]["side"] == "no" and qs[0]["price_dollars"] == 0.56
        legacy = q._reducing_quotes(0.40, 0.55, 10.0, 0.0)
        assert legacy[0]["price_dollars"] == 0.55, "default must stay byte-identical"


def _strand_setup(monkeypatch, tmp_path, pos_ct, yes_bid, no_bid, step=None):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 2.0)
    monkeypatch.setattr(q, "STRAND_CROSS_S", 15.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    old = (q.utcnow() - dt.timedelta(seconds=600)).isoformat()
    state = {"strand_grace": {"T1": old}}
    if step is not None:
        state["strand_step"] = {"T1": step}
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump(state, fh)

    def _pg(p):
        if "orderbook" in p:
            return {"orderbook_fp": {"yes_dollars": [[f"{yes_bid:.2f}", "500"]],
                                     "no_dollars": [[f"{no_bid:.2f}", "500"]]}}
        return {"incentive_programs": [], "next_cursor": ""}
    monkeypatch.setattr(q, "public_get", _pg)
    return MockClient(mode="live",
                      positions=[{"ticker": "T1", "position_fp": f"{pos_ct:.2f}",
                                  "market_exposure_dollars": "1.00",
                                  "realized_pnl_dollars": "0.00"}])


class TestExitLadderIntegration:
    def test_expensive_cross_steps_the_ladder_instead_of_paying(self, monkeypatch, tmp_path):
        # 80 ct, spread 0.40/0.45: exact cross cost ~= $2 + $1.34 fee >> $0.25 -> improve
        c = _strand_setup(monkeypatch, tmp_path, 80.0, 0.40, 0.55)
        row = _run(monkeypatch, c, str(tmp_path))
        assert c.crosses == [], "expensive cross must ladder, not pay"
        assert row.get("exit_ladder_stepped") == 1
        st = _state(tmp_path)
        assert st.get("strand_step", {}).get("T1") == 1
        assert "T1" in st.get("strand_grace", {}), "clock re-arms, exit stays bounded in time"

    def test_ladder_exhausted_pays_the_taker(self, monkeypatch, tmp_path):
        c = _strand_setup(monkeypatch, tmp_path, 80.0, 0.40, 0.55, step=q.EXIT_LADDER_STEPS)
        _run(monkeypatch, c, str(tmp_path))
        assert len(c.crosses) >= 1, "bounded-time exit: exhausted ladder must still cross"

    def test_cheap_cross_pays_immediately(self, monkeypatch, tmp_path):
        # 5 ct at 2-tick spread: ~$0.05 + ~$0.08 fee < $0.25 -> legacy immediate cross
        c = _strand_setup(monkeypatch, tmp_path, 5.0, 0.40, 0.58)
        _run(monkeypatch, c, str(tmp_path))
        assert len(c.crosses) >= 1

    def test_ladder_disabled_restores_legacy(self, monkeypatch, tmp_path):
        c = _strand_setup(monkeypatch, tmp_path, 80.0, 0.40, 0.55)
        monkeypatch.setattr(q, "EXIT_LADDER_STEPS", 0)
        _run(monkeypatch, c, str(tmp_path))
        assert len(c.crosses) >= 1, "EXIT_LADDER_STEPS=0 must cross at first strand as before"


class TestPortfolioTrackingTotalCap:
    def test_total_cap_tracks_last_known_equity(self, monkeypatch):
        monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", 350.0)
        q._TOTAL_CAP_EFF[0] = 200.0
        try:
            assert q._total_cap() == 200.0
            q._TOTAL_CAP_EFF[0] = 500.0
            assert q._total_cap() == 350.0, "env cap stays a static ceiling"
            q._TOTAL_CAP_EFF[0] = None
            assert q._total_cap() == 350.0, "no equity ever observed -> env cap alone"
        finally:
            q._TOTAL_CAP_EFF[0] = None

    def test_cycle_publishes_effective_cap_from_balance(self, monkeypatch, tmp_path):
        _cfg(monkeypatch)                               # pins MAX_TOTAL_CAPITAL=40
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
        c = MockClient(mode="live", positions=[])       # get_balance -> $100.00 equity
        row = _run(monkeypatch, c, str(tmp_path))
        try:
            assert row.get("total_cap_eff") == 40.0, "env ceiling binds when below equity"
            # equity below the env ceiling -> equity binds
            monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", 1000.0)
            row2 = _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
            assert row2.get("total_cap_eff") == 100.0, "cap must track live mark equity"
        finally:
            q._TOTAL_CAP_EFF[0] = None                  # don't leak into other tests

    def test_equity_actually_gates_creates(self, monkeypatch, tmp_path):
        """The cap must BIND, not just report: $100 mock equity with a huge env cap and a
        market whose join costs more than $100 -> the market is trimmed, nothing creates."""
        _cfg(monkeypatch, join=250, mktcap=500, totcap=100000)
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
        c = MockClient(mode="live", positions=[])       # get_balance -> $100 equity
        row = _run(monkeypatch, c, str(tmp_path))
        try:
            assert c.created == [], "a join beyond live equity must not rest"
            assert row.get("capped_markets", 0) >= 1
        finally:
            q._TOTAL_CAP_EFF[0] = None
