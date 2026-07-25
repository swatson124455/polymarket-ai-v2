"""Tests for kalshi_ws_feed (book mirror) + maker_kalshi_ws_daemon (hot path).

Pins the SAFETY invariants of the WS build:
  - mirror never trusts itself across a seq gap / unparseable delta
  - hot path is REPRICE-ONLY (a create without a same-side cancel is impossible)
  - every hot precondition (flag, dry_run, STOP, stale ctx, dirty mirror,
    foreign writer, debounce, budget) blocks writes
  - breaker => only unwind-tagged quotes survive the hot filter
  - hot write errors invalidate the context and force a full guarded cycle
  - the maker-only order surface: hot path can call ONLY batch_cancel /
    create_quote (post-only) — nothing that can cross the spread
"""
import time

import pytest

import kalshi_ws_feed as F
import maker_kalshi_ws_daemon as D


# ---------------- BookMirror ----------------

def test_mirror_snapshot_live_fp_dialect():
    """The dialect prod actually sends (live-verified 2026-07-25):
    yes_dollars_fp / no_dollars_fp with dollar-string rows."""
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["0.3600", "100"], ["0.3500", "50"]],
                      "no_dollars_fp": [["0.6300", "80"]]}, seq=5)
    assert not m.dirty
    assert m.best() == (0.36, 0.63)
    assert m.yes[0.35] == 50.0


def test_mirror_snapshot_one_sided_fp_book():
    """Prod sends only the non-empty side's key (live-verified: 4.095 snapshot
    had yes_dollars_fp only). Missing side must parse EMPTY, not poison."""
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["0.0500", "202.00"]]}, seq=1)
    assert not m.dirty
    assert m.best() == (0.05, None)


def test_mirror_snapshot_legacy_dialects():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[36, 100]], "no": [[63, 80]]}, seq=1)
    assert m.best() == (0.36, 0.63)
    m2 = F.BookMirror("T2")
    m2.apply_snapshot({"yes_dollars": [["0.3600", "100"]],
                       "no_dollars": [["0.6300", "80"]]}, seq=1)
    assert m2.best() == (0.36, 0.63)


def test_mirror_delta_live_fp_dialect():
    """Prod delta shape (live-verified): price_dollars + delta_fp strings."""
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["0.3600", "100"]],
                      "no_dollars_fp": [["0.6300", "80"]]}, seq=1)
    m.apply_delta({"side": "no", "price_dollars": "0.5200", "delta_fp": "30.00"}, seq=2)
    assert m.no[0.52] == 30.0
    m.apply_delta({"side": "no", "price_dollars": "0.5200", "delta_fp": "-30.00"}, seq=3)
    assert 0.52 not in m.no                           # level removed at zero
    assert not m.dirty


def test_mirror_delta_apply_add_and_remove():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[36, 100]], "no": [[63, 80]]}, seq=1)
    m.apply_delta({"side": "yes", "price": 37, "delta": 25}, seq=2)
    assert m.best()[0] == 0.37
    m.apply_delta({"side": "yes", "price": 37, "delta": -25}, seq=3)
    assert m.best()[0] == 0.36                        # level removed at zero
    assert not m.dirty


def test_feed_global_seq_gap_dirties_all_and_forces_reconnect():
    """seq is GLOBAL per subscription (live-verified): a gap means an UNKNOWN
    ticker missed a message -> every mirror dirty + reconnect requested."""
    import json as _json
    feed = F.Feed(["A", "B"])
    ok = feed._dispatch(_json.dumps(
        {"type": "orderbook_snapshot", "seq": 1,
         "msg": {"market_ticker": "A", "yes_dollars_fp": [["0.3600", "10"]]}}))
    assert ok and not feed.mirrors["A"].dirty
    ok = feed._dispatch(_json.dumps(
        {"type": "orderbook_delta", "seq": 3,                     # gap: 1 -> 3
         "msg": {"market_ticker": "A", "side": "yes",
                 "price_dollars": "0.3700", "delta_fp": "5.00"}}))
    assert ok is False                                # caller must reconnect
    assert feed.mirrors["A"].dirty and feed.mirrors["B"].dirty
    assert feed.gap_count == 1


def test_mirror_unparseable_delta_marks_dirty():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[36, 100]], "no": []}, seq=1)
    m.apply_delta({"side": "maybe", "price": 37, "delta": 5}, seq=2)
    assert m.dirty


def test_mirror_delta_before_snapshot_ignored():
    m = F.BookMirror("T")
    m.apply_delta({"side": "yes", "price": 37, "delta": 5}, seq=2)
    assert m.dirty and m.yes == {}


def test_mirror_rows_shape_matches_rest_api():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars": [["0.3600", "100"]],
                      "no_dollars": [["0.6300", "80"]]}, seq=1)
    ys, ns = m.rows()
    assert ys == [["0.3600", "100.00"]] and ns == [["0.6300", "80.00"]]
    lv, dropped = D.M._levels(ys)                     # must parse via the quoter's own parser
    assert lv == [(0.36, 100.0)] and dropped == 0


# ---------------- hot_reprice_ops: REPRICE-ONLY invariant ----------------

def _std(oid, side, price, count):
    return {"order_id": oid, "side": side, "price_dollars": price, "count": count}


def test_reprice_same_count_new_price():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])
    assert cancels == ["o1"]
    assert creates == [{"side": "yes", "price_dollars": 0.37, "count": 20}]


def test_side_pull_is_cancel_only():
    cancels, creates = D.hot_reprice_ops([], [_std("o1", "yes", 0.36, 20)])
    assert cancels == ["o1"] and creates == []


def test_count_change_is_left_for_cold_cycle():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 40}],
        [_std("o1", "yes", 0.36, 20)])
    assert cancels == [] and creates == []


def test_new_side_expansion_is_forbidden_in_hot_path():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20},
         {"side": "no", "price_dollars": 0.60, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])                # no standing NO order
    assert creates == [{"side": "yes", "price_dollars": 0.37, "count": 20}]
    assert cancels == ["o1"]                          # NO side untouched: no naked create


def test_multi_level_left_for_cold_cycle():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20}],
        [_std("o1", "yes", 0.36, 10), _std("o2", "yes", 0.35, 10)])
    assert cancels == [] and creates == []


def test_same_price_is_no_op():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.36, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])
    assert cancels == [] and creates == []


# ---------------- TokenBucket ----------------

def test_token_bucket_caps_burst():
    b = D.TokenBucket(rate=1000.0, burst=2)
    assert b.take() and b.take() and not b.take()


def test_token_bucket_refills():
    b = D.TokenBucket(rate=1000.0, burst=1)
    assert b.take()
    time.sleep(0.005)
    assert b.take()


# ---------------- Daemon hot preconditions ----------------

class MockClient:
    mode = "live"

    def __init__(self):
        self.calls = []

    def batch_cancel(self, oids):
        self.calls.append(("batch_cancel", list(oids)))
        return {"cancelled": oids, "failed": []}

    def create_quote(self, ticker, outcome, price, count, post_only=True,
                     client_order_id=None):
        assert post_only, "hot path must never send a crossing order"
        self.calls.append(("create_quote", ticker, outcome, price, count))
        return {"order": {"order_id": f"new-{len(self.calls)}"}}


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(D.M, "STOP_FILE", str(tmp_path / "STOP"))
    monkeypatch.setattr(D, "_plan_sig", lambda: ("plan", 0))
    monkeypatch.setattr(D, "WS_HOT", 1)
    monkeypatch.setenv("KALSHI_TRADING_MODE", "dry_run")
    d = D.Daemon.__new__(D.Daemon)                    # skip __init__ (no client build)
    d.client = MockClient()
    d.cycle_req = D.threading.Event()
    d.stopping = D.threading.Event()
    d.last_cycle_mono = time.monotonic()
    d.last_refs = {}
    d.ctx = D.HotContext()
    d.bucket = D.TokenBucket(100.0, 10)
    d.last_hot_mono = {}
    d.hot_disabled_reason = None
    # a fresh usable ctx for ticker T
    d.ctx.built_mono = time.monotonic()
    d.ctx.plan_sig = ("plan", 0)
    d.ctx.breaker = False
    d.ctx.by_ticker = {"T": {"m": {"ticker": "T"}, "own": None, "inv": 0.0,
                             "ev": 0.0, "cost": 0.0}}
    d.ctx.standing = {"T": [_std("o1", "yes", 0.36, 20)]}
    return d


def _mirror():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[36, 100]], "no": [[63, 80]]}, seq=1)
    return m


def _patch_quotes(monkeypatch, quotes):
    monkeypatch.setattr(D.M, "desired_quotes",
                        lambda *a, **k: [dict(q) for q in quotes])


def test_hot_acts_on_reprice(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    out = daemon.hot_event("T", _mirror(), time.monotonic())
    assert out == "acted"
    kinds = [c[0] for c in daemon.client.calls]
    assert kinds == ["batch_cancel", "create_quote"]
    # standing view updated: old id gone, new order present at the new price
    std = daemon.ctx.standing["T"]
    assert all(o["order_id"] != "o1" for o in std)
    assert any(abs(o["price_dollars"] - 0.37) < 1e-9 for o in std)


def test_hot_blocked_flag_off(daemon, monkeypatch):
    monkeypatch.setattr(D, "WS_HOT", 0)
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "flag_off"
    assert daemon.client.calls == []


def test_hot_blocked_dry_run(daemon):
    daemon.client.mode = "dry_run"
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "dry_run"
    assert daemon.client.calls == []


def test_hot_blocked_stop_file(daemon, tmp_path):
    open(D.M.STOP_FILE, "w").write("halt")
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "stop_file"
    assert daemon.client.calls == []


def test_hot_blocked_stale_ctx(daemon):
    daemon.ctx.built_mono = time.monotonic() - (D.WS_STALE_S + 1)
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "ctx_stale"
    assert daemon.client.calls == []


def test_hot_blocked_dirty_mirror(daemon):
    m = _mirror()
    m.dirty = True
    assert daemon.hot_event("T", m, time.monotonic()) == "mirror_dirty"
    assert daemon.client.calls == []


def test_hot_blocked_foreign_writer(daemon, monkeypatch):
    monkeypatch.setattr(D, "_plan_sig", lambda: ("plan", 999))   # plan file grew
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "foreign_writer"
    assert daemon.client.calls == []


def test_hot_blocked_debounce(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "acted"
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "debounce"


def test_hot_blocked_budget(daemon, monkeypatch):
    daemon.bucket = D.TokenBucket(0.0001, 1)
    daemon.bucket.tokens = 0.0
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "budget"
    assert daemon.client.calls == []


def test_hot_breaker_reduce_only_filter(daemon, monkeypatch):
    daemon.ctx.breaker = True
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    # non-unwind quote filtered under breaker -> desired empty -> side pulled
    out = daemon.hot_event("T", _mirror(), time.monotonic())
    assert out == "acted"
    assert [c[0] for c in daemon.client.calls] == ["batch_cancel"]   # cancel-only


def test_hot_write_error_invalidates_ctx(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])

    def boom(oids):
        raise RuntimeError("venue 500")
    daemon.client.batch_cancel = boom
    out = daemon.hot_event("T", _mirror(), time.monotonic())
    assert out == "write_error"
    assert daemon.ctx.stale()                          # forced stale
    assert daemon.cycle_req.is_set()                   # full guarded cycle requested


def test_hot_no_op_when_book_already_matches(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.36, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "no_op"
    assert daemon.client.calls == []


# ---------------- Stage A wiring ----------------

def test_on_book_move_requests_cycle_when_hot_off(daemon, monkeypatch):
    monkeypatch.setattr(D, "WS_HOT", 0)
    m = _mirror()
    daemon.on_book("T", m)                            # first sight: baseline only
    assert not daemon.cycle_req.is_set()
    m.apply_delta({"side": "yes", "price": 37, "delta": 5}, seq=2)
    daemon.on_book("T", m)                            # 1-tick move -> cycle
    assert daemon.cycle_req.is_set()


def test_on_book_subtick_move_ignored(daemon, monkeypatch):
    monkeypatch.setattr(D, "WS_HOT", 0)
    m = _mirror()
    daemon.on_book("T", m)
    daemon.on_book("T", m)                            # unchanged book
    assert not daemon.cycle_req.is_set()


def test_on_fill_forces_ctx_stale_and_cycle(daemon):
    daemon.on_fill({"market_ticker": "T", "count": 5})
    assert daemon.ctx.stale()
    assert daemon.cycle_req.is_set()
