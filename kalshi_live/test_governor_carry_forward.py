"""THE GOVERNOR CARRIES A DEPARTED TICKER'S REALIZED LOSS (defect 5a, 2026-08-02).

The governor's feed is /portfolio/positions?count_filter=total_traded. That endpoint NEVER
emits a SETTLED market — MEASURED read-only 2026-08-03T01:35:04Z over the complete account
history: of 127 settled tickers, 0 appear in the feed (42 rows returned, none settled).
count_filter governs zero-vs-nonzero POSITION rows, not settlement state, so the 07-31
burn-and-run fix (which correctly keeps FLAT-but-alive rows) has no bearing on it.

The old code then did `_REALIZED_LAST_GOOD.clear(); update(fresh)` — erasing a departed
ticker from the in-memory snapshot, the persisted snapshot and the fallback source in one
statement. Live 2026-08-02 the day's largest single loss (KXTEMPAUSH, -$9.4939 all-in) was
absent from the governor's realized read entirely.

A departed ticker's last known value is now CARRIED for the rest of the day, with FRESH
ALWAYS WINNING so a live ticker is never shadowed by a stale carry. The carry is FROZEN, so
by itself it cannot manufacture a trip the governor lacked the data for — moving it is the
separate settlement top-up.
"""
import json
import os

from test_live_hardening import MockClient, _cfg, _run, q


def _pos(t="T1", pos="0.50", realized="0.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "0.25",
            "realized_pnl_dollars": realized}


def _fp1(monkeypatch):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


def _arm(monkeypatch):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    monkeypatch.setattr(q, "MKT_OUT_LOSS_USD", 5.0)


def _state(tmp_path):
    p = os.path.join(str(tmp_path), "quoter_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def test_departed_ticker_value_is_carried(monkeypatch, tmp_path):
    # T2 trades and loses, then SETTLES — vanishing from the feed. Its loss must survive.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-4.00"}]),
         str(tmp_path))
    row = _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                       traded=[{"ticker": "T1",
                                                "realized_pnl_dollars": "0.00"}]),
               str(tmp_path))
    carry = _state(tmp_path).get("mkt_realized_carry") or {}
    assert "T2" in carry, "a settled ticker's realized value must not be erased"
    assert carry["T2"] == -4.0
    assert row.get("realized_carried") == 1
    assert row.get("realized_carried_usd") == -4.0


def test_fresh_always_wins_over_the_carry(monkeypatch, tmp_path):
    # A carried ticker that REAPPEARS (it never really settled) must use the live value.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-4.00"}]),
         str(tmp_path))
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-0.25"}]),
         str(tmp_path))
    carry = _state(tmp_path).get("mkt_realized_carry") or {}
    assert "T2" not in carry, "a ticker present in the fresh feed is not carried"


def test_the_carry_still_enforces_the_ladder_on_a_departed_ticker(monkeypatch, tmp_path):
    # THE POINT. A market that burned past the $3 rung and then settled must still be
    # exit-only/tripped rather than silently forgotten.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-3.50"}]),
         str(tmp_path))
    assert "T2" in (_state(tmp_path).get("mkt_loss_tripped") or [])
    # T2 settles away; the trip must persist and the value must still be carried
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    st = _state(tmp_path)
    assert "T2" in (st.get("mkt_loss_tripped") or []), "the trip must not be forgotten"
    assert (st.get("mkt_realized_carry") or {}).get("T2") == -3.5


def test_carry_clears_at_the_day_roll(monkeypatch, tmp_path):
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"},
                                         {"ticker": "T2", "realized_pnl_dollars": "-4.00"}]),
         str(tmp_path))
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    st = _state(tmp_path)
    assert (st.get("mkt_realized_carry") or {}).get("T2") == -4.0
    st["mkt_realized_day"] = "2020-01-01"
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump(st, fh)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "0.00"}]),
         str(tmp_path))
    assert (_state(tmp_path).get("mkt_realized_carry") or {}) == {}, \
        "the carry is per-day state and must clear with the rest of the ladder"


def test_feed_outage_does_not_freeze_live_tickers_into_the_carry(monkeypatch, tmp_path):
    # On a failed read every ticker looks 'departed'. Writing the carry then would shadow the
    # real feed once it recovers, so the carry is only persisted on a SUCCESSFUL read.
    _arm(monkeypatch)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                 traded=[{"ticker": "T1", "realized_pnl_dollars": "-1.00"}]),
         str(tmp_path))
    before = dict(_state(tmp_path).get("mkt_realized_carry") or {})
    row = _run(monkeypatch, MockClient(mode="live", positions=[_pos("T1")],
                                       traded=[{"ticker": "T1",
                                                "realized_pnl_dollars": "-1.00"}],
                                       get_realized_raises=True),
               str(tmp_path))
    assert row.get("realized_feed_fallback") == 1, "the outage path must still be flagged"
    assert (_state(tmp_path).get("mkt_realized_carry") or {}) == before, \
        "a feed outage must not rewrite the carry"
