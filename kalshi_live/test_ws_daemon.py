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


def test_reprice_pair_same_count_new_price():
    ops = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])
    assert len(ops) == 1
    assert ops[0]["order_id"] == "o1" and ops[0]["want_count"] == 20.0
    assert ops[0]["price_dollars"] == 0.37 and ops[0]["old_price"] == 0.36


def test_no_standalone_side_pull_kills_broken_variant():
    """re-review LADDER-HATCH: hot must NEVER pull a side it sees no desired for
    — run_once appends a ladder self-hedge hot cannot regenerate, so a pull would
    strip a floored pair into naked settlement risk."""
    ops = D.hot_reprice_ops([], [_std("o1", "yes", 0.36, 20)])
    assert ops == []


def test_count_change_is_left_for_cold_cycle():
    ops = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 40}],
        [_std("o1", "yes", 0.36, 20)])
    assert ops == []


def test_new_side_expansion_is_forbidden():
    ops = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20},
         {"side": "no", "price_dollars": 0.60, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])
    assert [o["side"] for o in ops] == ["yes"]          # NO side never created


def test_reduce_side_untouchable_kills_broken_variant():
    ops = D.hot_reprice_ops(
        [{"side": "no", "price_dollars": 0.65, "count": 10, "reason": "unwind"},
         {"side": "yes", "price_dollars": 0.37, "count": 20}],
        [_std("u1", "no", 0.63, 10), _std("o1", "yes", 0.36, 20)],
        reduce_side="no")
    assert [o["order_id"] for o in ops] == ["o1"]


def test_unwind_tagged_desired_never_repriced():
    ops = D.hot_reprice_ops(
        [{"side": "no", "price_dollars": 0.65, "count": 10, "reason": "unwind"}],
        [_std("u1", "no", 0.63, 10)])
    assert ops == []


def test_multi_level_left_for_cold_cycle():
    ops = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.37, "count": 20}],
        [_std("o1", "yes", 0.36, 10), _std("o2", "yes", 0.35, 10)])
    assert ops == []


def test_same_price_is_no_op():
    ops = D.hot_reprice_ops(
        [{"side": "yes", "price_dollars": 0.36, "count": 20}],
        [_std("o1", "yes", 0.36, 20)])
    assert ops == []


# ---------------- TokenBucket ----------------

def test_token_bucket_caps_burst():
    b = D.TokenBucket(rate=1000.0, burst=2)
    assert b.take() and b.take() and not b.take()


# ---------------- Daemon hot path ----------------

class MockClient:
    """Hostile-configurable client. remaining_map lets a test say how much the
    venue reports STILL RESTING at cancel time (None = venue did not say)."""
    mode = "live"

    def __init__(self, fail_cancel_ids=(), response_shape="nested",
                 remaining_map=None, remaining_default=20.0):
        self.calls = []
        self.coids = []
        self.fail_cancel_ids = set(fail_cancel_ids)
        self.response_shape = response_shape
        self.remaining_map = remaining_map or {}
        self.remaining_default = remaining_default

    cancel_remaining_ct = staticmethod(D.M.KalshiOrderClient.cancel_remaining_ct)

    def cancel_order(self, oid):
        self.calls.append(("cancel_order", oid))
        if oid in self.fail_cancel_ids:
            raise RuntimeError("429 cancel rejected")
        rem = self.remaining_map.get(oid, self.remaining_default)
        if rem is None:
            return {"order": {"order_id": oid}}          # venue did not state it
        return {"order": {"order_id": oid, "remaining_count_fp": str(rem)}}

    def create_quote(self, ticker, outcome, price, count, post_only=True,
                     client_order_id=None):
        assert post_only, "hot path must never send a crossing order"
        self.calls.append(("create_quote", ticker, outcome, price, count))
        self.coids.append(client_order_id)
        oid = "new-%d" % len(self.calls)
        if self.response_shape == "nested":
            return {"order": {"order_id": oid}}
        if self.response_shape == "toplevel":
            return {"order_id": oid, "status": "resting"}
        return {"status": "accepted"}


class InlineExec:
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
    d.bucket = D.TokenBucket(100.0, 20)
    d.last_hot_mono = {}
    d.last_fill_mono = {}
    d.programs = []
    d.programs_mono = 0.0
    d.feed = FakeFeed(True)
    d.in_cold = False
    d.std_lock = D.threading.Lock()
    d.cold_lock = D.threading.Lock()
    d._writer = InlineExec()
    d._coid = 0
    d.ctx.built_mono = time.monotonic()
    d.ctx.plan_sig = ("plan", 0)
    d.ctx.breaker = False
    d.ctx.by_ticker = {"T": {"m": {"ticker": "T"}, "own": None, "inv": 0.0,
                             "ev": 0.0, "cost": 0.0}}
    d.ctx.event_inv = {}
    d.ctx.standing = {"T": [_std("o1", "yes", 0.36, 20)]}
    d.ctx.headroom_total = 1000.0
    d.ctx.headroom_mkt = {"T": 100.0}
    return d


def _mirror(yes_depth=100, yes_px=36):
    m = F.BookMirror("T")
    m.apply_snapshot({"yes": [[yes_px, yes_depth]], "no": [[63, 80]]}, seq=1)
    return m


def _patch_quotes(monkeypatch, quotes):
    monkeypatch.setattr(D.M, "desired_quotes",
                        lambda *a, **k: [dict(q) for q in quotes])


def test_hot_reprice_executes(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "submitted"
    assert [c[0] for c in daemon.client.calls] == ["cancel_order", "create_quote"]
    assert daemon.client.calls[1][4] == 20.0            # full count still resting
    assert all(o["order_id"] != "o1" for o in daemon.ctx.standing["T"])


def test_partial_fill_clamps_create_to_venue_remaining_kills_broken_variant(
        daemon, monkeypatch):
    """THE round-2 BLOCKER (B3-1). Venue says only 12 of our 20 was still
    resting -> we may re-place AT MOST 12. The old code re-placed 20 (a +8ct
    adverse refill); the depth precondition could not see it because other
    makers' size covered the level."""
    daemon.client = MockClient(remaining_map={"o1": 12.0})
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(yes_depth=100), time.monotonic()) == "submitted"
    creates = [c for c in daemon.client.calls if c[0] == "create_quote"]
    assert len(creates) == 1
    assert creates[0][4] == 12.0, "must clamp to venue-confirmed remaining, not 20"


def _log_events(daemon_log_path):
    out = []
    try:
        with open(daemon_log_path, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    out.append(json.loads(ln).get("ev"))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def test_unknown_remaining_never_recreates_kills_broken_variant(daemon, monkeypatch):
    """Venue did not state remaining size -> re-creating would be a guess.
    Asserts the DELIBERATE guard ran (hot_cancel_no_remaining), not an
    incidental crash into the catch-all: removing the `is None` check makes
    float(None) raise, which fails safe but is NOT the same code path, and a
    test that cannot tell them apart does not pin the guard."""
    daemon.client = MockClient(remaining_map={"o1": None})
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert [c[0] for c in daemon.client.calls] == ["cancel_order"]   # no create
    assert daemon.ctx.stale() and daemon.cycle_req.is_set()
    evs = _log_events(D.LOG_PATH)
    assert "hot_cancel_no_remaining" in evs, evs
    assert "hot_write_error" not in evs, "must be a clean guard, not an exception"


def test_fully_filled_order_never_recreated_kills_broken_variant(daemon, monkeypatch):
    daemon.client = MockClient(remaining_map={"o1": 0.0})
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert [c[0] for c in daemon.client.calls] == ["cancel_order"]
    assert daemon.ctx.stale() and daemon.cycle_req.is_set()


def test_cancel_exception_blocks_create_kills_broken_variant(daemon, monkeypatch):
    daemon.client = MockClient(fail_cancel_ids={"o1"})
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert not any(c[0] == "create_quote" for c in daemon.client.calls)
    assert daemon.ctx.stale() and daemon.cycle_req.is_set()


def test_event_inventory_gate_kills_broken_variant(daemon, monkeypatch):
    """re-review B3-3 + LADDER-HATCH: inventory anywhere in the event -> stand
    down (covers both the stale-inv reduce_side race and the ladder hedge hot
    cannot regenerate)."""
    daemon.ctx.event_inv = {D.M._event_key("T"): D.M.INV_TOLERANCE + 5}
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "event_has_inventory"
    assert daemon.client.calls == []


def test_reduce_side_caller_derivation_kills_mutant3(daemon, monkeypatch):
    """MUTANT-3 (delete the reduce_side derivation in hot_event) passed all 49
    tests last round. This pins the CALLER: long YES inventory means the NO side
    is the reducing side and must never be hot-touched."""
    daemon.ctx.by_ticker["T"]["inv"] = +20.0
    daemon.ctx.event_inv = {}                            # gate open on purpose
    daemon.ctx.standing["T"] = [_std("u1", "no", 0.63, 10),
                                _std("o1", "yes", 0.36, 20)]
    _patch_quotes(monkeypatch, [{"side": "no", "price_dollars": 0.65, "count": 10},
                                {"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    touched = [c[1] for c in daemon.client.calls if c[0] == "cancel_order"]
    assert "u1" not in touched, "reducing (NO) side must never be hot-touched"
    assert touched == ["o1"]


def test_reduce_side_caller_derivation_short_yes_kills_mutant8(daemon, monkeypatch):
    """MUTANT-8 (delete the ban in hot_reprice_ops) also passed all 49 tests."""
    daemon.ctx.by_ticker["T"]["inv"] = -20.0            # short YES -> YES reduces
    daemon.ctx.event_inv = {}
    daemon.ctx.standing["T"] = [_std("o1", "yes", 0.36, 20)]
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert daemon.client.calls == [], "YES is the reducing side when short YES"


def test_fill_cooldown_blocks_hot(daemon, monkeypatch):
    daemon.last_fill_mono[D.M._event_key("T")] = time.monotonic()
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "fill_cooldown"
    assert daemon.client.calls == []


def test_depth_precondition_covers_untouched_orders_kills_broken_variant(
        daemon, monkeypatch):
    """re-review B3-2: a collapsed level on ANY standing order in the ticker is
    evidence our inventory changed, not just the one being repriced."""
    daemon.ctx.standing["T"] = [_std("o1", "yes", 0.36, 20),
                                _std("o9", "no", 0.63, 999)]   # 999 > book 80
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "possible_fill"
    assert daemon.client.calls == []


def test_toplevel_response_shape_parsed(daemon, monkeypatch):
    daemon.client = MockClient(response_shape="toplevel")
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert any(o["order_id"].startswith("new-") for o in daemon.ctx.standing["T"])


def test_create_no_id_invalidates_kills_broken_variant(daemon, monkeypatch):
    daemon.client = MockClient(response_shape="no_id")
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    assert not any(str(o["order_id"]).startswith("ws-unknown")
                   for o in daemon.ctx.standing.get("T", []))
    assert daemon.ctx.stale() and daemon.cycle_req.is_set()


def test_client_order_ids_unique(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.hot_event("T", _mirror(), time.monotonic())
    daemon.ctx.built_mono = time.monotonic()
    daemon.ctx.standing["T"] = [_std("o2", "yes", 0.37, 20)]
    daemon.last_hot_mono = {}
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.38, "count": 20}])
    daemon.hot_event("T", _mirror(yes_px=37), time.monotonic())
    ids = daemon.client.coids
    assert len(ids) == 2 and ids[0] != ids[1]


def test_invalidate_uses_none_not_zero_kills_broken_variant():
    """re-review SENTINEL: 0.0 reads as FRESH for the first WS_STALE_S seconds
    of a boot-relative monotonic clock."""
    c = D.HotContext()
    c.built_mono = 0.0
    assert c.stale(), "0.0 must not be treated as a fresh context"
    c.built_mono = time.monotonic()
    assert not c.stale()
    c.invalidate()
    assert c.built_mono is None and c.stale()


def test_hot_blocked_cold_running_kills_broken_variant(daemon):
    daemon.in_cold = True
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "cold_running"
    assert daemon.client.calls == []


def test_cold_lock_serializes_writes_kills_broken_variant(daemon, monkeypatch):
    """re-review B2-1: an already-submitted write must not interleave with
    run_once — the executor must take cold_lock, not just check a flag."""
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    daemon.cold_lock.acquire()                          # cold cycle holds it
    done = []

    def run():
        daemon._exec_hot("T", [{"order_id": "o1", "side": "yes",
                                "price_dollars": 0.37, "want_count": 20.0,
                                "old_price": 0.36}],
                         0.0, time.monotonic(), daemon.ctx.built_mono)
        done.append(True)
    th = D.threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout=0.5)
    assert not done, "executor must BLOCK while the cold cycle holds cold_lock"
    assert daemon.client.calls == []
    daemon.cold_lock.release()
    th.join(timeout=2.0)
    assert done


def test_abort_ok_rechecked_before_create_kills_mutant4(daemon, monkeypatch):
    """MUTANT-4 (drop the built_snapshot term) passed last round. A fill landing
    DURING the cancel must stop the follow-on create."""
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.37, "count": 20}])
    snap = daemon.ctx.built_mono
    orig = daemon.client.cancel_order

    def cancel_then_fill(oid):
        r = orig(oid)
        daemon.on_fill({"market_ticker": "T", "count": 8})   # fill mid-sequence
        return r
    daemon.client.cancel_order = cancel_then_fill
    daemon._exec_hot("T", [{"order_id": "o1", "side": "yes", "price_dollars": 0.37,
                            "want_count": 20.0, "old_price": 0.36}],
                     0.0, time.monotonic(), snap)
    assert not any(c[0] == "create_quote" for c in daemon.client.calls)


def test_hot_blocked_breaker(daemon):
    daemon.ctx.breaker = True
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "breaker"


def test_hot_blocked_fills_unconfirmed_kills_broken_variant(daemon):
    daemon.feed = FakeFeed(False)
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "fills_unconfirmed"
    assert daemon.client.calls == []


def test_exec_rechecks_fills_confirmed_kills_broken_variant(daemon):
    """re-review FILL-ACK-2: a disconnect between submit and execute aborts."""
    snap = daemon.ctx.built_mono
    daemon.feed = FakeFeed(False)
    daemon._exec_hot("T", [{"order_id": "o1", "side": "yes", "price_dollars": 0.37,
                            "want_count": 20.0, "old_price": 0.36}],
                     0.0, time.monotonic(), snap)
    assert daemon.client.calls == []


def test_notional_headroom_reserved_atomically_kills_broken_variant(daemon, monkeypatch):
    """re-review H7-1b: N tickers must not all pass the SAME total check —
    reservation happens under the lock at check time."""
    daemon.ctx.headroom_total = 0.60
    daemon.ctx.headroom_mkt = {"T": 100.0, "U": 100.0, "V": 100.0}
    for t in ("U", "V"):
        daemon.ctx.by_ticker[t] = {"m": {"ticker": t}, "own": None, "inv": 0.0,
                                   "ev": 0.0, "cost": 0.0}
        daemon.ctx.standing[t] = [_std(t + "1", "yes", 0.36, 20)]
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.38, "count": 20}])
    outs = []
    for t in ("T", "U", "V"):
        mm = F.BookMirror(t)
        mm.apply_snapshot({"yes": [[36, 100]], "no": [[63, 80]]}, seq=1)
        daemon.ctx.built_mono = time.monotonic()
        outs.append(daemon.hot_event(t, mm, time.monotonic()))
    assert outs.count("notional_cap") >= 2, "only one $0.40 reprice fits in $0.60"


def test_notional_decrease_allowed(daemon, monkeypatch):
    daemon.ctx.headroom_mkt["T"] = 0.0
    daemon.ctx.headroom_total = 0.0
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.30, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "submitted"


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


def test_no_op_when_book_already_matches(daemon, monkeypatch):
    _patch_quotes(monkeypatch, [{"side": "yes", "price_dollars": 0.36, "count": 20}])
    assert daemon.hot_event("T", _mirror(), time.monotonic()) == "no_op"


def test_cancel_remaining_ct_parser():
    P = D.M.KalshiOrderClient.cancel_remaining_ct
    assert P({"order": {"remaining_count_fp": "12.5"}}) == 12.5
    assert P({"remaining_count": 7}) == 7.0
    assert P({"order": {"order_id": "x"}}) is None
    assert P({"order": {"remaining_count_fp": "junk"}}) is None
    assert P(None) is None


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


def test_on_fill_forces_ctx_stale_cycle_and_event_cooldown(daemon):
    daemon.on_fill({"market_ticker": "T", "count": 5})
    assert daemon.ctx.stale()
    assert daemon.cycle_req.is_set()
    assert D.M._event_key("T") in daemon.last_fill_mono


def test_stop_heartbeat_not_widened_kills_regression():
    """re-review REGRESSION: widening the STOP heartbeat to 300s delayed the
    first flatten pass of an operator-written STOP. It must stay <= WS_COLD_S."""
    assert D.WS_STOP_COLD_S <= D.WS_COLD_S
