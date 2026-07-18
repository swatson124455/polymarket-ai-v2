"""Unit tests for maker_sensor_feed — the informed-flow tripwire publisher.

Detectors and universe loading are pure/parameterized; the run loop is the
family-standard skeleton. No network access anywhere in this file.
"""
import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "msf", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "maker_sensor_feed.py")
msf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(msf)

NOW = 1_784_000_000.0
Y = "YESASSET"


def tr(ago, px, sz, side="BUY", asset=Y):
    return {"timestamp": NOW - ago, "price": px, "size": sz, "side": side,
            "asset": asset, "transactionHash": "0x%f" % ago}


# ── window_stats ─────────────────────────────────────────────────────────────
def test_window_stats_frames_and_filters():
    prints = [tr(400, 0.50, 10),            # outside 5m — ignored
              tr(200, 0.50, 10),            # px_5m anchor; also pre-60s ref
              tr(30, 0.52, 10, side="SELL"),
              tr(5, 0.53, 10)]
    ws = msf.window_stats(prints, Y, NOW)
    assert ws["n_5m"] == 3
    assert ws["px_5m"] == 0.50 and ws["px_last"] == 0.53
    assert ws["px_pre60"] == 0.50            # LATEST print OLDER than 60s —
    assert ws["ts_last"] == NOW - 5          # the bite move reference
    assert ws["buy_60"] == 0.53 * 10 and ws["sell_60"] == 0.52 * 10
    # wrong asset / bad rows / None rows never crash or count
    ws2 = msf.window_stats([tr(5, 0.5, 10, asset="OTHER"),
                            {"timestamp": "x"}, None, {}], Y, NOW)
    assert ws2["n_5m"] == 0 and ws2["px_last"] is None
    # empty yes_asset must match NOTHING (a print with a missing asset field
    # also normalizes to "" — the two must not collide)
    ws3 = msf.window_stats([tr(5, 0.5, 10, asset="")], "", NOW)
    assert ws3["n_5m"] == 0


def test_window_stats_future_prints_ignored():
    ws = msf.window_stats([tr(-30, 0.9, 100)], Y, NOW)   # ts > now
    assert ws["n_5m"] == 0


# ── evaluate ─────────────────────────────────────────────────────────────────
def test_bite_requires_notional_and_move():
    # $800 buy in 60s, 2c move vs pre-60s ref -> bite +1
    prints = [tr(200, 0.50, 400), tr(50, 0.51, 400), tr(5, 0.52, 400)]
    evs = msf.evaluate(msf.window_stats(prints, Y, NOW))
    assert ("bite", 1) in [(t, d) for t, d, _ in evs]
    # same notional, no move vs pre-60s ref -> no bite
    flat = [tr(200, 0.50, 400), tr(50, 0.50, 400), tr(5, 0.50, 400)]
    assert not any(t == "bite" for t, _, _ in
                   msf.evaluate(msf.window_stats(flat, Y, NOW)))
    # move but tiny notional -> no bite
    small = [tr(200, 0.50, 5), tr(5, 0.52, 5)]
    assert not any(t == "bite" for t, _, _ in
                   msf.evaluate(msf.window_stats(small, Y, NOW)))


def test_bite_single_whale_print_fires():
    # review finding 2: ONE $500 sweep, nothing else in 5m. Must fire —
    # move judged vs the prior poll's price, or on notional alone if none.
    lone = [tr(5, 0.51, 1000)]
    evs = msf.evaluate(msf.window_stats(lone, Y, NOW), prev_px=0.45)
    assert ("bite", 1) in [(t, d) for t, d, _ in evs]
    # prior price flat -> genuinely no impact -> no bite
    evs2 = msf.evaluate(msf.window_stats(lone, Y, NOW), prev_px=0.51)
    assert not any(t == "bite" for t, _, _ in evs2)
    # no reference at all -> notional+existence is the signal
    evs3 = msf.evaluate(msf.window_stats(lone, Y, NOW), prev_px=None)
    assert ("bite", 1) in [(t, d) for t, d, _ in evs3]


def test_bite_two_sided_churn_suppressed():
    # review finding 9: $400 buy vs $390 sell inside 60s = churn, not a bite
    churn = [tr(200, 0.50, 100), tr(40, 0.52, 800, side="BUY"),
             tr(20, 0.50, 780 / 0.50 * 1, side="SELL", asset=Y)]
    ws = msf.window_stats(churn, Y, NOW)
    assert ws["buy_60"] > 0 and ws["sell_60"] > 0
    assert not any(t == "bite" for t, _, _ in msf.evaluate(ws))


def test_stampede_direction_from_net_flow():
    prints = [tr(290 - i * 30, 0.50, 5, side="SELL" if i % 4 else "BUY")
              for i in range(9)]
    evs = [e for e in msf.evaluate(msf.window_stats(prints, Y, NOW))
           if e[0] == "stampede"]
    assert len(evs) == 1
    assert evs[0][1] == -1                    # sells dominate net flow
    assert evs[0][2] >= 1.0


def test_run_detects_sustained_move_both_ways():
    up = [tr(250, 0.40, 5), tr(5, 0.44, 5)]
    dn = [tr(250, 0.44, 5), tr(5, 0.40, 5)]
    assert ("run", 1) in [(t, d) for t, d, _ in
                          msf.evaluate(msf.window_stats(up, Y, NOW))]
    assert ("run", -1) in [(t, d) for t, d, _ in
                           msf.evaluate(msf.window_stats(dn, Y, NOW))]
    flat = [tr(250, 0.40, 5), tr(5, 0.41, 5)]
    assert not any(t == "run" for t, _, _ in
                   msf.evaluate(msf.window_stats(flat, Y, NOW)))


def test_evaluate_empty_window_silent():
    assert msf.evaluate(msf.window_stats([], Y, NOW)) == []


# ── onset-only emission ──────────────────────────────────────────────────────
def test_edge_and_cooldown_onset_only():
    st = {}
    # first activation emits
    assert msf.edge_and_cooldown(st, "run", True, 1000.0)
    # persisting condition does NOT re-emit, even past cooldown expiry
    assert not msf.edge_and_cooldown(st, "run", True, 1000.0 + 60)
    assert not msf.edge_and_cooldown(st, "run", True, 1000.0 + 7000)
    # condition clears, re-arms; re-onset within cooldown suppressed...
    assert not msf.edge_and_cooldown(st, "run", False, 1000.0 + 7010)
    st2 = {}
    assert msf.edge_and_cooldown(st2, "bite", True, 0.0)
    assert not msf.edge_and_cooldown(st2, "bite", False, 100.0)
    assert not msf.edge_and_cooldown(st2, "bite", True, 200.0)   # cooldown
    # ...but re-onset after cooldown emits again
    assert not msf.edge_and_cooldown(st2, "bite", False, 300.0)
    assert msf.edge_and_cooldown(st2, "bite", True, 700.0)
    # triggers are independent latches
    assert msf.edge_and_cooldown(st2, "run", True, 700.0)


# ── fetch_tape (stubbed get) ─────────────────────────────────────────────────
def _stub_pages(pages):
    calls = {"n": 0}

    def fake_get(url, timeout=10):
        i = calls["n"]
        calls["n"] += 1
        return pages[i] if i < len(pages) else []
    return fake_get


def test_fetch_tape_reached_and_truncated(monkeypatch):
    p1 = [tr(i, 0.5, 1) for i in range(200)]           # full page
    # full page whose oldest is NEWER than since_ts, then cap -> truncated
    monkeypatch.setattr(msf, "get", _stub_pages([p1, p1]))
    out, trunc = msf.fetch_tape("0xc", since_ts=NOW - 10_000, max_pages=2)
    assert trunc and out
    # short page -> reached, not truncated
    monkeypatch.setattr(msf, "get", _stub_pages([[tr(5, 0.5, 1)]]))
    out2, trunc2 = msf.fetch_tape("0xc", since_ts=0.0)
    assert not trunc2 and len(out2) == 1
    # failed page 0 -> empty, NOT truncated (watermark must not move)
    monkeypatch.setattr(msf, "get", _stub_pages([None]))
    out3, trunc3 = msf.fetch_tape("0xc", since_ts=0.0)
    assert out3 == [] and not trunc3
    # oldest reaches since_ts on page 1 -> reached
    monkeypatch.setattr(msf, "get", _stub_pages([p1]))
    out4, trunc4 = msf.fetch_tape("0xc", since_ts=NOW - 50, max_pages=2)
    assert not trunc4
    assert out4[0]["timestamp"] < out4[-1]["timestamp"]   # oldest-first


# ── load_universe ────────────────────────────────────────────────────────────
def test_load_universe_union_dedup_and_cap(tmp_path):
    a = {"t": 1, "markets": [
        {"id": "1", "cid": "0xaa", "q": "A?", "sector": "sports",
         "yes": "Y1", "pool": 100.0},
        {"id": "2", "cid": "0xbb", "q": "B?", "sector": "weather",
         "yes": "Y2", "pool": 50.0}]}
    b = {"t": 1, "markets": [
        {"id": "1", "cid": "0xaa", "q": "A?", "sector": "sports",
         "yes": "Y1", "pool": 200.0},              # same cid, bigger pool wins
        {"id": "3", "cid": "not-a-cid", "q": "junk", "pool": 999},
        {"id": "4", "cid": "0xcc", "q": "no yes key", "pool": 500.0}]}
    (tmp_path / "u1.json").write_text(json.dumps(a))
    (tmp_path / "u2.json").write_text(json.dumps(b))
    (tmp_path / "u3.json").write_text("{broken")
    rows = msf.load_universe(pattern=str(tmp_path / "u*.json"))
    # 0xcc (no yes token) dropped — it could never match a print (review 4)
    assert [r["cid"] for r in rows] == ["0xaa", "0xbb"]
    assert rows[0]["pool"] == 200.0
