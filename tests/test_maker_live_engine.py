"""Unit tests for maker_live_engine — the deployable maker trader.

What is NEW and tested here: config interlocks (live mode must be loud),
the guard stack (every guard trips, in the handoff order), the y/n
inventory model (merge netting, paper fill matching, portfolio marks), the
per-event one-winner floor mapping, quote construction (jitter bounds,
tick rounding, band discipline), batch chunking, and the kill sequence
(cancel-ALL strictly BEFORE halt). WS-book / discovery / tape code is
family-verbatim (running-arm code, tested by v5's suite + weeks of arms).
No network access anywhere in this file.
"""
import importlib.util
import json
import pathlib
import time

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mle", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "maker_live_engine.py")
mle = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mle)


def cfg_over(**kw):
    c = mle.load_config(env={})
    c.update(kw)
    return c


def mk(**kw):
    m = {"id": "1234", "cid": "0xabc", "sector": "politics", "v": 0.03,
         "msz": 100.0, "yes": "Y", "no": "N", "q": "?", "pool": 100.0,
         "tick": 0.001, "neg_risk": False, "ev": "mkt:1234",
         "end": "2099-01-01", "end_ts": None, "game_start": None}
    m.update(kw)
    return m


def put_book(asset, bids, asks, ts=None):
    with mle.BOOKS_LOCK:
        mle.BOOKS[asset] = {"bids": dict(bids), "asks": dict(asks),
                            "ts": ts if ts is not None else time.time()}


@pytest.fixture(autouse=True)
def _clean_books():
    with mle.BOOKS_LOCK:
        mle.BOOKS.clear()
    mle.SENSOR_HOT.clear()
    yield
    with mle.BOOKS_LOCK:
        mle.BOOKS.clear()
    mle.SENSOR_HOT.clear()


# ── config interlocks ────────────────────────────────────────────────────────
def test_config_defaults_paper():
    c = mle.load_config(env={})
    assert c["mode"] == "paper"
    assert c["policy"] == "P0_base"
    assert c["style"] == "wide"
    assert c["excluded_sectors"] == {"esports", "finance"}
    assert c["inv_cap_mult"] == 3.0
    assert c["freshness_s"] == 180.0


def test_config_live_requires_pk_and_ack():
    with pytest.raises(ValueError, match="MAKER_PK"):
        mle.load_config(env={"MAKER_SUBMIT_MODE": "live"})
    with pytest.raises(ValueError, match="MAKER_LIVE_ACK"):
        mle.load_config(env={"MAKER_SUBMIT_MODE": "live", "MAKER_PK": "0x1"})
    c = mle.load_config(env={"MAKER_SUBMIT_MODE": "live", "MAKER_PK": "0x1",
                             "MAKER_LIVE_ACK": mle.LIVE_ACK_PHRASE})
    assert c["mode"] == "live"


def test_config_rejects_unknown_policy_and_style():
    with pytest.raises(ValueError):
        mle.load_config(env={"MAKER_GATE_POLICY": "P9_nope"})
    with pytest.raises(ValueError):
        mle.load_config(env={"MAKER_QUOTE_STYLE": "tight"})
    with pytest.raises(ValueError):
        mle.load_config(env={"MAKER_SUBMIT_MODE": "yolo"})


def test_config_sector_caps_json():
    c = mle.load_config(env={"MAKER_SECTOR_CAPS_USD": '{"weather": 900}'})
    assert c["sector_caps"] == {"weather": 900.0}
    with pytest.raises(ValueError):
        mle.load_config(env={"MAKER_SECTOR_CAPS_USD": "not-json"})


def test_policy_matrix_lab_only_arms_absent():
    # P5 (ungated control) and P6 (tilt experiment) are lab arms, never
    # deployable engine policies
    assert set(mle.POLICIES) == {"P0_base", "P1_volfit", "P2_ramp",
                                 "P3_tapevel", "P4_all"}


# ── quote construction ───────────────────────────────────────────────────────
def test_round_tick_bounds():
    assert mle.round_tick(0.5004, 0.001, up=False) == 0.5
    assert mle.round_tick(0.5004, 0.001, up=True) == 0.501
    assert mle.round_tick(0.00001, 0.001, up=False) == 0.001   # floor at tick
    assert mle.round_tick(1.5, 0.001, up=True) == 0.999        # cap below 1


class SeqRng:
    """Deterministic rng stub: randint returns max, uniform returns hi."""
    def randint(self, a, b):
        return b
    def uniform(self, a, b):
        return b


def test_desired_quote_wide_and_touch():
    m = mk()
    put_book("Y", {0.49: 50}, {0.51: 50})
    c = cfg_over(px_jitter_ticks=0, size_jitter=0.0)
    raw_bid, raw_ask, jb, ja, sb, sa = mle.desired_quote(m, 0.50, 0.49, 0.51, c)
    # wide: half-width = max(touch 0.01, v/2 = 0.015)
    assert raw_bid == pytest.approx(0.485)
    assert raw_ask == pytest.approx(0.515)
    assert sb == sa == m["msz"]
    c2 = cfg_over(style="touch", px_jitter_ticks=0, size_jitter=0.0)
    rb2, ra2, *_ = mle.desired_quote(m, 0.50, 0.49, 0.51, c2)
    assert rb2 == pytest.approx(0.49)
    assert ra2 == pytest.approx(0.51)


def test_desired_quote_none_outside_band():
    m = mk(v=0.005)     # band 0.5c but touch spread is 1c -> no scoring quote
    assert mle.desired_quote(m, 0.50, 0.49, 0.51, cfg_over()) is None


def test_desired_quote_jitter_bounds():
    m = mk()
    c = cfg_over(px_jitter_ticks=1, size_jitter=0.20)
    raw_bid, raw_ask, jb, ja, sb, sa = mle.desired_quote(
        m, 0.50, 0.49, 0.51, c, rng=SeqRng())
    # jitter moves TOWARD mid, at most 1 tick, sizes at most msz*(1+0.2)
    assert jb == pytest.approx(raw_bid + 0.001)
    assert ja == pytest.approx(raw_ask - 0.001)
    assert jb < ja
    assert sb == sa == pytest.approx(120.0)
    assert sb >= m["msz"]        # never below scoring min size


# ── inventory model ──────────────────────────────────────────────────────────
def test_merge_pairs_nets_dollar_identity():
    st = {"y": 30.0, "n": 20.0, "spent": 25.0}
    mle.merge_pairs(st)
    assert st["y"] == 10.0 and st["n"] == 0.0
    assert st["spent"] == pytest.approx(5.0)     # $20 of pairs netted out
    assert st["merged"] == 20.0


def test_match_fills_paper_y_n_legs():
    now = 1000.0
    st = {}
    qh = [[now, 0.48, 0.52, 100.0, 100.0]]
    prints = [
        {"timestamp": now + 1, "price": 0.47, "asset": "Y", "size": 10},   # hits bid
        {"timestamp": now + 2, "price": 0.53, "asset": "Y", "size": 10},   # lifts ask
        {"timestamp": now + 3, "price": 0.50, "asset": "Y", "size": 10},   # inside: no
        {"timestamp": now + 4, "price": 0.10, "asset": "OTHER", "size": 10},
        {"timestamp": now - 5, "price": 0.10, "asset": "Y", "size": 10},   # old
    ]
    fills, max_ts = mle.match_fills_paper(prints, qh, st, "Y", now)
    assert fills == 2
    assert max_ts == now + 3
    # y fill at 0.48 (100 tokens) + n fill at 1-0.52=0.48 (100 tokens),
    # then 100 pairs merge at $1
    assert st["merged"] == 100.0
    assert st["y"] == 0.0 and st["n"] == 0.0
    assert st["spent"] == pytest.approx(0.48 * 100 + 0.48 * 100 - 100.0)


def test_match_fills_paper_pulled_quote_no_fill():
    now = 1000.0
    st = {}
    qh = [[now, 0.48, 0.52, 100.0, 100.0], [now + 1, None, None, 0, 0]]
    prints = [{"timestamp": now + 2, "price": 0.40, "asset": "Y", "size": 5}]
    fills, _ = mle.match_fills_paper(prints, qh, st, "Y", now)
    assert fills == 0 and st.get("y", 0.0) == 0.0


def test_portfolio_net_marks():
    state = {"meta": {"halted": False},
             "1": {"y": 100.0, "n": 0.0, "spent": 48.0, "last_mid": 0.50},
             "2": {"y": 0.0, "n": 100.0, "spent": 45.0, "last_mid": 0.60}}
    # mkt1: 100*0.5 - 48 = +2 ; mkt2: 100*0.4 - 45 = -5
    assert mle.portfolio_net(state) == pytest.approx(-3.0)


def test_event_worst_v6_semantics():
    assert mle.event_worst([100.0], 50.0, 1, 2) == pytest.approx(-50.0)
    assert mle.event_worst([100.0], 50.0, 2, 2) == pytest.approx(50.0)
    assert mle.event_worst([], 0.0, 0, 2) == 0.0


# ── guard stack ──────────────────────────────────────────────────────────────
def fresh_guard_ctx(**mkw):
    m = mk(**mkw)
    put_book(m["yes"], {0.49: 50}, {0.51: 50})
    put_book(m["no"], {0.49: 50}, {0.51: 50})
    g = mle.Guards(cfg_over())
    state = {"meta": {"halted": False}, str(m["id"]): {"sector": m["sector"]}}
    uni = {m["ev"]: [m]}
    return g, m, state, uni


def intent(leg="yes", px=0.485, sz=100.0):
    return {"leg": leg, "px": px, "sz": sz}


def test_guard_halted_denies_first():
    g, m, state, uni = fresh_guard_ctx()
    state["meta"]["halted"] = True
    ok, why = g.check_place(intent(), m, state[str(m["id"])], state, uni, time.time())
    assert not ok and why == "halted"
    assert g.denials["halted"] == 1


def test_guard_stale_book_interlock():
    g, m, state, uni = fresh_guard_ctx()
    put_book(m["yes"], {0.49: 50}, {0.51: 50}, ts=time.time() - 400)
    ok, why = g.check_place(intent(), m, state[str(m["id"])], state, uni, time.time())
    assert not ok and why == "stale_book"
    # missing book entirely is also stale
    with mle.BOOKS_LOCK:
        mle.BOOKS.pop(m["yes"])
    ok, why = g.check_place(intent(), m, state[str(m["id"])], state, uni, time.time())
    assert not ok and why == "stale_book"


def test_guard_market_net_cap_includes_pending():
    g, m, state, uni = fresh_guard_ctx()
    st = state[str(m["id"])]
    st["y"], st["n"] = 200.0, 0.0
    st["ob"] = {"oid": "p1", "px": 0.48, "sz": 100.0, "cost": 48.0}
    # 200 held + 100 pending + 100 new = 400 > 3*100 cap
    ok, why = g.check_place(intent(sz=100.0), m, st, state, uni, time.time())
    assert not ok and why == "market_net_cap"


def test_guard_market_gross_cap():
    g, m, state, uni = fresh_guard_ctx()
    st = state[str(m["id"])]
    st["spent"] = 149.0
    ok, why = g.check_place(intent(px=0.485, sz=100.0), m, st, state, uni, time.time())
    assert not ok and why == "market_gross_cap"


def test_guard_sector_gross_cap():
    g, m, state, uni = fresh_guard_ctx()
    state["other"] = {"sector": "politics", "spent": 580.0}
    ok, why = g.check_place(intent(px=0.485, sz=100.0), m, state[str(m["id"])],
                            state, uni, time.time())
    assert not ok and why == "sector_gross_cap"


def test_guard_event_cap_binary():
    g, m, state, uni = fresh_guard_ctx()
    g.cfg = cfg_over(event_cap=40.0)
    # buying 100 YES at 0.485 risks $48.5 (NO wins branch) > $40 cap
    ok, why = g.check_place(intent(px=0.485, sz=100.0), m, state[str(m["id"])],
                            state, uni, time.time())
    assert not ok and why == "event_cap"


def test_guard_event_cap_negrisk_siblings():
    g, m, state, uni = fresh_guard_ctx(ev="0xevent", neg_risk=True)
    g.cfg = cfg_over(event_cap=100.0)
    sib = mk(id="5678", ev="0xevent", neg_risk=True)
    uni = {"0xevent": [m, sib]}
    state["5678"] = {"sector": "politics", "y": 100.0, "n": 0.0, "spent": 60.0}
    # sibling holds $60 of YES-5678; buying $48.5 of YES-1234: if a THIRD
    # (uncovered) outcome wins both pay 0 -> worst = -(60+48.5) > $100 cap
    ok, why = g.check_place(intent(px=0.485, sz=100.0), m, state[str(m["id"])],
                            state, uni, time.time())
    assert not ok and why == "event_cap"


def test_guard_never_cross_belt():
    g, m, state, uni = fresh_guard_ctx()
    ok, why = g.check_place(intent(px=0.51, sz=100.0), m, state[str(m["id"])],
                            state, uni, time.time())
    assert not ok and why == "would_cross"
    ok, why = g.check_place(intent(leg="no", px=0.51, sz=100.0), m,
                            state[str(m["id"])], state, uni, time.time())
    assert not ok and why == "would_cross"


def test_guard_allows_clean_two_sided_quote():
    g, m, state, uni = fresh_guard_ctx()
    ok, why = g.check_place(intent(leg="yes", px=0.485, sz=100.0), m,
                            state[str(m["id"])], state, uni, time.time())
    assert ok, why
    ok, why = g.check_place(intent(leg="no", px=0.485, sz=100.0), m,
                            state[str(m["id"])], state, uni, time.time())
    assert ok, why


def test_day_floor():
    g = mle.Guards(cfg_over(day_loss_floor=75.0))
    assert not g.day_floor_breached({}, -74.9)
    assert g.day_floor_breached({}, -75.1)


# ── gates ────────────────────────────────────────────────────────────────────
NOW = time.mktime((2026, 7, 18, 20, 30, 0, 0, 0, 0))   # not used for UTC asserts
P0 = mle.POLICIES["P0_base"]


def test_gate_in_play_and_extreme_wx():
    c = cfg_over()
    m = mk(sector="sports", game_start=1000.0)
    assert mle.gate(m, {}, {}, P0, c, 2000.0, 0.5) == "in_play"
    m2 = mk(sector="weather")
    assert mle.gate(m2, {}, {}, P0, c, 2000.0, 0.95) == "extreme_wx"
    assert mle.gate(m2, {}, {}, P0, c, 2000.0, 0.5) is None


def test_gate_vol_pull_and_tapevel_and_ramp():
    c = cfg_over()
    now = 5000.0
    assert mle.gate(mk(), {}, {"pull_until": now + 10}, P0, c, now, 0.5) == "vol_pull"
    p3 = mle.POLICIES["P3_tapevel"]
    assert mle.gate(mk(), {"hot_until": now + 10}, {}, p3, c, now, 0.5) == "tapevel"
    p2 = mle.POLICIES["P2_ramp"]
    m = mk(end_ts=now + 3600 * 5, end="2099-01-01")
    assert mle.gate(m, {}, {}, p2, c, now, 0.5) == "winddown"
    m2 = mk(end_ts=now + 3600 * 20, end="2099-01-01")
    assert mle.gate(m2, {}, {}, p2, c, now, 0.5) is None


def test_gate_sensor_hot_only_when_enabled(tmp_path):
    now = 7000.0
    m = mk(cid="0xSENSOR")
    mle.SENSOR_HOT["0xsensor"] = now + 100
    assert mle.gate(m, {}, {}, P0, cfg_over(), now, 0.5) is None      # feed off
    c = cfg_over(sensor_feed="/some/path")
    assert mle.gate(m, {}, {}, P0, c, now, 0.5) == "sensor_hot"


def test_load_sensor_feed(tmp_path):
    p = tmp_path / "informed_flow.jsonl"
    now = time.time()
    p.write_text(json.dumps({"market": "0xAA", "t": now - 10}) + "\n"
                 + json.dumps({"market": "0xBB", "t": now - 9000}) + "\n")
    mle._sensor["off"] = 0
    mle.load_sensor_feed(now, str(p))
    assert "0xaa" in mle.SENSOR_HOT          # recent -> hot
    assert "0xbb" not in mle.SENSOR_HOT      # ancient -> pruned


# ── execution core (paper path + chunking; live path is preflight-gated) ─────
def test_paper_place_batch_unique_oids(tmp_path):
    ex = mle.ExecCore(cfg_over(), str(tmp_path))
    res = ex.place_batch([{"mkt": "1", "leg": "yes", "tok": "Y", "px": 0.48,
                           "sz": 100.0, "tick": "0.001", "neg_risk": False}
                          for _ in range(35)])
    assert len(res) == 35
    oids = [r["oid"] for r in res]
    assert all(r["ok"] for r in res)
    assert len(set(oids)) == 35


def test_place_batch_chunks_at_15(tmp_path, monkeypatch):
    ex = mle.ExecCore(cfg_over(), str(tmp_path))
    sizes = []
    orig = ex._submit_paper
    monkeypatch.setattr(ex, "_submit_paper",
                        lambda chunk: sizes.append(len(chunk)) or orig(chunk))
    ex.place_batch([{"mkt": str(i)} for i in range(35)])
    assert sizes == [15, 15, 5]


def test_paper_cancel_is_noop_ok(tmp_path):
    ex = mle.ExecCore(cfg_over(), str(tmp_path))
    assert ex.cancel(["p1", None]) is True
    assert ex.cancel_all() is True
    assert ex.fetch_my_trades() == []
    assert ex.earnings_for_day("2026-07-18") is None


# ── kill sequence ────────────────────────────────────────────────────────────
def test_kill_sequence_cancel_all_strictly_before_halt(tmp_path):
    calls = []

    class FakeExec:
        def cancel_all(self, attempts=3):
            # halt flag must NOT be set yet when cancel-all runs
            calls.append(("cancel_all", state.get("meta", {}).get("halted", False)))
            return True

    state = {"meta": {"halted": False},
             "1": {"ob": {"oid": "x"}, "oa": {"oid": "y"}, "qh": []}}
    ok = mle.kill_sequence(FakeExec(), state, str(tmp_path), "test floor")
    assert ok
    assert calls == [("cancel_all", False)]           # cancel FIRST
    assert state["meta"]["halted"] is True            # halt AFTER
    assert state["meta"]["halt_reason"] == "test floor"
    assert state["1"]["ob"] is None and state["1"]["oa"] is None
    assert state["1"]["qh"][-1][1] is None            # pulled in quote history
    assert (tmp_path / "HALT").exists()


def test_kill_sequence_halts_even_if_cancel_fails(tmp_path):
    class FakeExec:
        def cancel_all(self, attempts=3):
            return False
    state = {"meta": {}}
    ok = mle.kill_sequence(FakeExec(), state, str(tmp_path), "x")
    assert not ok
    assert state["meta"]["halted"] is True            # halt regardless


# ── state persistence ────────────────────────────────────────────────────────
def test_save_state_slim_and_bak(tmp_path):
    sp = str(tmp_path / "state.json")
    state = {"meta": {"day": "20260718"},
             "1": {"y": 1.0, "qh": [[1, 2, 3, 4, 5]], "mid_hist": [[1, 0.5]],
                   "prints_hist": [[1, 2]]}}
    mle._save_state(str(tmp_path), sp, state)
    loaded = json.load(open(sp))
    assert "qh" not in loaded["1"] and "mid_hist" not in loaded["1"]
    assert loaded["1"]["y"] == 1.0
    mle._save_state(str(tmp_path), sp, state)
    assert (tmp_path / "state.json.bak").exists()


# ── live trade reconciliation (fake feed, REAL maker-oriented shape) ─────────
OUR = "0xM"


def taker_trade(tid, ts, makers):
    """CLOB-feed-shaped record: top level is TAKER-oriented; a maker's
    fills are maker_orders[] entries (the review-verified semantics —
    top-level side would be SELL when a taker sells into our bid)."""
    return {"id": tid, "match_time": ts, "side": "SELL", "size": "999",
            "price": "0.1", "asset_id": "IGNORED-TAKER-FIELDS",
            "taker_address": "0xTAKER",
            "maker_orders": makers}


def mo(asset, px, sz, addr=OUR, side="BUY"):
    return {"maker_address": addr, "asset_id": asset, "price": px,
            "matched_amount": sz, "side": side}


class FakeFeed:
    live = True
    address = OUR
    def __init__(self, trades):
        self._t = trades
    def fetch_my_trades(self):
        return self._t


def test_apply_live_trades_maker_orders_semantics(tmp_path):
    m = mk()
    now = time.time()
    trades = [
        # our maker fill: taker SOLD into our YES bid (top-level side SELL —
        # the old parser dropped exactly this record class)
        taker_trade("t1", now, [mo("Y", "0.48", "100"),
                                mo("Y", "0.30", "500", addr="0xOTHER")]),
        taker_trade("t1", now, [mo("Y", "0.48", "100")]),        # dup id
        taker_trade("t2", now, [mo("N", "0.47", "50")]),         # NO leg
        taker_trade("t3", now, [mo("UNKNOWN", "0.1", "5")]),     # loud, unapplied
        taker_trade("t4", now, [mo("Y", "bogus", "5")]),         # loud, unapplied
        taker_trade("t5", now, [mo("Y", "0.4", "5", side="SELL")]),  # we never SELL
    ]
    state = {"meta": {"trade_wm": now - 100, "trade_recent": {}}}
    mle._apply_live_trades(FakeFeed(trades), state, [m], str(tmp_path),
                           state["meta"])
    st = state["1234"]
    # t1 applied once at OUR matched amount (not the taker's 999, not the
    # other maker's 500), t2 NO leg, 50 pairs merged at $1
    assert st["y"] == 50.0 and st["n"] == 0.0
    assert st["merged"] == 50.0
    assert st["spent"] == pytest.approx(0.48 * 100 + 0.47 * 50 - 50.0)
    # second pass: nothing double-applied
    mle._apply_live_trades(FakeFeed(trades), state, [m], str(tmp_path),
                           state["meta"])
    assert st["y"] == 50.0 and st["merged"] == 50.0


def test_apply_live_trades_timestamp_watermark_not_id_eviction(tmp_path):
    """Review CRIT 3: old trades far below the watermark must be skipped
    even when their ids are no longer tracked."""
    m = mk()
    now = time.time()
    old = taker_trade("ancient", now - 7200, [mo("Y", "0.40", "100")])
    state = {"meta": {"trade_wm": now, "trade_recent": {}}}
    mle._apply_live_trades(FakeFeed([old]), state, [m], str(tmp_path),
                           state["meta"])
    assert "1234" not in state or not state["1234"].get("y")


def test_apply_live_trades_taker_fallback_and_persistent_tokmap(tmp_path):
    now = time.time()
    # preflight FAK: we are the taker; market NOT in universe but tok map
    # persisted in state (review finding 17)
    tr = {"id": "tk1", "match_time": now, "side": "BUY", "price": "0.52",
          "size": "5", "asset_id": "YOLD", "taker_address": OUR}
    state = {"meta": {"trade_wm": now - 10, "trade_recent": {}},
             "9999": {"tok_y": "YOLD", "tok_n": "NOLD", "sector": "politics"}}
    mle._apply_live_trades(FakeFeed([tr]), state, [], str(tmp_path),
                           state["meta"])
    assert state["9999"]["y"] == 5.0
    assert state["9999"]["spent"] == pytest.approx(0.52 * 5)


def test_trade_ts_parses_epoch_ms_and_iso():
    now = time.time()
    assert mle._trade_ts({"match_time": str(now)}) == pytest.approx(now)
    assert mle._trade_ts({"match_time": now * 1000}) == pytest.approx(now)
    assert mle._trade_ts({"created_at": "2026-07-18T12:00:00Z"}) > 0
    assert mle._trade_ts({"match_time": "garbage"}) == 0.0
    assert mle._trade_ts({}) == 0.0


# ── strict post_orders response contract (review CRIT 4) ─────────────────────
def test_parse_post_orders_good_list():
    out = mle.parse_post_orders_resp(
        [{"success": True, "orderID": "a"}, {"success": True, "orderID": "b"}], 2)
    assert [r["oid"] for r in out] == ["a", "b"]
    assert all(r["ok"] and not r.get("ambiguous") for r in out)


def test_parse_post_orders_positive_rejection_is_not_ambiguous():
    out = mle.parse_post_orders_resp(
        [{"success": False, "errorMsg": "not enough balance"}], 1)
    assert not out[0]["ok"] and not out[0].get("ambiguous")


def test_parse_post_orders_unknown_shapes_are_ambiguous():
    # wrapper dict, wrong length, junk items — all must scream ambiguous,
    # never fabricate per-leg results
    for resp, n in (({"success": True, "orders": [1, 2]}, 2),
                    ([{"success": True, "orderID": "a"}], 2),
                    ([None, 42], 2),
                    ("500 internal", 2)):
        out = mle.parse_post_orders_resp(resp, n)
        assert len(out) == n
        assert all(r.get("ambiguous") and not r["ok"] for r in out)


# ── backoff + halt persistence ───────────────────────────────────────────────
def test_backoff_exponential_capped():
    st = {}
    now = 1000.0
    mle._backoff(st, now)
    first = st["backoff"]
    assert first >= 5.0
    assert st["retry_after"] == now + first
    for _ in range(10):
        mle._backoff(st, now)
    assert st["backoff"] == 300.0


def test_write_halt_roundtrip(tmp_path):
    assert mle.write_halt(str(tmp_path), "why") is True
    assert (tmp_path / "HALT").read_text().strip().endswith("why")
    assert mle.write_halt(str(tmp_path / "no" / "such" / "dir"), "x") is False


def test_kill_sequence_sets_retry_flags(tmp_path):
    class FakeExec:
        def cancel_all(self, attempts=3):
            return False
    state = {"meta": {}}
    mle.kill_sequence(FakeExec(), state, str(tmp_path), "x")
    assert state["meta"]["halted"] is True
    assert state["meta"]["halt_cancel_ok"] is False      # loop must retry
    assert state["meta"]["halt_unpersisted"] is False    # file did write


# ── config bounds (review finding 15) ────────────────────────────────────────
def test_config_bounds_validation():
    for env in ({"MAKER_SIZE_JITTER": "-0.5"},
                {"MAKER_PX_JITTER_TICKS": "-1"},
                {"MAKER_FRESHNESS_S": "0"},
                {"MAKER_ROTATION_FRAC": "1.5"},
                {"MAKER_DAY_LOSS_FLOOR_USD": "-10"},
                {"MAKER_SECTOR_CAPS_USD": '{"weather": 0}'}):
        with pytest.raises(ValueError):
            mle.load_config(env=env)


# ── guard additions from the review ──────────────────────────────────────────
def test_guard_day_floor_denies_in_minute(tmp_path):
    g, m, state, uni = fresh_guard_ctx()
    state["meta"]["day_pnl"] = -80.0
    ok, why = g.check_place(intent(), m, state[str(m["id"])], state, uni,
                            time.time())
    assert not ok and why == "day_floor"


def test_guard_stale_no_book_denies():
    g, m, state, uni = fresh_guard_ctx()
    put_book(m["no"], {0.49: 50}, {0.51: 50}, ts=time.time() - 400)
    ok, why = g.check_place(intent(), m, state[str(m["id"])], state, uni,
                            time.time())
    assert not ok and why == "stale_book"


def test_guard_departed_quarantined_from_sector_cap():
    g, m, state, uni = fresh_guard_ctx()
    state["dead"] = {"sector": "politics", "spent": 580.0, "departed": True}
    ok, why = g.check_place(intent(px=0.485, sz=100.0), m, state[str(m["id"])],
                            state, uni, time.time())
    assert ok, why       # departed spend no longer strangles the sector


def test_apply_live_trades_ts_unparseable_quarantined_forever(tmp_path):
    """2nd-pass NEW-1: a ts-less record must NEVER be applied, and its id
    must survive eviction so the recurring feed can't re-apply it."""
    m = mk()
    now = time.time()
    junk = {"id": "junk1", "maker_orders": [mo("Y", "0.48", "100")],
            "taker_address": "0xT"}          # no timestamp anywhere
    state = {"meta": {"trade_wm": now, "trade_recent": {}}}
    mle._apply_live_trades(FakeFeed([junk]), state, [m], str(tmp_path),
                           state["meta"])
    assert "1234" not in state or not state.get("1234", {}).get("y")
    assert state["meta"]["feed_anomaly_n"] == 1
    assert state["meta"]["trade_recent"]["junk1"] == 4e12   # pinned
    # advance the watermark far past the lap; pin must survive eviction
    fresh = taker_trade("t9", now + 2 * mle.TRADE_LAP_S,
                        [mo("Y", "0.48", "10")])
    mle._apply_live_trades(FakeFeed([junk, fresh]), state, [m], str(tmp_path),
                           state["meta"])
    assert "junk1" in state["meta"]["trade_recent"]
    assert state["1234"]["y"] == 10.0        # only the real fill applied
    mle._apply_live_trades(FakeFeed([junk, fresh]), state, [m], str(tmp_path),
                           state["meta"])
    assert state["1234"]["y"] == 10.0        # junk still never applied


def test_apply_live_trades_dead_feed_counts(tmp_path):
    """2nd-pass M1: feed failure (None) must count consecutively and reset
    on success — never masquerade as 'no fills'."""
    class DeadFeed:
        live = True
        address = OUR
        def fetch_my_trades(self):
            return None
    state = {"meta": {"trade_wm": 0, "trade_recent": {}}}
    for i in range(3):
        mle._apply_live_trades(DeadFeed(), state, [], str(tmp_path),
                               state["meta"])
    assert state["meta"]["feed_fail_n"] == 3
    mle._apply_live_trades(FakeFeed([]), state, [], str(tmp_path),
                           state["meta"])
    assert state["meta"]["feed_fail_n"] == 0


def test_apply_live_trades_unmatched_and_no_id_counted(tmp_path):
    m = mk()
    now = time.time()
    trades = [
        taker_trade("u1", now, [mo("UNKNOWN", "0.4", "10")]),   # unmatched
        {"maker_orders": [mo("Y", "0.4", "10")], "match_time": now},  # no id
    ]
    state = {"meta": {"trade_wm": now - 10, "trade_recent": {}}}
    mle._apply_live_trades(FakeFeed(trades), state, [m], str(tmp_path),
                           state["meta"])
    assert state["meta"]["unmatched_fills_n"] == 1      # M2 counter
    assert state["meta"]["feed_anomaly_n"] == 1         # NEW-7 no-id loud
    assert not state.get("1234", {}).get("y")


def test_clear_zombies_helper():
    state = {"meta": {"zombie_fail_n": 4},
             "1": {"zombies": ["a", "b"]}, "2": {"zombies": []},
             "3": {"y": 1.0}}
    mle._clear_zombies(state)
    assert state["1"]["zombies"] == []
    assert state["meta"]["zombie_fail_n"] == 0
    assert state["3"] == {"y": 1.0}


def test_kill_sequence_clears_zombies_on_success(tmp_path):
    """2nd-pass NEW-3: a successful cancel_all leaves no order standing —
    zombie lists must not survive it and re-bar markets."""
    class FakeExec:
        def cancel_all(self, attempts=3):
            return True
    state = {"meta": {}, "1": {"zombies": ["x"], "ob": {"oid": "x"},
                               "oa": None, "qh": []}}
    mle.kill_sequence(FakeExec(), state, str(tmp_path), "t")
    assert state["1"]["zombies"] == []


def test_ledger_write_survives_bad_dir():
    """2nd-pass NEW-2: ENOSPC/OSError in ledger_write must never raise."""
    before = mle._LEDGER_MISS["n"]
    mle.ledger_write("Z:/no/such/dir/nowhere", "orders", {"a": 1})
    assert mle._LEDGER_MISS["n"] == before + 1


def test_event_floor_sees_departed_siblings():
    """2nd-pass NEW-4: rotated-out siblings holding inventory stay in the
    one-winner floor."""
    g, m, state, uni = fresh_guard_ctx(ev="0xevent", neg_risk=True)
    g.cfg = cfg_over(event_cap=100.0)
    # departed sibling NOT in uni_by_ev, but persisted in state with ev
    state["7777"] = {"sector": "politics", "ev": "0xevent", "departed": True,
                     "y": 100.0, "n": 0.0, "spent": 60.0}
    uni = {"0xevent": [m]}
    ok, why = g.check_place(intent(px=0.485, sz=100.0), m, state[str(m["id"])],
                            state, uni, time.time())
    assert not ok and why == "event_cap"    # 60 + 48.5 > 100 with 0-branch


# ── resolution backfill (2nd-pass M3 closure) ────────────────────────────────
def test_try_settle_yes_wins():
    st = {"y": 100.0, "n": 0.0, "spent": 48.0, "last_mid": 0.5}
    val = mle.try_settle(st, ["1", "0"])
    assert val == 100.0
    assert st["y"] == st["n"] == 0.0
    assert st["spent"] == pytest.approx(-52.0)     # net contribution = +52
    assert st["settled"] is True
    assert st["last_mid"] == 1.0
    # portfolio_net must now carry the REALIZED value (floor sees it)
    assert mle.portfolio_net({"1": st}) == pytest.approx(52.0)


def test_try_settle_no_wins_and_mixed():
    st = {"y": 100.0, "n": 40.0, "spent": 60.0}
    val = mle.try_settle(st, ["0", "1"])
    assert val == 40.0                              # only NO tokens pay
    assert st["spent"] == pytest.approx(20.0)       # realized −20
    assert mle.portfolio_net({"1": st}) == pytest.approx(-20.0)


def test_try_settle_refuses_undecided():
    st = {"y": 100.0, "spent": 48.0}
    assert mle.try_settle(st, ["0.97", "0.03"]) is None   # not decisive
    assert mle.try_settle(st, ["0.5", "0.5"]) is None     # split w/o UMA final
    assert mle.try_settle(st, []) is None
    assert mle.try_settle(st, ["bogus", "1"]) is None
    assert mle.try_settle(st, ["0.7", "0.7"]) is None     # doesn't sum to 1
    assert "settled" not in st and st["y"] == 100.0       # untouched


def test_try_settle_uma_final_split():
    """50/50 'no answer' resolutions settle when UMA-final (review
    finding 2 — the money math is exact for any payout vector)."""
    st = {"y": 100.0, "n": 0.0, "spent": 80.0}
    val = mle.try_settle(st, ["0.5", "0.5"], uma_resolved=True)
    assert val == pytest.approx(50.0)
    assert st["spent"] == pytest.approx(30.0)             # realized −30
    assert st["settled"] is True


def test_resolution_sweep_rejects_wrong_row_id(tmp_path, monkeypatch):
    """Review finding 4: never settle market k at another row's outcome."""
    state = {"meta": {}, "10": {"departed": True, "y": 50.0, "spent": 20.0}}
    monkeypatch.setattr(mle, "get", lambda url, timeout=10:
                        [{"id": "999", "closed": True,
                          "outcomePrices": '["1", "0"]',
                          "umaResolutionStatus": "resolved"}])
    mle.resolution_sweep(state, str(tmp_path), 1e9)
    assert "settled" not in state["10"] and state["10"]["y"] == 50.0


def test_late_fill_clears_settled_flag(tmp_path):
    """Review finding 3: a post-settlement fill re-opens the market for
    the next sweep instead of stranding unrealized tokens."""
    now = time.time()
    state = {"meta": {"trade_wm": now - 10, "trade_recent": {}},
             "1234": {"tok_y": "Y", "tok_n": "N", "settled": True,
                      "departed": True, "y": 0.0, "n": 0.0, "spent": -30.0}}
    tr = taker_trade("late1", now, [mo("Y", "0.48", "100")])
    mle._apply_live_trades(FakeFeed([tr]), state, [], str(tmp_path),
                           state["meta"])
    st = state["1234"]
    assert st["settled"] is False
    assert st["y"] == 100.0
    assert st["spent"] == pytest.approx(-30.0 + 48.0)


def test_resolution_sweep_gating_and_settle(tmp_path, monkeypatch):
    now = 1e9
    state = {"meta": {},
             "10": {"departed": True, "y": 50.0, "n": 0.0, "spent": 20.0,
                    "sector": "politics"},
             "11": {"departed": True, "settled": True, "spent": -5.0},   # skip
             "12": {"departed": False, "y": 9.0},                        # live: skip
             "13": {"departed": True, "y": 0.0, "n": 0.0, "spent": 0}}   # empty: skip
    calls = []
    def fake_get(url, timeout=10):
        calls.append(url)
        return [{"id": "10", "closed": True, "outcomePrices": '["1", "0"]',
                 "umaResolutionStatus": "resolved"}]
    monkeypatch.setattr(mle, "get", fake_get)
    mle.resolution_sweep(state, str(tmp_path), now)
    # PATH form required — the ?id= query excludes closed markets (the
    # sweep's whole target set); caught live 2026-07-19
    assert len(calls) == 1 and calls[0].endswith("/markets/10")
    assert state["10"]["settled"] is True
    assert state["10"]["spent"] == pytest.approx(-30.0)   # realized +30
    # cadence gate: immediate re-run does nothing
    mle.resolution_sweep(state, str(tmp_path), now + 10)
    assert len(calls) == 1
    # after the sweep interval, nothing left to look up
    mle.resolution_sweep(state, str(tmp_path), now + mle.RESOLUTION_SWEEP_S + 1)
    assert len(calls) == 1


def test_settle_realized_day_uses_jump_not_lifetime(tmp_path, monkeypatch):
    """Cold-eyes finding 1: settle_realized_day must record the mark->outcome
    JUMP (today's day_pnl effect), not the whole-position lifetime realized —
    else the kill reason overstates settlement and masks live bleed."""
    # prior-day loser: marked at 0.10 (frozen), spent $80, settles NO-wins
    state = {"meta": {},
             "10": {"departed": True, "y": 100.0, "n": 0.0, "spent": 80.0,
                    "last_mid": 0.10}}
    monkeypatch.setattr(mle, "get", lambda url, timeout=10:
                        {"id": "10", "closed": True, "outcomePrices": '["0", "1"]',
                         "umaResolutionStatus": "resolved"})
    mle.resolution_sweep(state, str(tmp_path), 1e9)
    st = state["10"]
    assert st["settled"] is True
    assert st["y"] == 0.0
    # frozen mark was 100*0.10 = 10; outcome pays 0 -> today's jump = 0-10 = -10
    assert state["meta"]["settle_realized_day"] == pytest.approx(-10.0)
    # ledger carries BOTH: lifetime realized (-80) and today's jump (-10)
    rows = [json.loads(l) for l in
            open(next(tmp_path.glob("settlements-*.jsonl"))).read().splitlines()]
    assert rows[-1]["realized"] == pytest.approx(-80.0)
    assert rows[-1]["day_jump"] == pytest.approx(-10.0)


def test_event_floor_ignores_settled_sibling(tmp_path):
    """Cold-eyes finding 2: a SETTLED sibling's realized spent must not enter
    the forward event floor (would loosen it for a winner)."""
    g, m, state, uni = fresh_guard_ctx(ev="0xevent", neg_risk=True)
    g.cfg = cfg_over(event_cap=40.0)
    uni = {"0xevent": [m]}
    order = intent(px=0.485, sz=100.0)          # $48.5 risk > $40 cap
    ok0, why0 = g.check_place(order, m, state[str(m["id"])], state, uni, time.time())
    assert not ok0 and why0 == "event_cap"      # blocked with no sibling
    # add a settled big-winner sibling: if wrongly counted it slashes cost and
    # would flip the verdict to allow
    state["8888"] = {"sector": "politics", "ev": "0xevent", "departed": True,
                     "settled": True, "y": 0.0, "n": 0.0, "spent": -1000.0}
    ok1, why1 = g.check_place(order, m, state[str(m["id"])], state, uni, time.time())
    assert not ok1 and why1 == "event_cap"      # STILL blocked — settled ignored


def test_resolution_sweep_unresolved_retries_later(tmp_path, monkeypatch):
    now = 1e9
    state = {"meta": {}, "10": {"departed": True, "y": 50.0, "spent": 20.0}}
    monkeypatch.setattr(mle, "get", lambda url, timeout=10:
                        [{"id": "10", "closed": False,
                          "outcomePrices": '["0.7", "0.3"]'}])
    mle.resolution_sweep(state, str(tmp_path), now)
    assert "settled" not in state["10"] and state["10"]["y"] == 50.0


def test_guard_pair_sibling_gross_cap():
    """Review finding 11: the two legs individually pass the gross cap but
    the PAIR must not."""
    g, m, state, uni = fresh_guard_ctx()
    g.cfg = cfg_over(market_gross_cap=90.0)
    st = state[str(m["id"])]
    leg1 = {"leg": "yes", "px": 0.485, "sz": 100.0}      # $48.5 — passes alone
    ok, why = g.check_place(leg1, m, st, state, uni, time.time())
    assert ok
    leg2 = {"leg": "no", "px": 0.485, "sz": 100.0}       # $48.5 — passes alone
    ok, why = g.check_place(leg2, m, st, state, uni, time.time(),
                            sibling={"leg": "yes", "px": 0.485, "sz": 100.0})
    assert not ok and why == "market_gross_cap"          # pair > $90
