"""Tests for r1_floor_probe.py — the R1 floor-probe standalone script.

No network: every venue read is monkeypatched. The mutating paths are tested for
their REFUSALS first (that is the safety surface), then happy paths against fake
clients that record calls. Each 2026-08-13 review finding that changed behavior
has a test pinning the fix (noted inline as B# / M#).
"""
import datetime
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r1_floor_probe as rp                                    # noqa: E402


NOW = datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.isoformat()


# ---------------- book_refs / last_price_anchor / side_price ----------------

class _BookClient:
    def __init__(self, book):
        self._book = book

    def get_orderbook(self, ticker):
        return {"orderbook_fp": self._book}


def test_book_refs_two_sided():
    cl = _BookClient({"yes_dollars": [["0.30", "5"], ["0.28", "9"]],
                      "no_dollars": [["0.60", "5"]]})
    yb, nb, why = rp.book_refs(cl, "T")
    assert (yb, nb, why) == (0.30, 0.60, "ok")


def test_book_refs_read_failure():
    class Boom:
        def get_orderbook(self, t):
            raise RuntimeError("down")
    yb, nb, why = rp.book_refs(Boom(), "T")
    assert yb is None and nb is None and why == "book_read_failed"


def test_last_price_anchor_units_and_band():
    assert abs(rp.last_price_anchor({"last_price": 42}) - 0.42) < 1e-9
    assert rp.last_price_anchor({"last_price": 1}) is None      # 1c near-settled
    assert rp.last_price_anchor({}) is None


def test_side_price_joins_ref_at_n_zero():
    """Score discipline: with a rival ref inside the cap we JOIN AT REF (N=0),
    never a clamped price ticks below it."""
    p, lbl = rp.side_price(0.30, None, False)
    assert (p, lbl) == (0.30, "join_ref")


def test_side_price_refuses_ref_above_cap():
    p, lbl = rp.side_price(0.94, 0.5, False)
    assert p is None and "above_cap" in lbl


def test_side_price_empty_side_sets_ref_from_anchor():
    p, lbl = rp.side_price(None, 0.42, False)
    assert lbl == "set_ref_from_anchor"
    assert abs(p - 0.37) < 1e-9                    # anchor − MARGIN
    p2, _ = rp.side_price(None, 0.42, True)        # stale last_price anchor
    assert p2 <= rp.STALE_ANCHOR_PX_CAP + 1e-9
    p3, lbl3 = rp.side_price(None, None, False)
    assert p3 is None and lbl3 == "empty_side_no_anchor"


# ---------------- validate_orders (M: mutation-boundary re-validation) ----------------

def _order(**kw):
    o = {"ticker": "KXTEST-1", "y_price": 0.20, "n_price": 0.40, "count": rp.PROBE_CT}
    o.update(kw)
    return o


def test_validate_ok():
    ok, why = rp.validate_orders([_order()])
    assert ok, why


def test_validate_rejects_oversize_count():
    ok, why = rp.validate_orders([_order(count=1000)])
    assert not ok and "PROBE_CT" in why


def test_validate_rejects_price_above_cap():
    ok, _ = rp.validate_orders([_order(y_price=0.99)])
    assert not ok


def test_validate_rejects_pair_at_or_above_one():
    ok, _ = rp.validate_orders([_order(y_price=0.45, n_price=0.56)])
    assert not ok


def test_validate_rejects_too_many_markets():
    ok, _ = rp.validate_orders([_order(ticker=f"KX-{i}") for i in range(3)])
    assert not ok


def test_validate_rejects_malformed_row():
    ok, _ = rp.validate_orders([{"ticker": "KX", "y_price": "abc"}])
    assert not ok


def test_validate_recomputes_collateral_from_prices_not_flags():
    # two markets at the price caps: (0.45+0.45)*10*2 = $18 <= $20 passes...
    ok, _ = rp.validate_orders([_order(ticker="A", y_price=0.45, n_price=0.45),
                                _order(ticker="B", y_price=0.45, n_price=0.45)])
    assert ok
    # ...but a crafted count pushes the RECOMPUTED total over the cap regardless
    # of any stored 'within_cap' flag (review BLOCKER B2)
    ok, why = rp.validate_orders([_order(count=25)])
    assert not ok and "PROBE_CT" in why


# ---------------- place() refusals ----------------

def _args(**kw):
    ns = types.SimpleNamespace(operator_go="", census="", tickers="")
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _fresh_plan(tmp_path, monkeypatch, orders=None, age_s=0):
    plan = {"generated": _iso(NOW - datetime.timedelta(seconds=age_s)),
            "orders": orders if orders is not None else [_order()],
            "valid": True}
    pf = tmp_path / "plan.json"
    pf.write_text(json.dumps(plan))
    monkeypatch.setattr(rp, "PLAN_F", str(pf))
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "state.json"))
    monkeypatch.setattr(rp, "STOP_F", str(tmp_path / "STOP"))
    monkeypatch.setattr(rp, "quoter_inactive", lambda: True)
    monkeypatch.setattr(rp, "probe_program_ids", lambda kal, t: {k: "pid-1" for k in t})
    monkeypatch.setattr(rp, "latest_estimates_snapshot", lambda: {
        "ts": _iso(NOW), "estimates": [{"program_id": "pid-1", "reward_centicents": 100}]})
    return plan


def test_place_refuses_without_go(tmp_path, monkeypatch):
    _fresh_plan(tmp_path, monkeypatch)
    assert rp.place(_args(operator_go="")) == 2
    assert rp.place(_args(operator_go="go")) == 2       # case-exact one word
    assert rp.place(_args(operator_go="GO please")) == 2


def test_place_refuses_with_stop_file(tmp_path, monkeypatch):
    _fresh_plan(tmp_path, monkeypatch)
    (tmp_path / "STOP").write_text("halt")
    assert rp.place(_args(operator_go="GO")) == 2


def test_place_refuses_when_quoter_active(tmp_path, monkeypatch):
    _fresh_plan(tmp_path, monkeypatch)
    monkeypatch.setattr(rp, "quoter_inactive", lambda: False)
    assert rp.place(_args(operator_go="GO")) == 2


def test_place_refuses_stale_plan(tmp_path, monkeypatch):
    _fresh_plan(tmp_path, monkeypatch, age_s=3600)
    assert rp.place(_args(operator_go="GO")) == 2


def test_place_refuses_existing_state(tmp_path, monkeypatch):
    _fresh_plan(tmp_path, monkeypatch)
    (tmp_path / "state.json").write_text("{}")
    assert rp.place(_args(operator_go="GO")) == 2


def test_place_refuses_adversarial_plan(tmp_path, monkeypatch):
    """Review BLOCKER B2: a hand-edited plan with big count/price must be refused
    at the mutation boundary regardless of its own flags; zero orders sent."""
    calls = []

    class Rec:
        def create_quote(self, *a, **kw):
            calls.append(a)
    monkeypatch.setattr(rp, "client", lambda mode: Rec())
    for bad in ([_order(count=1000)], [_order(y_price=0.99, n_price=0.99)],
                [_order(ticker=f"KX-{i}") for i in range(3)]):
        _fresh_plan(tmp_path, monkeypatch, orders=bad)
        assert rp.place(_args(operator_go="GO")) == 2
        assert calls == []
        sf = tmp_path / "state.json"
        if sf.exists():
            sf.unlink()


# ---------------- place() happy + partial paths ----------------

class _RecClient:
    def __init__(self):
        self.calls = []

    def create_quote(self, ticker, outcome, price, count, post_only=True,
                     client_order_id=None):
        assert post_only is True          # the probe may NEVER take liquidity
        self.calls.append((ticker, outcome, price, count))
        return {"order": {"order_id": f"oid-{len(self.calls)}"}}


def test_place_happy_path_places_paired_and_writes_state(tmp_path, monkeypatch):
    _fresh_plan(tmp_path, monkeypatch)
    rec = _RecClient()
    seen_mode = []

    def mk(mode):
        seen_mode.append(mode)
        return rec
    monkeypatch.setattr(rp, "client", mk)
    assert rp.place(_args(operator_go="GO")) == 0
    assert seen_mode == ["live"]          # review T4: must construct in live mode
    assert [(c[0], c[1]) for c in rec.calls] == [("KXTEST-1", "yes"), ("KXTEST-1", "no")]
    st = json.loads((tmp_path / "state.json").read_text())
    assert len(st["orders"]) == 2
    assert st["orders"][0]["order_id"] == "oid-1"
    assert st["program_ids"] == {"KXTEST-1": "pid-1"}
    assert st["estimates_baseline"] == {"pid-1": 0.01}
    assert st["t0"] <= st["orders"][0]["ts"]        # M: t0 stamped BEFORE placing


def test_place_partial_failure_exits_4_and_keeps_placed_orders(tmp_path, monkeypatch):
    """Review M: the second order failing must (a) not raise a bare traceback,
    (b) keep the first order in state so halt can find it."""
    _fresh_plan(tmp_path, monkeypatch)

    class Half(_RecClient):
        def create_quote(self, *a, **kw):
            if self.calls:
                raise RuntimeError("venue 500")
            return super().create_quote(*a, **kw)
    monkeypatch.setattr(rp, "client", lambda mode: Half())
    assert rp.place(_args(operator_go="GO")) == 4
    st = json.loads((tmp_path / "state.json").read_text())
    assert len(st["orders"]) == 1


def test_state_written_before_first_order(tmp_path, monkeypatch):
    """Review BLOCKER B3: if the FIRST create_quote dies ambiguously, the state
    file must already exist with the planned tickers, so halt can scope to them."""
    _fresh_plan(tmp_path, monkeypatch)

    class Boom:
        def create_quote(self, *a, **kw):
            raise RuntimeError("timeout — ambiguous")
    monkeypatch.setattr(rp, "client", lambda mode: Boom())
    assert rp.place(_args(operator_go="GO")) == 4
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "state.json"))
    tickers, st, cond = rp.probe_tickers()
    assert cond == "ok"
    assert tickers == ["KXTEST-1"]        # planned ticker visible despite 0 placed


# ---------------- probe_tickers state conditions ----------------

def test_probe_tickers_absent_vs_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "none.json"))
    assert rp.probe_tickers() == ([], {}, "absent")
    (tmp_path / "bad.json").write_text("{torn")
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "bad.json"))
    t, st, cond = rp.probe_tickers()
    assert cond == "corrupt" and t == []


def test_probe_tickers_unions_placed_and_planned(tmp_path, monkeypatch):
    st = {"orders": [{"ticker": "A"}],
          "plan": {"orders": [{"ticker": "A"}, {"ticker": "B"}]}}
    (tmp_path / "s.json").write_text(json.dumps(st))
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "s.json"))
    t, _, cond = rp.probe_tickers()
    assert cond == "ok" and t == ["A", "B"]


# ---------------- halt scoping (review BLOCKERs B1 + B4) ----------------

class _HaltClient:
    """get_orders returns the client's REAL shape: a dict with an 'orders' list."""
    def __init__(self, orders):
        self._orders = orders
        self.cancelled = []

    def get_orders(self, status="resting"):
        return {"orders": [o for o in self._orders
                           if o["order_id"] not in self.cancelled], "cursor": ""}

    def cancel_order(self, oid):
        self.cancelled.append(oid)

    def get_positions(self):
        return {"market_positions": []}


def test_halt_refuses_without_state(tmp_path, monkeypatch, capsys):
    """Review B4: no state must NEVER become an account-wide cancel."""
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "none.json"))
    cl = _HaltClient([{"ticker": "QUOTER-MKT", "order_id": "q1"}])
    monkeypatch.setattr(rp, "client", lambda mode: cl)
    assert rp.halt(_args()) == 2
    assert cl.cancelled == []


def test_halt_refuses_on_corrupt_state(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{torn")
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "bad.json"))
    cl = _HaltClient([{"ticker": "QUOTER-MKT", "order_id": "q1"}])
    monkeypatch.setattr(rp, "client", lambda mode: cl)
    assert rp.halt(_args()) == 2
    assert cl.cancelled == []


def test_halt_cancels_only_probe_tickers(tmp_path, monkeypatch):
    st = {"orders": [{"ticker": "A"}], "plan": {"orders": [{"ticker": "A"}]}}
    (tmp_path / "s.json").write_text(json.dumps(st))
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "s.json"))
    cl = _HaltClient([{"ticker": "A", "order_id": "a1"},
                      {"ticker": "A", "order_id": "a2"},
                      {"ticker": "QUOTER-MKT", "order_id": "q1"}])
    monkeypatch.setattr(rp, "client", lambda mode: cl)
    assert rp.halt(_args()) == 0
    assert sorted(cl.cancelled) == ["a1", "a2"]


def test_halt_arming_error_is_remediation_not_traceback(tmp_path, monkeypatch):
    st = {"orders": [{"ticker": "A"}], "plan": {"orders": [{"ticker": "A"}]}}
    (tmp_path / "s.json").write_text(json.dumps(st))
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "s.json"))

    def raise_arm(mode):
        raise RuntimeError("live mode requires KALSHI_LIVE_ARMED (operator act)")
    monkeypatch.setattr(rp, "client", raise_arm)
    assert rp.halt(_args()) == 2


def test_resting_orders_uses_client_wrapper_shape():
    """Review BLOCKER B1: the resting read must use get_orders() and unwrap the
    dict — the old raw-path read 404'd and iterated dict keys."""
    cl = _HaltClient([{"ticker": "A", "order_id": "a1"}])
    out = rp.resting_orders(cl)
    assert out == [{"ticker": "A", "order_id": "a1"}]


# ---------------- quoter gate fails closed (review M) ----------------

def _fake_run(stdout, rc):
    def run(cmd, capture_output=True, text=True):
        return types.SimpleNamespace(stdout=stdout, returncode=rc)
    return run


def test_quoter_gate_passes_only_inactive_or_failed(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run("inactive\n", 3))
    assert rp.quoter_inactive() is True
    monkeypatch.setattr(subprocess, "run", _fake_run("failed\n", 3))
    assert rp.quoter_inactive() is True
    for out, rc in (("active\n", 0), ("activating\n", 3), ("unknown\n", 4), ("", 1)):
        monkeypatch.setattr(subprocess, "run", _fake_run(out, rc))
        assert rp.quoter_inactive() is False, (out, rc)


# ---------------- estimates plumbing ----------------

def test_estimates_for_programs_filters_and_sums():
    snap = {"estimates": [{"program_id": "p1", "reward_centicents": 150},
                          {"program_id": "p1", "reward_centicents": 50},
                          {"program_id": "p2", "reward_centicents": 999}]}
    out = rp.estimates_for_programs(snap, {"p1"})
    assert out == {"p1": 0.02}


def test_latest_estimates_falls_back_over_empty_rollover(tmp_path, monkeypatch):
    old = tmp_path / "estimates-202607.jsonl"
    old.write_text(json.dumps({"ts": "2026-07-31T23:00:00+00:00", "estimates": []}) + "\n")
    (tmp_path / "estimates-202608.jsonl").write_text("")      # fresh rollover, empty
    monkeypatch.setattr(rp, "DATA", str(tmp_path))
    monkeypatch.setattr(rp, "glob", __import__("glob"))
    snap = rp.latest_estimates_snapshot()
    assert snap.get("ts") == "2026-07-31T23:00:00+00:00"


# ---------------- pricing invariants ----------------

def test_plan_prices_pair_below_one_and_stale_cap():
    for anchor in (0.05, 0.20, 0.50, 0.80, 0.95):
        for src_cap in (rp.PRICE_MAX, rp.STALE_ANCHOR_PX_CAP):
            y = max(rp.PRICE_MIN, min(src_cap, round(anchor - rp.MARGIN, 2)))
            n = max(rp.PRICE_MIN, min(src_cap, round((1.0 - anchor) - rp.MARGIN, 2)))
            assert rp.PRICE_MIN <= y <= rp.PRICE_MAX
            assert rp.PRICE_MIN <= n <= rp.PRICE_MAX
            assert y + n < 1.0


def test_collateral_cap_arithmetic():
    worst = (rp.PRICE_MAX * 2) * rp.PROBE_CT * rp.MAX_MARKETS
    assert worst <= rp.COLLATERAL_CAP_USD + 1e-9


# ---------------- status ----------------

def test_status_no_state_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "none.json"))
    assert rp.status(_args()) == 0


def test_status_corrupt_state_is_loud_exit_2(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{torn")
    monkeypatch.setattr(rp, "STATE_F", str(tmp_path / "bad.json"))
    assert rp.status(_args()) == 2
