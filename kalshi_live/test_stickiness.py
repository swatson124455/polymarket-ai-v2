"""Pins for the STICKINESS SLATE (operator-named 2026-07-29: items A, D, E, G).

  A INCUMBENCY — a market we rest in keeps its seat unless a challenger beats it by the
    bonus margin; 0.0 = provable no-op; sunk losses buy no loyalty (that's the governor).
  D RETENTION GAUGE — plan.fp_retained_pct measures footprint overlap vs last cycle.
  E PROBE SIZING — exploration slots rest probe-sized accumulating quotes; unwinds untouched.
  G REF_MOVE GAP GUARD — an observation after a long gap measures drift, not volatility;
    it must not poison the swing penalty.
"""
import json
import os

import kalshi_market_scores as ks
from test_live_hardening import q, MockClient, _run, _cfg


def _row(t, pool):
    return {"ticker": t, "usd_day": pool}


# ---- A ----

def test_incumbent_keeps_seat_against_a_merely_equal_challenger():
    m = {}
    ks.update(m, "INC", 20.0, 0.50, now=1000.0)
    ks.update(m, "CHAL", 20.0, 0.50, now=1000.0)          # identical measurement
    out = ks.rank(m, [_row("CHAL", 500), _row("INC", 500)], now=1000.0, explore=0,
                  incumbents={"INC"}, incumbency_bonus=0.25)
    assert [r["ticker"] for r in out][0] == "INC", "a tie must go to the incumbent"
    # and a challenger that clearly beats the margin still takes the seat
    ks.update(m, "CHAL", 30.0, 0.50, now=1000.0)          # 50% better > 25% bonus
    out2 = ks.rank(m, [_row("CHAL", 500), _row("INC", 500)], now=1000.0, explore=0,
                   incumbents={"INC"}, incumbency_bonus=0.25)
    assert [r["ticker"] for r in out2][0] == "CHAL", "a clearly better challenger must win"


def test_incumbency_zero_is_noop():
    m = {}
    ks.update(m, "A", 20.0, 0.50, now=1000.0)
    ks.update(m, "B", 21.0, 0.50, now=1000.0)
    plain = ks.rank(m, [_row("A", 500), _row("B", 500)], now=1000.0, explore=0)
    with_inc = ks.rank(m, [_row("A", 500), _row("B", 500)], now=1000.0, explore=0,
                       incumbents={"A"}, incumbency_bonus=0.0)
    assert [r["ticker"] for r in plain] == [r["ticker"] for r in with_inc]
    assert q.INCUMBENCY_BONUS == 0.0, "code default ships OFF; env sets the live value"


def test_incumbents_feed_comes_from_prev_cycle_standing(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    from test_live_hardening import _order
    c = MockClient(mode="live", resting=[_order("a", "TSTAND", "yes", 0.5, 5)])
    _run(monkeypatch, c, str(tmp_path))
    st = json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))
    assert st.get("prev_standing_tickers") == ["TSTAND"], \
        "next cycle's incumbents = where we actually rest"


# ---- D ----

def test_retention_gauge(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "T2", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    row1 = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))
    assert "fp_retained_pct" not in row1, "first cycle has no baseline"
    row2 = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))
    assert row2.get("fp_retained_pct") == 100.0
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": "T9", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    row3 = _run(monkeypatch, MockClient(mode="live"), str(tmp_path))
    assert row3.get("fp_retained_pct") == 50.0


# ---- E ----

def test_rank_marks_explore_picks():
    m = {}
    ks.update(m, "KNOWN", 999.0, 0.50, now=1000.0)
    rows = [_row("KNOWN", 100), _row("NEW1", 1)]
    out = ks.rank(m, rows, now=1000.0, explore=1)
    assert out[0]["ticker"] == "NEW1" and out[0].get("explore") is True
    assert "explore" not in out[1], "non-explore picks carry no mark"


def test_probe_sizing_caps_explore_accumulating_quotes(monkeypatch, tmp_path):
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "TPROBE", "usd_day": 100.0, "target": 1,
         "end": "2099-01-01T00:00:00Z", "explore": True},
        {"ticker": "TFULL", "usd_day": 90.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live")
    # the portfolio-tracking total cap (operator 2026-07-31) would trim TFULL at the default
    # $100 mock equity — lift equity so this test keeps pinning PROBE SIZING, not the cap
    monkeypatch.setattr(c, "get_balance", lambda: {"balance_dollars": "1000.0000"})
    _run(monkeypatch, c, str(tmp_path))
    probe = [x for x in c.created if x["ticker"] == "TPROBE"]
    full = [x for x in c.created if x["ticker"] == "TFULL"]
    assert probe and all(x["count"] <= 5 for x in probe), "explore market probe-sized"
    assert full and any(x["count"] > 5 for x in full), "earner keeps full size"


def test_probe_sizing_never_shrinks_an_exit(monkeypatch, tmp_path):
    """An explore market we happen to HOLD in must still rest its full-size unwind — shrinking
    an exit to probe size would strand inventory behind a data-budget knob."""
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "THELD", "usd_day": 100.0, "target": 1,
         "end": "2099-01-01T00:00:00Z", "explore": True}])
    c = MockClient(mode="live", positions=[
        {"ticker": "THELD", "position_fp": "20.00", "market_exposure_dollars": "10.00"}])
    _run(monkeypatch, c, str(tmp_path))
    held_orders = [x for x in c.created if x["ticker"] == "THELD"]
    assert held_orders and max(x["count"] for x in held_orders) >= 20, \
        "the unwind must keep |inv| size, never the probe cap"


def test_probe_sizing_default_off(monkeypatch, tmp_path):
    assert q.EXPLORE_PROBE_CT == 0
    _cfg(monkeypatch, join=100, mktcap=250, totcap=100000)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "TPROBE", "usd_day": 100.0, "target": 1,
         "end": "2099-01-01T00:00:00Z", "explore": True}])
    c = MockClient(mode="live")
    _run(monkeypatch, c, str(tmp_path))
    assert any(x["count"] > 5 for x in c.created), "flag off -> full size even when marked"


# ---- G ----

def test_ref_move_ignores_long_gap_drift():
    m = {}
    ks.update(m, "T", 20.0, 0.50, now=1000.0)
    ks.update(m, "T", 20.0, 0.51, now=1060.0)              # normal cycle: 1c move folds
    normal = m["T"]["ref_move"]
    assert normal > 0
    # 3 hours later the ref is 10c away — that is DRIFT; the EWMA must not eat it
    ks.update(m, "T", 20.0, 0.61, now=1060.0 + 3 * 3600)
    assert m["T"]["ref_move"] == normal, "a gap observation must not poison the swing penalty"
    assert m["T"]["ref"] == 0.61, "the reference itself still updates"
    # and the next NORMAL-cadence observation folds again
    ks.update(m, "T", 20.0, 0.62, now=1060.0 + 3 * 3600 + 60)
    assert m["T"]["ref_move"] != normal
