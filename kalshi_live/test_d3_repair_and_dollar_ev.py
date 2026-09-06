"""Pins for D3 (operator-approved 2026-08-25): A. re-pair-after-cheap-fill, B. dollar-
weighted event delta. Both ship DEFAULT-OFF; the off state must be byte-identical (the
rest of the suite is that proof; the pins here assert both states explicitly).

Design doc: docs/maker_handoffs/KALSHI_D3_DESIGN_REPAIR_AND_DOLLAR_RISK_2026-08-25.md
"""
from test_live_hardening import q


_YL_Q = [[0.98, 1060.0]]     # qualifying join book (both sides reach Target 1000)
_NL_Q = [[0.01, 1100.0]]
_YL_DEEP = [[0.98, 100000.0]]
_NL_DEEP = [[0.01, 100000.0]]


def _mkt():
    return {"ticker": "KXD3-EV-40", "target": 1000,
            # end NOW-RELATIVE (+7d; date-rot incident 2026-09-06)
            "end": (q.utcnow() + __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"), "usd_day": 100.0, "df": 0.5}


def _cfg(monkeypatch, repair=1, dollars=0):
    monkeypatch.setattr(q, "REPAIR_CHEAP_FILL", repair)
    monkeypatch.setattr(q, "REPAIR_BASIS_MAX_D", 0.02)
    monkeypatch.setattr(q, "EVENT_DELTA_DOLLARS", dollars)
    monkeypatch.setattr(q, "EVENT_SOFT_USD", 5.25)
    monkeypatch.setattr(q, "EVENT_HARD_USD", 17.50)
    monkeypatch.setattr(q, "EVENT_FALLBACK_BASIS_D", 0.35)
    monkeypatch.setattr(q, "CAPTURE_GATE", 1)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 1.00)
    monkeypatch.setattr(q, "QUALIFIABLE_GATE", True)
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 0.0)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.003)
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.995)
    monkeypatch.setattr(q, "MIN_DEPTH_SYM", 0.0)
    monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 8)
    monkeypatch.setattr(q, "JOIN_SIZE", 40)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "INV_SOFT_CT", 15.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 50.0)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 60.0)
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 60.0)


def _by_reason(qs):
    out = {}
    for x in qs:
        out.setdefault(x.get("reason"), []).append(x)
    return out


# ---------------- A. re-pair after cheap fill -------------------------------------------------
def test_a1_default_off_is_exit_only(monkeypatch):
    _cfg(monkeypatch, repair=0)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.02)
    assert all(x.get("reason") == "unwind" for x in qs)      # byte-identical legacy


def test_a2_cheap_basis_repairs_consumed_side_within_envelope(monkeypatch):
    _cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.02)
    r = _by_reason(qs)
    assert "unwind" in r                                      # exit untouched, still present
    assert len(r.get("repair", [])) == 1
    rp = r["repair"][0]
    assert rp["side"] == "no" and rp["price_dollars"] == 0.01  # consumed side, at reference
    assert rp["count"] == 10                                   # min(join 40, INV_HARD 50 - 40)


def test_a3_mid_band_basis_stays_exit_only(monkeypatch):
    # the 07-27 incident class (basis 0.30-0.40) is untouched by the feature
    _cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.35)
    assert all(x.get("reason") == "unwind" for x in qs)


def test_a4_unknown_basis_fails_closed(monkeypatch):
    _cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.0)
    assert all(x.get("reason") == "unwind" for x in qs)


def test_a5_entry_gates_still_come_first(monkeypatch):
    # capture-poor book: the capture gate's HOLDING path returns reduce-only BEFORE the
    # join branch — re-pair never fires on a market that fails an entry gate.
    _cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_DEEP, _NL_DEEP, q.utcnow(), inv=-40.0, cost=0.02)
    assert qs and all(x.get("reason") == "unwind" for x in qs)


def test_a6_event_throttle_governs_repair(monkeypatch):
    _cfg(monkeypatch)
    # hard, same direction (long NO, event NO-ward beyond hard): no repair at all
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.02,
                          event_delta=-60.0)
    assert all(x.get("reason") == "unwind" for x in qs)
    # opposite direction (event YES-ward): repairing NO reduces |ev| -> full size
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.02,
                          event_delta=+60.0)
    r = _by_reason(qs)
    assert len(r.get("repair", [])) == 1 and r["repair"][0]["count"] == 10
    # soft band, same direction: shrink/step applies (never larger than the clean size)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-40.0, cost=0.02,
                          event_delta=-30.0)
    for rp in _by_reason(qs).get("repair", []):
        assert rp["count"] <= 10 and rp["price_dollars"] <= 0.01 + 1e-9


def test_a7_no_headroom_no_repair(monkeypatch):
    _cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=-50.0, cost=0.02)
    assert all(x.get("reason") == "unwind" for x in qs)       # |inv| == INV_HARD -> 0 room


# ---------------- B. dollar-weighted event delta ----------------------------------------------
def test_b1_flag_off_counts_contracts(monkeypatch):
    _cfg(monkeypatch, dollars=0)
    ev = q.event_deltas({"KXD3-EV-40": -40.0}, cost_by={"KXD3-EV-40": 0.02})
    assert q.event_delta_for(ev, "KXD3-EV-40") == -40.0
    assert q._ev_thresholds() == (15.0, 50.0)


def test_b2_flag_on_counts_dollars(monkeypatch):
    _cfg(monkeypatch, dollars=1)
    ev = q.event_deltas({"KXD3-EV-40": -40.0}, cost_by={"KXD3-EV-40": 0.02})
    assert abs(q.event_delta_for(ev, "KXD3-EV-40") - (-0.80)) < 1e-9
    assert q._ev_thresholds() == (5.25, 17.50)


def test_b3_missing_basis_uses_conservative_fallback(monkeypatch):
    _cfg(monkeypatch, dollars=1)
    ev = q.event_deltas({"KXD3-EV-40": -40.0}, cost_by={})
    assert abs(q.event_delta_for(ev, "KXD3-EV-40") - (-14.0)) < 1e-9   # 40 x 0.35 -> throttles


def test_b4_cheap_inventory_no_longer_mutes_siblings(monkeypatch):
    # THE live 2026-08-25 incident, pinned: a sibling FLAT join market with the event carrying
    # 40ct @ $0.02 ($0.80). Contract mode throttles it; dollar mode does not.
    _cfg(monkeypatch, dollars=1)
    qs = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=0.0, event_delta=-0.80)
    sides = {x["side"]: x for x in qs}
    assert sides["yes"]["count"] == 30 and sides["no"]["count"] == 40   # untouched full join
    # parity: the same 40ct at $0.35 basis ($14) still throttles the NO side
    qs2 = q.desired_quotes(_mkt(), _YL_Q, _NL_Q, q.utcnow(), inv=0.0, event_delta=-14.0)
    sides2 = {x["side"]: x for x in qs2}
    assert (sides2.get("no") is None or sides2["no"]["count"] < 40
            or sides2["no"]["price_dollars"] < 0.01)
