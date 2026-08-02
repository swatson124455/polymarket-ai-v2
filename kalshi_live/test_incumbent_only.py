"""INCUMBENT-ONLY GATE (operator directive 2026-08-01: "just stop doing new markets and
keep all else equal"; option c — a real gate — operator-named 2026-08-01).

Contract under test:
- OFF (default) is a provable no-op: no plan keys, no state keys, quotes untouched.
- ON: the incumbent set (standing + held + last cycle's resting) is captured ONCE,
  persisted to quoter_state, and survives later cycles unchanged (never grows).
- Non-incumbent + FLAT -> strips to [] (never opens). Non-incumbent + HELD -> unwind
  quotes survive (de-risk never blocked). Incumbents behave byte-identically.
- OFF after ON clears the captured set so a re-enable captures fresh.
- The knob is hot-reloadable via _refresh_safety_knobs."""
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_live_hardening import MockClient, _cfg, _run, _order, q  # noqa: E402


def _fp(*tickers):
    return [{"ticker": t, "usd_day": 100.0, "target": 1,
             "end": "2099-01-01T00:00:00Z"} for t in tickers]


def _arm(monkeypatch, footprint, incumbent_only=1):
    _cfg(monkeypatch, join=20, mktcap=250, totcap=200)
    monkeypatch.setattr(q, "HELD_MAX_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_LOSS_HALT_USD", 1e9)
    monkeypatch.setattr(q, "DAILY_DOWN_HALT_USD", 1e9)
    monkeypatch.setattr(q, "INCUMBENT_ONLY", incumbent_only)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: footprint)


def _state(tmp_path):
    p = os.path.join(str(tmp_path), "quoter_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def test_off_is_noop(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp("KXNEW-26AUG-A"), incumbent_only=0)
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert "incumbent_only_n" not in row
    assert "incumbent_only_set" not in _state(tmp_path)
    assert len(c.created) == 2, "flag off -> new market joins exactly as before"


def test_on_flat_book_captures_empty_and_opens_nothing(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp("KXNEW-26AUG-A"))
    c = MockClient(mode="live", positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("incumbent_only_n") == 0
    assert c.created == [], "no incumbents -> nothing may open"
    assert _state(tmp_path).get("incumbent_only_set") == []


def test_on_standing_market_stays_new_market_blocked(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp("KXINC-26AUG-A", "KXNEW-26AUG-B"))
    c = MockClient(mode="live", positions=[],
                   resting=[_order("o1", "KXINC-26AUG-A", "yes", 0.50, 10)])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("incumbent_only_n") == 1
    assert _state(tmp_path).get("incumbent_only_set") == ["KXINC-26AUG-A"]
    created_tickers = {o["ticker"] for o in c.created}
    assert "KXINC-26AUG-A" in created_tickers, "incumbent keeps quoting"
    assert "KXNEW-26AUG-B" not in created_tickers, "new market never opens"


def test_on_held_nonincumbent_still_unwinds(monkeypatch, tmp_path):
    # force the unreachable-in-practice case: a pre-seeded set that excludes a held market
    _arm(monkeypatch, _fp("KXHELD-26AUG-A"))
    monkeypatch.setattr(q, "INV_TOLERANCE", 3.0)
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"incumbent_only_set": []}, fh)
    c = MockClient(mode="live",
                   positions=[{"ticker": "KXHELD-26AUG-A", "position_fp": "10",
                               "market_exposure_dollars": "5.00"}])
    row = _run(monkeypatch, c, str(tmp_path))
    unwinds = [o for o in c.created if o["ticker"] == "KXHELD-26AUG-A"]
    assert unwinds, "held inventory must keep its reducing exit through the gate"
    assert row.get("incumbent_only_n") == 0


def test_set_is_sticky_across_cycles(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp("KXINC-26AUG-A"))
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"incumbent_only_set": ["KXOLD-26AUG-Z"]}, fh)
    c = MockClient(mode="live", positions=[],
                   resting=[_order("o1", "KXINC-26AUG-A", "yes", 0.50, 10)])
    _run(monkeypatch, c, str(tmp_path))
    assert _state(tmp_path).get("incumbent_only_set") == ["KXOLD-26AUG-Z"], \
        "captured set never re-captures or grows while the gate stays on"


def test_off_clears_captured_set(monkeypatch, tmp_path):
    _arm(monkeypatch, _fp("KXINC-26AUG-A"), incumbent_only=0)
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"incumbent_only_set": ["KXOLD-26AUG-Z"]}, fh)
    _run(monkeypatch, MockClient(mode="live", positions=[]), str(tmp_path))
    assert "incumbent_only_set" not in _state(tmp_path)


def test_knob_is_hot_reloadable():
    src = inspect.getsource(q._refresh_safety_knobs)
    assert '"KALSHI_INCUMBENT_ONLY": ("INCUMBENT_ONLY", int)' in src


def test_ladder_escape_hatch_is_incumbent_gated():
    # Blind review 2026-08-01 (lens A #1 / lens B I5): the ladder hatch OPENS a position on
    # an adjacent strike AFTER the quote-loop strip — the one path that could open a new
    # market through the gate (latent today only because unwind pricing rests at/above the
    # touch). The invariant must not depend on pricing internals: the hatch carries the
    # same incumbent guard as the F6b exit-only gate beside it.
    src = inspect.getsource(q)
    i = src.index('"count": cross, "reason": "ladder"')
    window = src[i - 2200:i]
    assert "_incumbent_only is not None and t2 not in _incumbent_only" in window
    assert window.count("ladder_cross_gated") >= 2   # both gates count into the same key
