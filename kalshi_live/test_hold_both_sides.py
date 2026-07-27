"""HOLD BOTH SIDES — a double fill must land PAIRED, in every regime (2026-07-26).

The block's own design comment says: "Position control is done by SKEW, not by removing a
quote: shrink+step-in the accumulating side, grow the reducing side. Both stay live."
The implementation did not deliver it. The reducing side was sized
`_unwind_size(_capped_join(...), price, inv)` = min(|inv|, room) off the NOMINAL join, so it
ignored whatever shaping had already run, and the throttle that was supposed to shrink the
adding side only fires above INV_SOFT_CT.

Measured before the fix (JOIN=100, MIN_QUOTE_CT=2, MAX_MARKET_CAPITAL=250):

    regime               inv   ADD   RED   net after double fill
    below SOFT             8   100     8   +100     <- the live case
    above SOFT            40    44    40    +44
    stand-down            40     2    40     +2
    hard stop             90     0    90      0
    flat                   0   100   100      0
    short below SOFT      -8   100     8   -100     <- the live case

Live consequence 2026-07-26T23:37:07Z on KXCLUBFSPREAD-26JUL26ERKHIL-ERK2 holding +8 YES:
resting YES bid 20 (full join, ADDING) vs NO bid 8 — set up to get 2.5x longer before the
pair could ever complete.

FIX: RED = ADD + |inv| where ADD is the count AFTER stand-down/ramp/throttle, plus a clamp of
ADD to RED - |inv| for when `room` caps RED (it does in production: MAX_MARKET_CAPITAL=15 ->
room ~23). `_unwind_size` is untouched and still caps PURE unwinds at |inv|.
"""
from test_live_hardening import MockClient, _run, q
from test_live_hardening import legacy_inventory_mode  # noqa: E402

M = {"target": 1000, "end": "2099-01-01T00:00:00Z", "usd_day": 100.0, "ramp_min": 180}


def _cfg(monkeypatch, join=100, mktcap=250.0, soft=15.0, hard=60.0, standdown=False):
    monkeypatch.setattr(q, "JOIN_SIZE", join)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", mktcap)
    monkeypatch.setattr(q, "MIN_QUOTE_CT", 2)
    monkeypatch.setattr(q, "INV_TOLERANCE", 1.0)
    monkeypatch.setattr(q, "INV_SOFT_CT", soft)
    monkeypatch.setattr(q, "INV_HARD_CT", hard)
    monkeypatch.setattr(q, "STANDDOWN", standdown)
    monkeypatch.setattr(q, "STANDDOWN_MIN_USD_DAY", 20.0)
    monkeypatch.setattr(q, "PAIR_BOTH_SIDES", True)
    monkeypatch.setattr(q, "MIN_PRICE_DOLLARS", 0.04)
    monkeypatch.setattr(q, "MAX_PRICE_DOLLARS", 0.96)


def _sides(inv, m=None):
    qs = q.desired_quotes(m or M, [["0.33", "5000"]], [["0.65", "5000"]],
                          q.utcnow(), inv=float(inv), cost=0.33)
    d = {x["side"]: x for x in qs}
    return d.get("yes", {}).get("count", 0), d.get("no", {}).get("count", 0)


# ---- THE invariant: a double fill returns us to flat -----------------------------------

def test_double_fill_lands_paired_below_soft(monkeypatch):
    _cfg(monkeypatch)
    y, n = _sides(8)
    assert 8 + y - n == 0, f"yes={y} no={n} -> net {8 + y - n}"
    assert n > y, "the REDUCING side must be the larger one when long"


def test_double_fill_lands_paired_above_soft(monkeypatch):
    _cfg(monkeypatch)
    y, n = _sides(40)
    assert 40 + y - n == 0


def test_double_fill_lands_paired_under_standdown(monkeypatch):
    _cfg(monkeypatch, soft=30.0, hard=80.0, standdown=True)
    y, n = _sides(40, {"target": 1000, "end": "2099-01-01T00:00:00Z",
                       "usd_day": 5.0, "ramp_min": 180})
    assert y <= 2, "stand-down must still shrink the ACCUMULATING side"
    assert 40 + y - n == 0, f"yes={y} no={n}"


def test_double_fill_lands_paired_short(monkeypatch):
    _cfg(monkeypatch)
    y, n = _sides(-8)
    assert -8 + y - n == 0
    assert y > n, "the REDUCING side must be the larger one when short"


def test_double_fill_paired_across_a_sweep(monkeypatch):
    _cfg(monkeypatch)
    for inv in (1, 2, 5, 8, 14, 15, 16, 25, 40, -1, -5, -8, -16, -40):
        y, n = _sides(inv)
        assert inv + y - n == 0, f"inv={inv}: yes={y} no={n} -> net {inv + y - n}"


# ---- both sides stay live ---------------------------------------------------------------

def test_both_sides_stay_live_while_holding(monkeypatch):
    legacy_inventory_mode(monkeypatch)
    _cfg(monkeypatch)
    for inv in (1, 8, 25, -1, -8, -25):
        y, n = _sides(inv)
        assert y > 0 and n > 0, f"inv={inv} dropped a side: yes={y} no={n}"


def test_hard_stop_still_pulls_the_adding_side_to_zero(monkeypatch):
    # UNCHANGED behaviour: above HARD the accumulating side is pulled to 0 outright.
    _cfg(monkeypatch)
    y, n = _sides(90)
    assert y == 0 and n == 90


def test_flat_is_unchanged(monkeypatch):
    _cfg(monkeypatch)
    y, n = _sides(0)
    assert y == n == 100


# ---- the legacy defect must not come back ----------------------------------------------

def test_flag_off_reproduces_the_legacy_sizing(monkeypatch):
    legacy_inventory_mode(monkeypatch)
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "PAIR_BOTH_SIDES", False)
    y, n = _sides(8)
    assert n == 8, "flag OFF must restore min(|inv|, room)"
    assert y == 100 and n < y, "which IS the defect: adding 100 while reducing 8"
    assert 8 + y - n == 100, "and a double fill leaves us +100"


# ---- room cap: never grow the imbalance --------------------------------------------------

def test_when_room_caps_the_reducing_side_the_adding_side_is_clamped(monkeypatch):
    # PRODUCTION shape: JOIN=20, MAX_MARKET_CAPITAL=15 -> room ~23 at 0.65.
    _cfg(monkeypatch, join=20, mktcap=15.0)
    for inv in (5, 8, 20):
        y, n = _sides(inv)
        assert inv + y - n == 0, f"inv={inv}: yes={y} no={n}"
        assert n * 0.65 <= 15.0 + 1e-9, "must stay inside MAX_MARKET_CAPITAL"


def test_adding_stops_entirely_when_room_cannot_cover_inventory(monkeypatch):
    # room (~23) < |inv| (40): we cannot hedge the position, so we must not add to it.
    _cfg(monkeypatch, join=20, mktcap=15.0)
    y, n = _sides(40)
    assert y == 0, "must stop adding when the reducing side cannot cover inventory"


# ---- pure unwinds are UNTOUCHED ----------------------------------------------------------

def test_unwind_size_still_caps_pure_exits_at_inv(monkeypatch):
    _cfg(monkeypatch)
    for inv in (1.0, 8.0, 60.0):
        assert q._unwind_size(100, 0.50, inv) <= int(abs(inv))


def test_reducing_quotes_helper_still_capped_at_inv(monkeypatch):
    # wind-down / gate exits have STOPPED quoting: pure unwind, still |inv|-capped.
    _cfg(monkeypatch)
    got = q._reducing_quotes(0.33, 0.65, 8.0, 0.33)
    assert got and got[0]["count"] <= 8
