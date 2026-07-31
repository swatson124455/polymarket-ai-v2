"""Pins for the 2026-07-31 blind re-review fix batch (H1 H2 M4 M5 M6, mkt_out gate,
amnesty backup, L9). H1's pin is the exact gap the old tests had: a REAL cross in cycle N
must be visible to the governor in cycle N+1 via the persisted state round trip."""
import datetime as dt
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _pos(t="T1", pos="0.50", realized="0.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "0.25",
            "realized_pnl_dollars": realized}


def _state(tmp_path):
    return json.load(open(os.path.join(str(tmp_path), "quoter_state.json")))


def _stranded(monkeypatch, tmp_path, extra=None):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 2.0)
    monkeypatch.setattr(q, "STRAND_CROSS_S", 15.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    monkeypatch.setattr(q, "EXIT_LADDER_STEPS", 0)      # cross at first strand (legacy)
    old = (q.utcnow() - dt.timedelta(seconds=600)).isoformat()
    st = {"strand_grace": {"T1": old}}
    st.update(extra or {})
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump(st, fh)

    def _pg(p):
        if "orderbook" in p:
            return {"orderbook_fp": {"yes_dollars": [["0.50", "500"]],
                                     "no_dollars": [["0.45", "500"]]}}
        return {"incentive_programs": [], "next_cursor": ""}
    monkeypatch.setattr(q, "public_get", _pg)


def test_h1_paid_exit_count_survives_the_cycle_round_trip(monkeypatch, tmp_path):
    _stranded(monkeypatch, tmp_path)
    c = MockClient(mode="live", positions=[_pos(pos="10.00")])
    _run(monkeypatch, c, str(tmp_path))
    assert len(c.crosses) >= 1, "setup: the strand must cross"
    assert _state(tmp_path).get("mkt_taker_xn", {}).get("T1", 0) >= 1, \
        "H1: the paid-exit count must SURVIVE into persisted state (was clobbered)"


def test_h1_counts_one_episode_not_per_ioc_pass(monkeypatch, tmp_path):
    _stranded(monkeypatch, tmp_path)
    q._TAKER_XN.clear()
    monkeypatch.setattr(q, "public_get", lambda p: {
        "orderbook_fp": {"yes_dollars": [["0.50", "500"]], "no_dollars": [["0.45", "500"]]}})
    c = MockClient(mode="live", positions=[_pos(pos="10.00")])

    # partial fills across 3 IOC passes inside ONE invocation = ONE episode
    orig = c.create_order_v2
    def _partial(ticker, side, count, price, **kw):
        return orig(ticker, side, min(4, count), price, **kw)
    monkeypatch.setattr(c, "create_order_v2", _partial)
    q._taker_cross_capped(c, "T1", 10, True, tries=4)
    assert q._TAKER_XN.get("T1") == 1, "L10: one episode per invocation, not per pass"
    q._TAKER_XN.clear()


def test_h2_touch_anchor_pruned_when_flat(monkeypatch, tmp_path):
    _stranded(monkeypatch, tmp_path, {"strand_touch": {"T1": [0.50, 1]}})
    c = MockClient(mode="live", positions=[_pos(pos="10.00")])
    _run(monkeypatch, c, str(tmp_path))                 # crosses flat
    assert "T1" not in _state(tmp_path).get("strand_touch", {}), \
        "H2: a finished episode must not leave a stale anchor"


def test_l9_corrupt_touch_entry_self_heals_and_cross_proceeds(monkeypatch, tmp_path):
    _stranded(monkeypatch, tmp_path, {"strand_touch": {"T1": "not-a-list"}})
    monkeypatch.setattr(q, "EXIT_LADDER_STEPS", 2)
    monkeypatch.setattr(q, "SWEEP_VETO_TICKS", 3)
    c = MockClient(mode="live", positions=[_pos(pos="10.00")])
    row = _run(monkeypatch, c, str(tmp_path))           # must not raise
    # after self-heal the normal calculator path proceeds (here: expensive book -> ladder);
    # the backstop is intact either way — what must NOT happen is a book-wide abort
    assert len(c.crosses) >= 1 or row.get("exit_ladder_stepped") == 1,         "L9: corrupt state must not block the exit machinery"
    assert "strand_cross_failed" not in row or row.get("strand_cross_failed", 0) == 0


def test_m4_feed_failure_is_visible_and_feeds_fail_closed(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    q._REALIZED_LAST_GOOD.clear()
    rows = []
    for _ in range(3):
        c = MockClient(mode="live", positions=[_pos()], get_realized_raises=True)
        rows.append((_run(monkeypatch, c, str(tmp_path)), c))
    assert rows[0][0].get("realized_feed_fallback") == 1, "M4: degradation must be visible"
    assert rows[2][0].get("governor_fail_reduce_only") == 1, \
        "M4: persistent feed failure must fail closed like any governor fault"
    assert rows[2][1].created == []


def test_m5_halt_confirm_n6_still_halts(monkeypatch, tmp_path):
    """Integration: with the knob above the old hardcoded 5-window, sustained breaches must
    STILL confirm (the window sizes to the knob; the halt can never go unreachable)."""
    from test_audit_batch2 import _drawdown_setup, _BalClient
    monkeypatch.setattr(q, "HALT_CONFIRM_N", 6)
    _drawdown_setup(monkeypatch, tmp_path, peak=300.0)
    stop = os.path.join(str(tmp_path), "STOP")
    for _ in range(6):
        _run(monkeypatch, _BalClient(250.0, mode="live"), str(tmp_path))
    assert os.path.exists(stop), "M5: 6 sustained breaches at N=6 must write STOP"


def test_m6_incumbent_in_out_series_keeps_full_size(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "EXPLORE_PROBE_CT", 5)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "KXBURN-INC-1", "usd_day": 100.0, "target": 1,
         "end": "2099-01-01T00:00:00Z"}])
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"mkt_out": ["KXBURN-OLD-1"],
                   "prev_standing_tickers": ["KXBURN-INC-1"]}, fh)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("series_probe") is None, "M6: incumbents are not probe-shrunk"
    mine = [x for x in c.created if x["ticker"] == "KXBURN-INC-1"]
    assert mine and any(x["count"] > 5 for x in mine)


def test_mkt_out_enforced_with_day_governor_off(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    assert q.MKT_DAY_LOSS_EXITONLY_USD == 0.0           # code default: knob off
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"mkt_out": ["T1"]}, fh)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert c.created == [], "permanent bans must not die with the day-governor knob"
    assert row.get("mkt_out") == 1


def test_amnesty_backup_restores_bans_after_state_loss(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    q._REALIZED_LAST_GOOD.clear()
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"mkt_out": ["T1"]}, fh)
    _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    assert os.path.exists(os.path.join(str(tmp_path), "mkt_out_backup.json"))
    # simulate total state loss
    os.remove(os.path.join(str(tmp_path), "quoter_state.json"))
    c2 = MockClient(mode="live", positions=[])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert c2.created == [], "backup file must survive a state amnesty and re-ban"
    assert row2.get("mkt_out") == 1
