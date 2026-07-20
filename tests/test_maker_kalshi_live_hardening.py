"""Tests for the LIVE-path capital-safety hardening (NO_GO must-fixes).
Run: python -m pytest test_live_hardening.py -q  (from the probe dir)."""
import importlib.util, os, sys, tempfile, json

import pathlib as _pl
_S = _pl.Path(__file__).resolve().parents[1] / "scripts"
def _load(n):
    s = importlib.util.spec_from_file_location(n, str(_S / f"{n}.py"))
    m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m); return m

q = _load("maker_kalshi_quoter")


class MockClient:
    def __init__(self, mode="live", resting=None, positions=None,
                 cancel_fail_ids=(), create_raises=False, get_orders_raises=False,
                 get_positions_raises=False):
        self.mode = mode
        self._resting = resting or []
        self._positions = positions or []
        self._cancel_fail = set(cancel_fail_ids)
        self._create_raises = create_raises
        self._get_orders_raises = get_orders_raises
        self._get_positions_raises = get_positions_raises
        self.created = []
        self.cancelled = []
    def get_orders(self, status="resting"):
        if self._get_orders_raises:
            raise RuntimeError("read timeout")
        return {"orders": list(self._resting)}
    def get_positions(self):
        if self._get_positions_raises:
            raise RuntimeError("positions read 500")
        return {"market_positions": list(self._positions)}
    def get_balance(self):
        return {"balance_dollars": "100.0000"}
    def cancel_order(self, oid):
        if oid in self._cancel_fail:
            raise RuntimeError("cancel 429")
        self.cancelled.append(oid)
        self._resting = [o for o in self._resting if o.get("order_id") != oid]
        return {"ok": True}
    def create_quote(self, ticker, side, price, count, post_only=True, client_order_id=None):
        if self._create_raises:
            raise RuntimeError("create rejected")
        self.created.append({"ticker": ticker, "side": side, "price": price, "count": count})
        return {"order": {"order_id": client_order_id}}


def _order(oid, ticker, outcome, price, cnt):
    return {"order_id": oid, "ticker": ticker, "outcome_side": outcome,
            f"{outcome}_price_dollars": f"{price:.4f}", "remaining_count_fp": f"{cnt:.2f}"}


def _run(monkeymod, client, tmpdir, footprint_env=None):
    """Drive run_once with a mock client + a temp data dir. Returns the plan row."""
    q.DATA_DIR = tmpdir; q.STOP_FILE = os.path.join(tmpdir, "STOP")
    q.STATE_FILE = os.path.join(tmpdir, "quoter_state.json")
    orig = q.KalshiOrderClient
    q.KalshiOrderClient = lambda *a, **k: client
    try:
        q.run_once()
    finally:
        q.KalshiOrderClient = orig
    rows = []
    for p in os.listdir(tmpdir):
        if p.startswith("plans-"):
            for line in open(os.path.join(tmpdir, p)):
                rows.append(json.loads(line))
    return rows[-1] if rows else {}


# ---- _live_standing: (dict,count) + crash-proof ----
def test_live_standing_returns_dict_and_count():
    c = MockClient(resting=[_order("a", "T1", "yes", 0.60, 10),
                            _order("b", "T1", "no", 0.30, 5)])
    st, n = q._live_standing(c)
    assert n == 2
    assert st["T1"] == [{"side": "yes", "price_dollars": 0.60, "count": 10, "order_id": "a"},
                        {"side": "no", "price_dollars": 0.30, "count": 5, "order_id": "b"}]

def test_live_standing_isolates_one_malformed_record():
    good = _order("a", "T1", "yes", 0.60, 10)
    bad = {"order_id": "b", "ticker": "T2", "outcome_side": "yes"}  # missing price
    worse = {"order_id": "c"}                                        # missing everything
    st, n = q._live_standing(MockClient(resting=[good, bad, worse]))
    assert n == 3 and "T1" in st and "T2" not in st  # bad rows skipped, good survives


# ---- series allowlist (pilot scoped to weather/temp) ----
def test_series_allowlist_filters_to_temp(monkeypatch):
    progs = [
        {"market_ticker": "KXTEMPNYCH-26JUL2014-T81.99", "incentive_type": "liquidity",
         "target_size_fp": 1000, "discount_factor_bps": 5000, "period_reward": 1000000,
         "start_date": "2026-07-20T17:00:00Z", "end_date": "2099-01-01T00:00:00Z"},
        {"market_ticker": "KXDXYDUD-26JUL20-T100", "incentive_type": "liquidity",
         "target_size_fp": 1000, "discount_factor_bps": 5000, "period_reward": 9000000,
         "start_date": "2026-07-20T17:00:00Z", "end_date": "2099-01-01T00:00:00Z"},
    ]
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXTEMPNYCH", "KXTEMPDCH"])
    picked = q.select_footprint(progs, q.utcnow())
    assert [m["ticker"] for m in picked] == ["KXTEMPNYCH-26JUL2014-T81.99"]  # DXY excluded
    # empty allowlist = no filter (legacy behavior)
    monkeypatch.setattr(q, "SERIES_ALLOW", [])
    picked2 = q.select_footprint(progs, q.utcnow())
    assert len(picked2) == 2


# ---- crossed-book gate ----
def test_desired_quotes_gates_crossed_book():
    m = {"target": 1, "end": "2099-01-01T00:00:00Z"}
    # best_y 0.60 + best_n 0.60 = 1.20 >= 1 -> crossed -> gated
    assert q.desired_quotes(m, [["0.60", "9999"]], [["0.60", "9999"]], q.utcnow()) == []
    # healthy book best_y 0.55 + best_n 0.40 = 0.95 < 1 -> quotes
    assert q.desired_quotes(m, [["0.55", "9999"]], [["0.40", "9999"]], q.utcnow())


# ---- committed pre-check + failed-cancel deferral (via run_once) ----
def _cfg(monkeypatch, join=20, mktcap=15, totcap=40):
    monkeypatch.setattr(q, "JOIN_SIZE", join)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", float(mktcap))
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", float(totcap))
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""}
                        if "incentive" in p else
                        {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]], "no_dollars": [["0.49", "9999"]]}})

def test_committed_precheck_creates_when_room(monkeypatch, tmp_path):
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[])
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("mode") == "live"
    assert len(c.created) == 2                        # both sides placed
    assert 0 < row.get("committed_usd", 0) <= 40.0

def test_committed_precheck_skips_over_cap(monkeypatch, tmp_path):
    _cfg(monkeypatch, totcap=40)
    # standing survivor that CANNOT be cancelled (cancel 429) worth ~$38 -> only ~$2
    # of headroom left, so the fresh ~$20 desired book must be SKIPPED, not stacked.
    survivor = _order("S", "TOLD", "yes", 0.95, 40)   # 0.95*40 = $38 committed, uncancellable
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[survivor], cancel_fail_ids=["S"])
    row = _run(monkeypatch, c, str(tmp_path))
    # T1 is a different ticker (not deferred by failed-cancel), but committed starts
    # at ~$38 (survivor) so the ~$20 T1 book breaches $40 and is skipped.
    assert row.get("create_skipped", 0) >= 1
    assert row.get("committed_usd", 0) <= 40.0
    assert len(c.created) == 0                         # nothing stacked on top of survivor

def test_positions_read_failure_defers_all_creates(monkeypatch, tmp_path):
    # FAIL CLOSED: if held inventory can't be read, committed is unknown -> defer
    # every create rather than admit orders at held_cost=0 (the fix4 regression).
    _cfg(monkeypatch)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[], positions=[], get_positions_raises=True)
    row = _run(monkeypatch, c, str(tmp_path))
    assert "positions_read_failed" in row
    assert len(c.created) == 0                        # no blind creates on unknown inventory
    assert row.get("create_skipped", 0) >= 1

def test_failed_cancel_defers_same_ticker(monkeypatch, tmp_path):
    _cfg(monkeypatch, totcap=200)                     # cap not the binding constraint here
    # standing on T1 (old price) that fails to cancel; desired re-quotes T1 (new price).
    old = _order("O", "T1", "yes", 0.40, 5)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": "T1", "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
    c = MockClient(mode="live", resting=[old], cancel_fail_ids=["O"])
    row = _run(monkeypatch, c, str(tmp_path))
    # cancel of O fails -> T1 is a failed-cancel ticker -> its fresh creates deferred
    assert row.get("cancel_fail", 0) == 1
    assert row.get("create_skipped", 0) >= 1
    assert all(cr["ticker"] != "T1" for cr in c.created)


def test_reconcile_guard_halts(monkeypatch, tmp_path):
    # get_orders returns rows but they are all unparseable -> raw>0 parsed==0 -> halt
    bad = [{"order_id": "x", "ticker": "T", "outcome_side": "yes"}]  # missing price
    c = MockClient(mode="live", resting=bad)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    row = _run(monkeypatch, c, str(tmp_path))
    assert row.get("reconcile_fail") == 1
    assert not c.created and not c.cancelled            # halted: no order ops


def test_standing_read_failure_skips_cycle(monkeypatch, tmp_path):
    c = MockClient(mode="live", get_orders_raises=True)
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    monkeypatch.setattr(q, "public_get", lambda p: {"incentive_programs": [], "next_cursor": ""})
    row = _run(monkeypatch, c, str(tmp_path))
    assert "standing_read_failed" in row
    assert not c.created and not c.cancelled            # acted on nothing


def test_stop_sentinel_flattens(monkeypatch, tmp_path):
    c = MockClient(mode="live", resting=[_order("a", "T1", "yes", 0.6, 10),
                                         _order("b", "T2", "no", 0.3, 5)])
    d = str(tmp_path); open(os.path.join(d, "STOP"), "w").close()
    q.DATA_DIR = d; q.STOP_FILE = os.path.join(d, "STOP"); q.STATE_FILE = os.path.join(d, "s.json")
    orig = q.KalshiOrderClient; q.KalshiOrderClient = lambda *a, **k: c
    try:
        q.run_once()
    finally:
        q.KalshiOrderClient = orig
    assert set(c.cancelled) == {"a", "b"}               # STOP cancelled everything
