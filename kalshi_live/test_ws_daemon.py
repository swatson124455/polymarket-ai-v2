"""Tests for kalshi_ws_feed (book mirror) + maker_kalshi_ws_daemon (hot path).

Post-adversarial-review suite (2026-07-25): beyond the happy path, every test
here that ends in *_kills_broken_variant pins a defect one of the four review
lenses proved would lose money — the mocks are deliberately hostile (failing
cancels, top-level response shapes, shrunk mirror levels) because a safety
test that cannot kill a broken implementation pins nothing.
"""
import asyncio
import json
import time

import pytest

import kalshi_ws_feed as F
import maker_kalshi_ws_daemon as D


# ---------------- BookMirror ----------------

def test_mirror_snapshot_live_fp_dialect():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["0.3600", "100"], ["0.3500", "50"]],
                      "no_dollars_fp": [["0.6300", "80"]]}, seq=5)
    assert not m.dirty
    assert m.best() == (0.36, 0.63)
    assert m.yes[0.35] == 50.0


def test_mirror_snapshot_one_sided_fp_book():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["0.0500", "202.00"]]}, seq=1)
    assert not m.dirty
    assert m.best() == (0.05, None)


def test_mirror_snapshot_no_recognized_keys_is_dirty():
    """Next dialect migration must read as UNREADABLE, not as an empty book."""
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_v3": [["0.3600", "100"]]}, seq=1)
    assert m.dirty


def test_mirror_snapshot_garbage_rows_dropped():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["abc", "def"], ["0.0000", "10"],
                                         ["0.3600", "-5"], ["0.3500", "50"]],
                      "no_dollars_fp": []}, seq=1)
    assert not m.dirty
    assert m.yes == {0.35: 50.0}                      # only the sane row survives


def test_mirror_snapshot_legacy_dialects():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[36, 100]], "no": [[63, 80]]}, seq=1)
    assert m.best() == (0.36, 0.63)


def test_mirror_delta_live_fp_dialect():
    m = F.BookMirror("T")
    m.apply_snapshot({"yes_dollars_fp": [["0.3600", "100"]],
                      "no_dollars_fp": [["0.6300", "80"]]}, seq=1)
    m.apply_delta({"side": "no", "price_dollars": "0.5200", "delta_fp": "30.00"}, seq=2)
    assert m.no[0.52] == 30.0
    m.apply_delta({"side": "no", "price_dollars": "0.5200", "delta_fp": "-30.00"}, seq=3)
    assert 0.52 not in m.no
    assert not m.dirty


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
    m.apply_snapshot({"yes_dollars_fp": [["0.3600", "100"]],
                      "no_dollars_fp": [["0.6300", "80"]]}, seq=1)
    ys, ns = m.rows()
    lv, dropped = D.M._levels(ys)
    assert lv == [(0.36, 100.0)] and dropped == 0


# ---------------- Feed dispatch hardening ----------------

def test_feed_nondict_frame_survives_kills_broken_variant():
    """Review F5a: a valid-JSON non-dict frame must be ignored, never raise."""
    feed = F.Feed(["A"])
    for raw in ("null", "[]", '"x"', "1"):
        assert feed._dispatch(raw) is True


def test_feed_nondict_msg_survives():
    feed = F.Feed(["A"])
    assert feed._dispatch(json.dumps(
        {"type": "orderbook_snapshot", "seq": 1, "msg": ["not", "a", "dict"]})) is True


def test_feed_subscribed_ack_tracked():
    feed = F.Feed(["A"], want_fills=True)
    assert not feed.fills_confirmed
    feed._dispatch(json.dumps({"type": "subscribed", "id": 1,
                               "msg": {"channel": "orderbook_delta", "sid": 1}}))
    assert not feed.fills_confirmed
    feed._dispatch(json.dumps({"type": "subscribed", "id": 1,
                               "msg": {"channel": "fill", "sid": 2}}))
    assert feed.fills_confirmed


def test_feed_error_frame_forces_reconnect():
    feed = F.Feed(["A"])
    assert feed._dispatch(json.dumps({"type": "error", "msg": {"code": 6}})) is False
    assert feed.error_frames == 1


def test_feed_global_seq_gap_dirties_all_and_forces_reconnect():
    feed = F.Feed(["A", "B"])
    ok = feed._dispatch(json.dumps(
        {"type": "orderbook_snapshot", "seq": 1,
         "msg": {"market_ticker": "A", "yes_dollars_fp": [["0.3600", "10"]]}}))
    assert ok and not feed.mirrors["A"].dirty
    ok = feed._dispatch(json.dumps(
        {"type": "orderbook_delta", "seq": 3,
         "msg": {"market_ticker": "A", "side": "yes",
                 "price_dollars": "0.3700", "delta_fp": "5.00"}}))
    assert ok is False
    assert feed.mirrors["A"].dirty and feed.mirrors["B"].dirty
    assert feed.gap_count == 1


def test_recv_or_stop_wakes_fast_on_stop():
    """Review F2/F3-async: stop must not wait out the 300s idle recv."""
    async def scenario():
        class NeverWS:
            async def recv(self):
                await asyncio.sleep(3600)
        stop = asyncio.Event()
        loop = asyncio.get_event_loop()
        loop.call_later(0.05, stop.set)
        t0 = time.monotonic()
        kind, raw = await F._recv_or_stop(NeverWS(), stop)
        return kind, time.monotonic() - t0
    kind, dt = asyncio.run(scenario())
    assert kind == "stopped" and dt < 2.0


# ---------------- hot_reprice_ops: REPRICE-ONLY + reduce-side ban ----------------

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


def test_new_side_expansion_is_forbidden():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20},
         {"side": "no", "price_dollars": 0.60, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])
    assert creates == [{"side": "yes", "price_dollars": 0.37, "count": 20}]
    assert cancels == ["o1"]


def test_reduce_side_untouchable_kills_broken_variant():
    """Review F3/F4: the reducing side is never cancelled OR created hot —
    no unwind overshoot, no stranding, no KEEP_BOTH parity gap."""
    cancels, creates = D.hot_reprice_ops(
        [{"side": "no", "price_dollars": 0.65, "count": 10, "reason": "unwind"},
         {"side": "yes", "price_dollars": 0.37, "count": 20}],
        [_std("u1", "no", 0.63, 10), _std("o1", "yes", 0.36, 20)],
        reduce_side="no")
    assert cancels == ["o1"]                          # only the accumulating side
    assert creates == [{"side": "yes", "price_dollars": 0.37, "count": 20}]


def test_unwind_tagged_desired_never_created():
    cancels, creates = D.hot_reprice_ops(
        [{"side": "no", "price_dollars": 0.65, "count": 10, "reason": "unwind"}],
        [_std("u1", "no", 0.63, 10)])
    assert cancels == [] and creates == []            # unwind reprice = cold-cycle work


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


# ---------------- Daemon hot path ----------------

class MockClient:
    """Hostile-configurable client: failing cancels, response shapes."""
    mode = "live"

    def __init__(self, fail_cancel_ids=(), response_shape="nested"):
        self.calls = []
        self.coids = []
        self.fail_cancel_ids = set(fail_cancel_ids)
        self.response_shape = response_shape

    def batch_cancel(self, oids):
        self.calls.append(("batch_cancel", list(oids)))
        ok = [{"order": {"order_id": o}} for o in oids if o not in self.fail_cancel_ids]
        failed = [{"order_id": o, "error": "429"} for o in oids if o in self.fail_cancel_ids]
        return {"cancelled": ok, "failed": failed}

    def create_quote(self, ticker, outcome, price, count, post_only=True,
                     client_order_id=None):
        assert post_only, "hot path must never send a crossing order"
        self.calls.append(("create_quote", ticker, outcome, price, count))
        self.coids.append(client_order_id)
        oid = f"new-{len(self.calls)}"
        if self.response_shape == "nested":
            return {"order": {"order_id": oid}}
        if self.response_shape == "toplevel":
            return {"order_id": oid, "status": "resting"}
        return {"status": "accepted"}                 # no id at all


class InlineExec:
    """Executes submitted work synchronously so tests see effects immediately."""

    def submit(self, fn, *a, **k):
        fn(*a, **k)

    def shutdown(self, wait=True):
        pass


class FakeFeed:
    def __init__(self, fills_confirmed=True):
        self.fills_confirmed = fills_confirmed


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(D.M, "STOP_FILE", str(tmp_path / "STOP"))
    monkeypatch.setattr(D, "_plan_sig", lambda: ("plan", 0))
    monkeypatch.setattr(D, "WS_HOT", 1)
    d = D.Daemon.__new__(D.Daemon)
    d.client = MockClient()
    d.cycle_req = D.threading.Event()
    d.stopping = D.threading.Event()
    d.last_cycle_mono = time.monotonic()
    d.last_refs = {}
    d.ctx = D.HotContext()
    d.bucket = D.TokenBucket(100.0, 10)
    d.last_hot_mono = {}
    d.programs = []
    d.programs_mono = 0.0
    d.feed = FakeFeed(fills_confirmed=True)
    d.in_cold = False
    d.std_lock = D.threading.Lock()
    d._writer = InlineExec()
    d._coid = 0
    d.ctx.built_mono = time.monotonic()
    d.ctx.plan_sig = ("plan", 0)
    d.ctx.breaker = False
    d.ctx.by_ticker = {"T": {"m": {"ticker": "T"}, "own": None, "inv": 0.0,
                             "ev": 0.0, "cost": 0.0}}
    d.ctx.standing = {"T": [_std("o1", "yes", 0.36, 20)]}
    d.ctx.headroom_total = 1000.0
    d.ctx.headroom_mkt = {"T": 100.0}
    return d


def _mirror(yes_depth=100):
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[36, yes_depth]], "no": [[63, 80]]}, seq=1)
    return m


def _patch_quotes(monkeypatch, quotes):
    monkeypatch.setattr(D.M, "desired_quotes",
                        lambda *a, **k: [dict(q) for q in quotes])


def test_hot_reprice_executes(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    out = daemon.hot_event("T", _mirror(), time.monotonic())
    assert out == "submitted"
    kinds = [c[0] for c in daemon.client.calls]
    assert kinds == ["batch_cancel", "create_quote"]
    std = daemon.ctx.standing["T"]
    assert all(o["order_id"] != "o1" for o in std)
    assert any(abs(o["price_dollars"] - 0.37) < 1e-9 for o in std)


def test_hot_cancel_fail_blocks_create_kills_broken_variant(daemon, monkeypatch):
    """Review F1 BLOCKER: a failed cancel must abort creates + force a cycle."""
    daemon.client = MockClient(fail_cancel_ids={"o1"})
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    out = daemon.hot_event("T", _mirror(), time.monotonic())
    assert out == "submitted"
    kinds = [c[0] for c in daemon.client.calls]
    assert kinds == ["batch_cancel"]                  # NO create after failed cancel
    assert any(o["order_id"] == "o1" for o in daemon.ctx.standing["T"])   # kept in view
    assert daemon.ctx.stale()                         # invalidated
    assert daemon.cycle_req.is_set()                  # guarded cycle forced


def test_hot_toplevel_response_shape_parsed(daemon, monkeypatch):
    """Review F4: V2 create may return top-level order fields."""
    daemon.client = MockClient(response_shape="toplevel")
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert any(o["order_id"].startswith("new-") for o in daemon.ctx.standing["T"])


def test_hot_no_order_id_invalidates_kills_broken_variant(daemon, monkeypatch):
    """Review F4: a create ack without an id must invalidate, never invent one."""
    daemon.client = MockClient(response_shape="no_id")
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert not any(str(o["order_id"]).startswith("ws-unknown")
                   for o in daemon.ctx.standing.get("T", []))
    assert daemon.ctx.stale() and daemon.cycle_req.is_set()


def test_hot_client_order_ids_unique_kills_broken_variant(daemon, monkeypatch):
    """Review F3: same-second reprices must never reuse a client_order_id."""
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    daemon.ctx.built_mono = time.monotonic()          # refresh ctx (was consumed)
    daemon.ctx.standing["T"] = [_std("o2", "yes", 0.37, 20)]
    daemon.last_hot_mono = {}
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.38, "count": 20}])
    m2 = F.BookMirror("T")                            # mirror must show our 0.37 level
    m2.apply_snapshot({"yes": [[37, 100]], "no": [[63, 80]]}, seq=1)
    daemon.hot_event("T", m2, time.monotonic())
    ids = daemon.client.coids
    assert len(ids) == 2 and ids[0] != ids[1]


def test_hot_blocked_cold_running_kills_broken_variant(daemon):
    """Review F2 BLOCKER (all reviewers): hot is dead while run_once runs."""
    daemon.in_cold = True
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "cold_running"
    assert daemon.client.calls == []


def test_hot_blocked_breaker(daemon):
    """Review F4: under breaker the cold cycle owns all shaping — hot stands down."""
    daemon.ctx.breaker = True
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "breaker"
    assert daemon.client.calls == []


def test_hot_blocked_fills_unconfirmed_kills_broken_variant(daemon):
    """Review F3 BLOCKER: no venue ack for the fill channel -> no hot writes."""
    daemon.feed = FakeFeed(fills_confirmed=False)
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "fills_unconfirmed"
    assert daemon.client.calls == []


def test_hot_depth_precondition_kills_broken_variant(daemon, monkeypatch):
    """Review F2-partial-fills: level shrunk below our count = possible fill ->
    invalidate + cycle, never a refill."""
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    out = daemon.hot_event("T", _mirror(yes_depth=8), time.monotonic())   # 8 < our 20
    assert out == "possible_fill"
    assert daemon.client.calls == []
    assert daemon.ctx.stale() and daemon.cycle_req.is_set()


def test_hot_notional_cap_kills_broken_variant(daemon, monkeypatch):
    """Review F5: repricing may not raise resting notional past headroom."""
    daemon.ctx.headroom_mkt["T"] = 0.05               # 5c of room
    daemon.ctx.headroom_total = 0.05
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.40, "count": 20}])
    out = daemon.hot_event("T", _mirror(), time.monotonic())   # +0.04*20 = $0.80 > 5c
    assert out == "notional_cap"
    assert daemon.client.calls == []


def test_hot_notional_decrease_allowed(daemon, monkeypatch):
    daemon.ctx.headroom_mkt["T"] = 0.0
    daemon.ctx.headroom_total = 0.0
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.30, "count": 20}])
    out = daemon.hot_event("T", _mirror(), time.monotonic())   # repricing DOWN
    assert out == "submitted"


def test_hot_worker_revalidates_kills_broken_variant(daemon, monkeypatch):
    """Review F6: the worker must abort if the world changed after submit."""
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])

    class LateColdExec(InlineExec):
        def submit(self, fn, *a, **k):
            daemon.in_cold = True                     # cold cycle starts before exec
            fn(*a, **k)
    daemon._writer = LateColdExec()
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert daemon.client.calls == []                  # aborted, no venue writes


def test_hot_blocked_flag_off(daemon, monkeypatch):
    monkeypatch.setattr(D, "WS_HOT", 0)
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "flag_off"


def test_hot_blocked_dry_run(daemon):
    daemon.client.mode = "dry_run"
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "dry_run"


def test_hot_blocked_stop_file(daemon):
    open(D.M.STOP_FILE, "w").write("halt")
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "stop_file"


def test_hot_blocked_stale_ctx(daemon):
    daemon.ctx.built_mono = time.monotonic() - (D.WS_STALE_S + 1)
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "ctx_stale"


def test_hot_blocked_dirty_mirror(daemon):
    m = _mirror()
    m.dirty = True
    assert daemon.hot_event("T", m, time.monotonic()) == "mirror_dirty"


def test_hot_blocked_foreign_writer(daemon, monkeypatch):
    monkeypatch.setattr(D, "_plan_sig", lambda: ("plan", 999))
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "foreign_writer"


def test_hot_blocked_debounce(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "submitted"
    daemon.ctx.built_mono = time.monotonic()
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "debounce"


def test_hot_write_exception_invalidates(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])

    def boom(oids):
        raise RuntimeError("venue 500")
    daemon.client.batch_cancel = boom
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert daemon.ctx.stale()
    assert daemon.cycle_req.is_set()


def test_hot_no_op_when_book_already_matches(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.36, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "no_op"


# ---------------- Stage A wiring ----------------

def test_on_book_move_requests_cycle_when_hot_off(daemon, monkeypatch):
    monkeypatch.setattr(D, "WS_HOT", 0)
    m = _mirror()
    daemon.on_book("T", m)
    assert not daemon.cycle_req.is_set()
    m.apply_delta({"side": "yes", "price": 37, "delta": 5}, seq=2)
    daemon.on_book("T", m)
    assert daemon.cycle_req.is_set()


def test_on_book_subtick_move_ignored(daemon, monkeypatch):
    monkeypatch.setattr(D, "WS_HOT", 0)
    m = _mirror()
    daemon.on_book("T", m)
    daemon.on_book("T", m)
    assert not daemon.cycle_req.is_set()


def test_on_book_blocked_hot_falls_back_to_cycle(daemon):
    daemon.ctx.breaker = True                         # hot blocked -> Stage A fallback
    m = _mirror()
    daemon.on_book("T", m)
    m.apply_delta({"side": "yes", "price": 37, "delta": 5}, seq=2)
    daemon.on_book("T", m)
    assert daemon.cycle_req.is_set()


def test_on_fill_forces_ctx_stale_and_cycle(daemon):
    daemon.on_fill({"market_ticker": "T", "count": 5})
    assert daemon.ctx.stale()
    assert daemon.cycle_req.is_set()
