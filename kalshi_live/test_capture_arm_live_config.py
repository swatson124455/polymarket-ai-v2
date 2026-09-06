"""Stress pins for the 2026-08-25 gate config — LIVE knob values, LIVE book shapes.

History: first version pinned the 13:53Z CAPTURE_GATE arming (floor $1.00, QUALIFIABLE
bypassed) and documented the VOID-BYPASS known gap as P5. Operator rulings later the same
day (D1/D4): QUALIFIABLE_GATE re-armed with the D1 _addable clamp tightening (the R1-probe
refutation that justified the bypass was dissolved by the official-rules read, R3 doc), and
the capture floor raised to $2.00. P5 now pins the gap CLOSED under the armed gate; P5b
preserves the legacy bypass behavior under env=0.

Shapes are the measured window shapes: the 08-24 KXAAAGASW-26AUG31-3.900 book (D4 raw tape
17-20Z: yes 1,060ct at 0.98 incl. our 40, no 49ct at 0.01) and deep-rival / healed variants.
"""
from test_live_hardening import q


_YL_DEEP = [[0.98, 20000.0]]           # P1/P2: both sides clear Target, rivals dwarf us
_NL_DEEP = [[0.01, 20000.0]]
_YL_WALL = [[0.98, 1060.0]]            # healed-book pair: meaningful share
_NL_HEAL = [[0.01, 1100.0]]
_NL_THIN = [[0.01, 49.0]]              # the measured 08-24 gas shape (sub-Target side)
_YL_MID = [[0.50, 920.0]]              # P6: residual-band shape — gap 80 fundable in $
_NL_MID = [[0.49, 920.0]]              # (120ct at ~0.50) but above the 50ct INV_HARD clamp


def _mkt(usd_day=100.0, target=1000):
    return {"ticker": "KXAAAGASW-26AUG31-3.900", "target": target,
            # end is NOW-RELATIVE (+7d): hardcoded dates rotted past the wind-down gate on
            # 2026-09-06 (17-pin date-rot incident) — never pin a wall-clock end again.
            "end": (q.utcnow() + __import__("datetime").timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"), "usd_day": usd_day, "df": 0.5}


def _live_cfg(monkeypatch):
    """The knobs as ruled by the operator 2026-08-25 (D1/D4) on top of live.env."""
    monkeypatch.setattr(q, "CAPTURE_GATE", 1)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 1.00)  # D4 corrected to $1.00 (operator, 2026-08-25 ~16:5xZ: "$2" was a misread)
    monkeypatch.setattr(q, "QUALIFIABLE_GATE", True)     # D1 ruling: re-armed
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)           # live: 0 (ratified 08-19, unchanged)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.003)   # cliff band
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
    monkeypatch.setattr(q, "MIN_RUNWAY_H", 0.0)          # runway pins live in their own file


def test_p1_deep_rival_book_refused_flat(monkeypatch):
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_DEEP, _NL_DEEP, q.utcnow(), inv=0.0, stats=stats)
    assert qs == []                                    # pays us ~$0 -> no entry
    assert stats.get("capture_skipped") == 1           # refused BY CAPTURE (book qualifies)
    assert stats.get("capture_min_pc") < 1.0


def test_p2_holding_exit_still_rests_full_size(monkeypatch):
    _live_cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_DEEP, _NL_DEEP, q.utcnow(), inv=-40.0)
    sides = {x["side"]: x for x in qs}
    assert "yes" in sides and sides["yes"]["count"] == 40   # de-risk never down-sized
    assert all(x.get("reason") == "unwind" for x in qs)


def test_p3_healed_book_readmits_same_cycle(monkeypatch):
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_HEAL, q.utcnow(), inv=0.0, stats=stats)
    sides = {x["side"]: x for x in qs}
    # both sides reach Target; our share (~30/1060, ~40/1100 of $100/day ~= $3/day model)
    # clears the $1.00 floor -> the join returns with no sticky state.
    assert stats.get("capture_skipped") is None
    assert sides["yes"]["count"] == 30 and sides["no"]["count"] == 40
    assert sides["yes"]["price_dollars"] == 0.98 and sides["no"]["price_dollars"] == 0.01


def test_p4_capture_exactly_at_floor_is_admitted(monkeypatch):
    _live_cfg(monkeypatch)
    pc = q._prospective_capture(_mkt(), _YL_WALL, _NL_HEAL, 0.98, 0.01, 1000, own_orders=None)
    assert pc >= 1.00                                  # sanity: healed book clears the floor
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", pc)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_HEAL, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("capture_skipped") is None and qs   # `< floor` boundary admits


def test_p5_void_thin_side_now_refused_gate_armed(monkeypatch):
    # THE GAP, CLOSED (D1 ruling): thin NO side (49 < Target 1000) and the bridge we could
    # actually rest (min($60/0.98, INV_HARD 50) = 50ct) cannot reach Target -> unqualifiable
    # -> NOTHING rests. Before the ruling this book slipped down the activate path.
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_THIN, q.utcnow(), inv=0.0, stats=stats)
    assert qs == []
    assert stats.get("unqualifiable") == 1
    assert not any(x.get("reason") == "activate" for x in qs)


def test_p5b_legacy_bypass_preserved_under_env_zero(monkeypatch):
    # env KALSHI_QUALIFIABLE_GATE=0 must restore the pre-ruling behavior byte-for-byte:
    # the thin-side book takes the activate path (the documented former gap).
    _live_cfg(monkeypatch)
    monkeypatch.setattr(q, "QUALIFIABLE_GATE", False)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_THIN, q.utcnow(), inv=0.0, stats=stats)
    assert any(x.get("reason") == "activate" for x in qs)
    assert stats.get("unqualifiable") == 1             # stat still counts either way (:3092)
    cost = sum(x["price_dollars"] * x["count"] for x in qs)
    assert cost <= 60.0 + 1e-9


def test_p6_residual_band_closed_by_addable_clamp(monkeypatch):
    # D1 tightening pin: gap (80ct/side) IS fundable in dollars ($60/0.50 = 120ct) but sits
    # above the 50ct INV_HARD clamp the activate branch applies -> the old capital-only
    # "addable" would have admitted it; the clamped bridge refuses it.
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_MID, _NL_MID, q.utcnow(), inv=0.0, stats=stats)
    assert qs == []
    assert stats.get("unqualifiable") == 1


def test_p7_held_inventory_unwinds_through_unqualifiable_book(monkeypatch):
    # The armed gate is flat-only: held inventory in a sub-Target book still rests its
    # reducing quote (de-risk is never gated on reward).
    _live_cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_THIN, q.utcnow(), inv=-40.0)
    sides = {x["side"]: x for x in qs}
    assert "yes" in sides and sides["yes"]["count"] == 40
    assert all(x.get("reason") == "unwind" for x in qs)


def test_p8_max_price_side_disqualified(monkeypatch):
    # R4 fix pin (filing: best bid at the highest possible price -> NO qualifying bids).
    _live_cfg(monkeypatch)
    share, qual = q._qualifying_score([[0.99, 5000.0]], 0.99, 40, 1000, 0.5)
    assert (share, qual) == (0.0, False)
    share, qual = q._qualifying_score([[0.98, 5000.0]], 0.98, 40, 1000, 0.5)
    assert qual and share > 0.0                        # one tick lower is unaffected
    import kalshi_market_scorecard as sc
    assert sc.qualifying_share([[0.99, 5000.0]], 0.99, 40, 1000, 0.5) == (0.0, False)
