"""Pins for the PER-MARKET LOSS GOVERNORS (operator directive 2026-07-29: "dont ban markets
as fixes, fix what caused the issue"). Two market-agnostic LOGIC fixes for the 07-29 loss
shape (KXMUSKNW -$11.02/day, session_econ 18:26Z: join trending book -> adverse fill ->
paid taker exit -> REJOIN minutes later -> repeat):

  GOVERNOR 1 — MKT_DAY_LOSS_EXITONLY_USD: realized loss today (venue receipt delta) hits the
  limit -> that market is EXIT-ONLY for the rest of the UTC day. Latches. Resets at day roll.
  GOVERNOR 2 — REENTRY_COOLDOWN_S: a strand taker-cross (we PAID to leave) starts an
  exit-only clock on that ticker. Survives restarts via quoter_state.

  T1 TRIP STRIPS ACCUMULATION  T2 PRIOR-DAY REALIZED NEVER TRIPS  T3 TRIP LATCHES WHEN FLAT
  T4 DAY ROLL RESETS           T5 SHIPS OFF (defaults 0 = byte-identical)
  T6 COOLDOWN ENFORCES + EXPIRES  T7 STRAND CROSS FEEDS THE COOLDOWN  T8 SIDE CHANNEL
"""
import datetime as dt
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


def test_trip_strips_accumulating_quotes(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
    # cycle 1: -$1 realized -> baseline; small position (below tolerance) so both sides quote
    row = _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-1.00")]),
               str(tmp_path))
    assert row.get("loss_exitonly") == 0
    # cycle 2: -$7 realized -> delta -6 vs baseline -> TRIP -> nothing accumulating created
    c2 = MockClient(mode="live", positions=[_pos(realized="-7.00")])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("loss_exitonly") == 1
    assert c2.created == [], "a tripped market must not receive accumulating quotes"
    assert _state(tmp_path).get("mkt_loss_tripped") == ["T1"]


def test_prior_day_or_preexisting_realized_never_trips(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
    # first sight of a market already carrying -$50 LIFETIME realized: baseline at -50,
    # today's governor sees a delta of 0 — history is not today's bleed
    c = MockClient(mode="live", positions=[_pos(realized="-50.00")])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("loss_exitonly") == 0
    assert len(c.created) > 0, "an un-tripped market quotes normally"
    # MID-DAY first sight (the setdefault path, distinct from the day-roll snapshot): a market
    # that first appears AFTER the day's baseline was taken, already carrying lifetime realized,
    # must baseline at its current value — not at zero
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T9", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c2 = MockClient(mode="live", positions=[_pos(t="T9", realized="-50.00")])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("loss_exitonly") == 0, "mid-day first sight must baseline, not trip"
    assert len(c2.created) > 0


def test_trip_latches_for_the_day_even_when_flat(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
    _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="0.00")]), str(tmp_path))
    _run(monkeypatch, MockClient(mode="live", positions=[_pos(realized="-6.00")]), str(tmp_path))
    # fully flat now -> the positions row VANISHES (count_filter) -> trip must survive
    c3 = MockClient(mode="live", positions=[])
    row3 = _run(monkeypatch, c3, str(tmp_path))
    assert row3.get("loss_exitonly") == 1, "trip must latch for the day, not amnesty on flat"
    assert c3.created == []


def test_day_roll_resets_baseline_and_trips(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 5.0)
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"mkt_realized_day": "2020-01-01", "mkt_loss_tripped": ["T1"],
                   "mkt_realized_base": {"T1": 0.0}}, fh)
    c = MockClient(mode="live", positions=[_pos(realized="-50.00")])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("loss_exitonly") == 0, "a new UTC day starts clean"
    assert len(c.created) > 0


def test_ships_off_and_off_means_byte_identical(monkeypatch, tmp_path):
    assert q.MKT_DAY_LOSS_EXITONLY_USD == 0.0
    assert q.REENTRY_COOLDOWN_S == 0.0
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    c = MockClient(mode="live", positions=[_pos(realized="-500.00")])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "loss_exitonly" not in row and "reentry_cooldown" not in row
    assert len(c.created) > 0, "flag off -> even a catastrophic realized number gates nothing"


def test_cooldown_enforces_and_expires(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 3600.0)
    future = (q.utcnow() + dt.timedelta(seconds=3000)).isoformat()
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"reentry_cool": {"T1": future}}, fh)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("reentry_cooldown") == 1
    assert c.created == [], "a cooling market must not be rejoined"
    past = (q.utcnow() - dt.timedelta(seconds=10)).isoformat()
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"reentry_cool": {"T1": past}}, fh)
    c2 = MockClient(mode="live", positions=[])
    row2 = _run(monkeypatch, c2, str(tmp_path))
    assert row2.get("reentry_cooldown") == 0
    assert len(c2.created) > 0, "an expired cooldown must fully release the market"
    assert _state(tmp_path).get("reentry_cool") == {}, "expired stamps are pruned"


def test_strand_cross_feeds_the_cooldown(monkeypatch, tmp_path):
    """Integration: a naked position outside the footprint strand-crosses -> the ticker lands
    in reentry_cool with a future expiry. This pins the CALL-SITE wiring, not just the
    _strand_cross return value."""
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 3600.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 2.0)
    monkeypatch.setattr(q, "STRAND_CROSS_S", 15.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    old = (q.utcnow() - dt.timedelta(seconds=600)).isoformat()
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"strand_grace": {"T1": old}}, fh)
    c = MockClient(mode="live", positions=[_pos(pos="5.00")])
    _run(monkeypatch, c, str(tmp_path))
    assert len(c.crosses) >= 1, "the stranded naked position must taker-cross"
    cool = _state(tmp_path).get("reentry_cool") or {}
    assert "T1" in cool and q.parse_iso(cool["T1"]) > q.utcnow()


# ---- SELF-AUDIT FIXES (2026-07-29 evening: F18 / F19 / F6a / F6b / F5 gaps) ----

def test_dry_run_cycle_survives_with_governors_in_code(monkeypatch, tmp_path):
    """F18: _exit_only_mkts was initialized only in the live branch -> every dry-run cycle
    died with UnboundLocalError at the first quoted market. Dry-run must complete a cycle."""
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    c = MockClient(mode="dry_run")
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("mode") == "dry_run"
    assert row.get("quote_fail", 0) == 0 and "positions_read_failed" not in row


def test_unparseable_cooldown_stamp_fails_closed(monkeypatch, tmp_path):
    """F19: an unparseable expiry used to be forgotten (fail-open re-entry). It must now
    re-stamp a fresh cooldown and keep the market exit-only."""
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 3600.0)
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"reentry_cool": {"T1": "not-a-timestamp"}}, fh)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("reentry_cooldown") == 1, "corrupt stamp must stay cooling, not amnesty"
    assert c.created == []
    fresh = _state(tmp_path)["reentry_cool"]["T1"]
    assert q.parse_iso(fresh) > q.utcnow(), "stamp self-heals to a real future expiry"


def test_fetch_fail_retention_respects_governor(monkeypatch, tmp_path):
    """F6a: on a transient book-fetch error the retained standing copy routed around the
    exit-only strip, so a governed market's accumulating orders survived. They must not."""
    _cfg(monkeypatch)
    _fp1(monkeypatch)
    monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 3600.0)
    future = (q.utcnow() + dt.timedelta(seconds=3000)).isoformat()
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"reentry_cool": {"T1": future}}, fh)
    # book fetch fails for T1; an accumulating order is resting -> diff must CANCEL it
    def _pg(p):
        if "orderbook" in p:
            # ValueError, NOT RuntimeError: RuntimeError is the read-budget-exhausted signal
            # (`except RuntimeError: break`), which empties `desired` and cancels everything —
            # passing this test without exercising the retention branch at all.
            raise ValueError("book 500")
        return {"incentive_programs": [], "next_cursor": ""}
    monkeypatch.setattr(q, "public_get", _pg)
    from test_live_hardening import _order
    c = MockClient(mode="live", positions=[],
                   resting=[_order("acc1", "T1", "yes", 0.50, 10)])
    _run(monkeypatch, c, str(tmp_path))
    assert "acc1" in c.cancelled, \
        "governed market's accumulating order must be cancelled even on a fetch error"


def test_settle_taker_feeds_reentry_cooldown(monkeypatch, tmp_path):
    """F5: the settle-taker paid a taker to leave but never armed the cooldown — the exact
    rejoin loop the 07-29 fix was written to close, still open through this path."""
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 3600.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    # market closes inside SETTLE_UNWIND_MIN -> settle-taker arms
    close = (q.utcnow() + dt.timedelta(minutes=5)).isoformat()
    def _pg(p):
        if p.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [["0.60", "500"]],
                                     "no_dollars": [["0.38", "500"]]}}
        if "/markets/" in p:
            return {"market": {"close_time": close}}
        return {"incentive_programs": [], "next_cursor": ""}
    monkeypatch.setattr(q, "public_get", _pg)
    c = MockClient(mode="live", positions=[_pos(pos="5.00")])
    _run(monkeypatch, c, str(tmp_path))
    assert len(c.crosses) >= 1, "settle-taker must fire"
    cool = _state(tmp_path).get("reentry_cool") or {}
    assert "T1" in cool and q.parse_iso(cool["T1"]) > q.utcnow(), \
        "a settle-taker exit must arm the re-entry cooldown"


def test_realized_side_channel_populated_by_held_cost():
    c = MockClient(positions=[_pos(realized="-3.25"),
                              {"ticker": "T2", "position_fp": "0", "realized_pnl_dollars": "9"}])
    q._held_cost(c)
    assert q._REALIZED_BY["T1"] == -3.25
    assert q._REALIZED_BY["T2"] == 9.0, "flat-but-present rows still report (API may include)"
