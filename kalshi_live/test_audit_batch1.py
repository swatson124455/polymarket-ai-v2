"""Pins for the 2026-07-29 self-audit fix batch 1 (operator: "proceed" on items 1-4).

  B1 STOP ESCALATION CROSSES NAKED ONLY — _flatten_all pass 2 must never de-pair a floored
     ladder pair (ports the settle-taker's audit-F3 fix to the STOP path).
  B2 PRE-CLOSE TAKER IS PACED — a non-flat cross re-arms the grace clock, stale stamps for
     flat tickers are pruned, and one invocation crosses at most TAKER_MAX_MKTS markets.
  B3 STOP FLATTEN IS PACED — first invocation flattens immediately; repeats inside
     KALSHI_STOPFLAT_REPEAT_S stand by instead of re-bursting takers every heartbeat.
  B4 FILL CONFIRMATION SURVIVES THE _fp DIALECT — probe 2026-07-29T18:50Z: GET-orders serves
     only fill_count_fp; the cross-confirm must read either dialect.
"""
import datetime as dt
import json
import os

from test_live_hardening import q, MockClient, _order

_BOOK = {"orderbook_fp": {"yes_dollars": [["0.60", "500"]], "no_dollars": [["0.38", "500"]]}}


def _stop_run(monkeypatch, c, d):
    monkeypatch.setattr(q, "DATA_DIR", d)
    monkeypatch.setattr(q, "STOP_FILE", os.path.join(d, "STOP"))
    monkeypatch.setattr(q, "STATE_FILE", os.path.join(d, "s.json"))
    open(os.path.join(d, "STOP"), "w").close()
    monkeypatch.setattr(q, "KalshiOrderClient", lambda *a, **k: c)
    q.run_once()


# ---- B1 ----

def test_stop_escalation_crosses_naked_not_gross(monkeypatch, tmp_path):
    """+40 yes on a lower strike / -34 no on a higher strike of the same ladder = 34 ct paired
    (floored), +6 naked. The escalation must cross ~6 ct, never the 74-ct gross."""
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 2.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)         # no sleep in tests
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    pos = [{"ticker": "KXAAAGASD-26JUL23-4.095", "position_fp": "40.00",
            "market_exposure_dollars": "16.00"},
           {"ticker": "KXAAAGASD-26JUL23-4.100", "position_fp": "-34.00",
            "market_exposure_dollars": "20.40"}]
    c = MockClient(mode="live", positions=pos)
    q._flatten_all(c)
    total = c.total_crossed()
    assert 0 < total <= 6 + 1, f"crossed {total} ct — must be the naked 6, never the gross 74"


# ---- B2 ----

def _mkpos(t="T1", pos="10.00"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": "5.00"}


def _preclose_env(monkeypatch, close_min=5):
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "STOP_TAKER_MIN_CT", 2.0)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 120.0)
    monkeypatch.setattr(q, "PRECLOSE_FLATTEN_MIN", 20.0)
    monkeypatch.setattr(q, "TAKER_FLATTEN", 1)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    return (q.utcnow() + dt.timedelta(minutes=close_min)).isoformat()


def test_preclose_nonflat_cross_rearms_the_clock(monkeypatch):
    close = _preclose_env(monkeypatch)
    now = q.utcnow()
    old = (now - dt.timedelta(seconds=600)).isoformat()
    grace = {"T1": old}
    plan = {}
    # position that stays >= threshold after the cross (MockClient reduces by crossed count;
    # cross fills against a book, cap 10 -> position reaches 0 -> flat. Force RESIDUAL instead:
    # make create_order_v2 fill nothing so flat=False.)
    c = MockClient(mode="live", positions=[_mkpos()])
    orig = c.create_order_v2
    def _nofill(*a, **k):
        r = orig(*a, **k)
        r["order"]["fill_count"] = "0"
        for p in c._positions:                       # undo the mock's position reduction
            p["position_fp"] = "10.00"
        return r
    monkeypatch.setattr(c, "create_order_v2", _nofill)
    q._preclose_naked_flatten(c, {"T1": 10.0}, now, plan, grace,
                              close_time_of=lambda t: close, costs_by={})
    assert grace["T1"] != old, "a non-flat cross must RE-ARM the grace clock (pacing)"
    assert (now - q.parse_iso(grace["T1"])).total_seconds() < 5


def test_preclose_prunes_stale_stamp_for_flat_ticker(monkeypatch):
    close = _preclose_env(monkeypatch)
    now = q.utcnow()
    grace = {"GONE": (now - dt.timedelta(days=3)).isoformat()}
    # GONE is fully flat (absent from held_by); T1 is fresh in-window -> gets a NEW stamp and
    # (grace running) no taker. The ancient GONE stamp must be pruned, not consulted later.
    c = MockClient(mode="live", positions=[_mkpos()])
    q._preclose_naked_flatten(c, {"T1": 10.0}, now, {}, grace,
                              close_time_of=lambda t: close, costs_by={})
    assert "GONE" not in grace, "flat ticker's stale stamp must be pruned"
    assert "T1" in grace and c.crosses == [], "fresh stamp -> maker grace runs, no taker"


def test_preclose_caps_markets_per_invocation(monkeypatch):
    close = _preclose_env(monkeypatch)
    monkeypatch.setattr(q, "TAKER_MAX_MKTS", 2)
    now = q.utcnow()
    old = (now - dt.timedelta(seconds=600)).isoformat()
    held = {f"T{i}": 10.0 for i in range(5)}
    grace = {t: old for t in held}
    c = MockClient(mode="live", positions=[_mkpos(t) for t in held])
    q._preclose_naked_flatten(c, held, now, {}, grace,
                              close_time_of=lambda t: close, costs_by={})
    assert len({x["ticker"] for x in c.crosses}) <= 2, "one invocation crosses at most the cap"


# ---- B3 ----

def test_stop_flatten_is_paced(monkeypatch, tmp_path):
    d = str(tmp_path)
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    monkeypatch.setattr(q, "STOP_ESCALATE_S", 0)
    c1 = MockClient(mode="live", resting=[_order("a", "T1", "yes", 0.6, 10)])
    _stop_run(monkeypatch, c1, d)
    assert c1.cancelled == ["a"], "first STOP invocation flattens immediately"
    # second heartbeat seconds later: paced -> must NOT re-run the flatten
    c2 = MockClient(mode="live", resting=[_order("b", "T1", "yes", 0.6, 10)])
    _stop_run(monkeypatch, c2, d)
    assert c2.cancelled == [], "repeat inside the pacing window must stand by"
    # age the stamp past the window -> flatten runs again
    stale = (q.utcnow() - dt.timedelta(seconds=q.STOPFLAT_REPEAT_S + 60)).isoformat()
    with open(os.path.join(d, "stopflat.last"), "w") as fh:
        fh.write(stale)
    c3 = MockClient(mode="live", resting=[_order("c", "T1", "yes", 0.6, 10)])
    _stop_run(monkeypatch, c3, d)
    assert c3.cancelled == ["c"], "an aged stamp must allow the next flatten pass"


# ---- B4 ----

def test_cross_confirmation_reads_fp_dialect(monkeypatch):
    """MockClient now returns fill_count_fp ONLY (the migrated GET-orders shape, probe
    18:50Z) — the cross must still see its fill, decrement, and go flat."""
    monkeypatch.setattr(q, "public_get", lambda p: _BOOK)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    c = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "5.00"}])
    orig = c.create_order_v2
    def _fp_only(*a, **k):
        r = orig(*a, **k)
        r["order"]["fill_count_fp"] = r["order"].pop("fill_count")
        return r
    monkeypatch.setattr(c, "create_order_v2", _fp_only)
    flat, n = q.flatten_to_zero(c, "T1")
    assert flat and n >= 1, "fill_count_fp-only response must confirm the cross"
    ok, nc = None, None
    c2 = MockClient(mode="live", positions=[{"ticker": "T1", "position_fp": "5.00"}])
    orig2 = c2.create_order_v2
    def _fp_only2(*a, **k):
        r = orig2(*a, **k)
        r["order"]["fill_count_fp"] = r["order"].pop("fill_count")
        return r
    monkeypatch.setattr(c2, "create_order_v2", _fp_only2)
    ok, nc = q._taker_cross_capped(c2, "T1", 5, True)
    assert ok and nc == 5
