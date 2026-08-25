"""Stress pins for the 2026-08-25 CAPTURE_GATE arming — the LIVE config, the LIVE book shapes.

test_capture_gate.py pins the mechanism at floor $5 / 100-ct joins. These pins re-run it at the
values actually armed on the box 2026-08-25T13:53Z (floor $1.00, JOIN 40, MAX_MARKET_CAPITAL 60,
QUALIFIABLE_GATE bypassed, cliff band 0.003/0.995) against the shapes measured this window:

  P1  JOIN path, wall-dilution shape (both sides reach Target, deep rival walls, our share ~0
      -> pc < $1): armed gate refuses the flat entry via capture_skipped.
  P2  Holding in that book: the EXIT still rests at full |inv| (de-risk never blocked).
  P3  The book heals (thin side reaches Target, our share meaningful): the gate re-admits the
      full two-sided join THE SAME CYCLE — no sticky state (the "reenter" property).
  P4  Boundary: capture exactly AT the floor is admitted (`< floor`, not `<=`).
  P5  KNOWN GAP, pinned as CURRENT behavior (2026-08-25 stress-test finding, doc
      KALSHI_R3_TARGET_RULE_AND_CAPTURE_ARM §status): a thin-but-NONEMPTY side (< Target)
      classifies the book VOID (:3072) -> ACTIVATE path -> the capture gate is scoped out
      (`not void`) and does NOT protect. This is the exact 08-24 gas shape (NO side 49ct).
      Exposure there is bounded by MAX_ACTIVATE_CAPITAL=$60/market only. If this pin ever
      FAILS because activate became capture-gated, that is a deliberate design change —
      update the doc trail, don't silently re-pin.
"""
from test_live_hardening import q


# P1/P2 shape — both sides clear Target 1000 but rival depth dwarfs our 30-40ct join:
# share ~ 40/20000 -> pc ~ $100 * ~0.002 avg << $1 floor.
_YL_DEEP = [[0.98, 20000.0]]
_NL_DEEP = [[0.01, 20000.0]]
# P3/P4 shape — thin-side book healed to Target: our join is a meaningful share.
_YL_WALL = [[0.98, 1060.0]]
_NL_HEAL = [[0.01, 1100.0]]
# P5 shape — the measured 08-24 KXAAAGASW-26AUG31-3.900 book (D4 raw tape 17-20Z).
_NL_THIN = [[0.01, 49.0]]


def _mkt(usd_day=100.0, target=1000):
    return {"ticker": "KXAAAGASW-26AUG31-3.900", "target": target,
            "end": "2026-08-31T03:59:00Z", "usd_day": usd_day, "df": 0.5}


def _live_cfg(monkeypatch):
    """The knobs as armed on the box 2026-08-25T13:53Z (live.env reads 13:53:22Z/14:0xZ)."""
    monkeypatch.setattr(q, "CAPTURE_GATE", 1)
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", 1.00)
    monkeypatch.setattr(q, "QUALIFIABLE_GATE", False)    # live: KALSHI_QUALIFIABLE_GATE=0
    monkeypatch.setattr(q, "CAPTURE_DF_DEFAULT", 0.5)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "STANDDOWN", 0)
    monkeypatch.setattr(q, "PRESENCE_GATE", 0)           # live: 0 (ratified 08-19)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.003)   # cliff band
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.995)
    monkeypatch.setattr(q, "MIN_DEPTH_SYM", 0.0)         # isolate capture from the sym gate
    monkeypatch.setattr(q, "MAX_SPREAD_TICKS", 8)
    monkeypatch.setattr(q, "JOIN_SIZE", 40)              # live
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "INV_SOFT_CT", 15.0)          # live
    monkeypatch.setattr(q, "INV_HARD_CT", 50.0)          # live
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)         # live
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 60.0)   # live F15 per-market cap
    monkeypatch.setattr(q, "MAX_ACTIVATE_CAPITAL", 60.0)  # live


def test_p1_deep_rival_book_refused_flat(monkeypatch):
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_DEEP, _NL_DEEP, q.utcnow(), inv=0.0, stats=stats)
    assert qs == []                                    # no entry on a book that pays us ~$0
    assert stats.get("capture_skipped") == 1           # refused BY CAPTURE, not a fallthrough
    assert stats.get("capture_min_pc") < 1.0           # sub-floor share, both sides qualifying


def test_p2_holding_exit_still_rests_full_size(monkeypatch):
    _live_cfg(monkeypatch)
    qs = q.desired_quotes(_mkt(), _YL_DEEP, _NL_DEEP, q.utcnow(), inv=-40.0)
    sides = {x["side"]: x for x in qs}
    assert "yes" in sides                              # reducing side of a -40 (long NO) position
    assert sides["yes"]["count"] == 40                 # full |inv| — de-risk never down-sized
    assert all(x.get("reason") == "unwind" for x in qs)  # nothing accumulating while holding


def test_p3_healed_book_readmits_same_cycle(monkeypatch):
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_HEAL, q.utcnow(), inv=0.0, stats=stats)
    sides = {x["side"]: x for x in qs}
    # both sides reach Target and our share is meaningful (~30/1060, ~40/1100 of $100/day
    # ≈ $3/day model) >= the $1.00 floor -> the join comes back with no state to clear.
    assert stats.get("capture_skipped") is None
    assert sides["yes"]["count"] == 30                 # int((60/2)/0.98) — per-side $ cap
    assert sides["no"]["count"] == 40                  # JOIN_SIZE cap at 0.01
    assert sides["yes"]["price_dollars"] == 0.98 and sides["no"]["price_dollars"] == 0.01


def test_p4_capture_exactly_at_floor_is_admitted(monkeypatch):
    _live_cfg(monkeypatch)
    pc = q._prospective_capture(_mkt(), _YL_WALL, _NL_HEAL, 0.98, 0.01, 1000, own_orders=None)
    assert pc >= 1.00                                  # sanity: healed book clears the floor
    monkeypatch.setattr(q, "CAPTURE_MIN_USD_DAY", pc)  # raise the floor to exactly pc
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_HEAL, q.utcnow(), inv=0.0, stats=stats)
    assert stats.get("capture_skipped") is None and qs   # `< floor` boundary admits


def test_p5_void_thin_side_bypasses_capture_KNOWN_GAP(monkeypatch):
    _live_cfg(monkeypatch)
    stats = {}
    qs = q.desired_quotes(_mkt(), _YL_WALL, _NL_THIN, q.utcnow(), inv=0.0, stats=stats)
    # thin side (49 < Target) -> void -> ACTIVATE path; capture gate scoped out (`not void`).
    # The book that motivated the arming is NOT protected by it — pinned so the gap is visible.
    assert stats.get("capture_skipped") is None        # capture never ran
    assert any(x.get("reason") == "activate" for x in qs)  # entered via activate regardless
    cost = sum(x["price_dollars"] * x["count"] for x in qs)
    assert cost <= 60.0 + 1e-9                         # only MAX_ACTIVATE_CAPITAL bounds it
