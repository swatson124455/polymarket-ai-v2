"""D-C macro-probe (operator-ruled 2026-08-06) — failing-before tests.

The 5ct probe is formula-invisible in empty books (LIP: a side qualifies only when its
resting depth reaches Target Size, 1000ct on 3,755/3,802 programs) — so receipts can never
arrive from exactly the LOW-competition empty pools the thesis targets. The macro-probe
rests a TWO-SIDED deep-discount ladder meeting Target on operator-DESIGNATED tickers
(KALSHI_MACRO_PROBE_TICKERS, exact tickers, default empty = OFF), dollar-capped per market
(KALSHI_MACRO_PROBE_USD). Deep-discount levels mean: reserved dollars ARE the max loss, any
fill is at a large discount to fair, and per D-F the ladder spans 3 real price levels,
never a 1c-only wall. Flat-only; held inventory routes to the standard reducing path.
"""
import datetime as dt

import maker_kalshi_quoter as q


def _now():
    return dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=dt.timezone.utc)


def _mkt(ticker="KXMACRO-26AUG08-T1", target=1000.0, macro=True):
    m = {"ticker": ticker, "target": target, "df": 0.5, "usd_day": 100.0,
         "end": (_now() + dt.timedelta(days=2)).isoformat(), "life_min": 5 * 1440.0}
    if macro:
        m["macro"] = True
    return m


def _cfg(monkeypatch, tickers, usd=60.0, top=0.03):
    monkeypatch.setattr(q, "MACRO_PROBE_TICKERS", frozenset(tickers))
    monkeypatch.setattr(q, "MACRO_PROBE_USD", usd)
    monkeypatch.setattr(q, "MACRO_PROBE_TOP", top)
    monkeypatch.setattr(q, "W12_PRICE_SHAPE", 0)
    monkeypatch.setattr(q, "SETTLE_UNWIND_MIN", 30)
    # review #5: the per-side cap is min(MACRO_PROBE_USD/2, 0.9*HELD_MAX_USD) — arm both
    # knobs coherently; the fixture raises the held ceiling so the $60 default is testable
    monkeypatch.setattr(q, "HELD_MAX_USD", 40.0)


_EMPTYISH_Y = [(0.02, 10.0)]      # near-empty book: 10ct resting each side
_EMPTYISH_N = [(0.02, 10.0)]


def test_flag_off_default_is_noop(monkeypatch):
    _cfg(monkeypatch, ())
    out = q.desired_quotes(_mkt(macro=False), _EMPTYISH_Y, _EMPTYISH_N, _now(), inv=0.0)
    # not designated -> whatever legacy does, but never a macro_probe reason
    assert all(o.get("reason") != "macro_probe" for o in out)


def test_designated_flat_rests_two_sided_target_ladder(monkeypatch):
    _cfg(monkeypatch, ("KXMACRO-26AUG08-T1",))
    m = _mkt()
    out = q.desired_quotes(m, _EMPTYISH_Y, _EMPTYISH_N, _now(), inv=0.0, stats={})
    mac = [o for o in out if o.get("reason") == "macro_probe"]
    assert mac, out
    for side in ("yes", "no"):
        rows = [o for o in mac if o["side"] == side]
        assert len(rows) >= 2, f"{side}: ladder must span multiple levels, got {rows}"
        total = sum(o["count"] for o in rows)
        # our resting + the 10ct external must reach Target
        assert total + 10.0 >= m["target"], (side, total)
        assert all(0.0 < o["price_dollars"] <= q.MACRO_PROBE_TOP + 1e-9 for o in rows)
        cost = sum(o["price_dollars"] * o["count"] for o in rows)
        assert cost <= q.MACRO_PROBE_USD / 2.0 + 1e-6, (side, cost)


def test_designated_with_inventory_reduces_only(monkeypatch):
    _cfg(monkeypatch, ("KXMACRO-26AUG08-T1",))
    out = q.desired_quotes(_mkt(), [(0.02, 10.0)], [(0.90, 10.0)], _now(), inv=40.0,
                           stats={})
    assert all(o.get("reason") != "macro_probe" for o in out)


def test_unaffordable_target_rests_nothing(monkeypatch):
    """If the dollar cap cannot reach Target on a side, resting a partial ladder is pure
    fill risk with zero qualification — skip the market and count it."""
    _cfg(monkeypatch, ("KXMACRO-26AUG08-T1",), usd=5.0)   # $2.50/side can't buy ~990ct at 1-3c
    stats = {}
    out = q.desired_quotes(_mkt(), _EMPTYISH_Y, _EMPTYISH_N, _now(), inv=0.0, stats=stats)
    assert all(o.get("reason") != "macro_probe" for o in out)
    assert stats.get("macro_probe_unaffordable", 0) >= 1
