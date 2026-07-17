"""Unit tests for maker_paper_sim_v5 — the GATE LAB policy matrix.

Inherited v3 core (WS books incl. the 0c9708f batched price_change fix, tape
fetch, discovery) is the running-arm code; what is NEW and tested here is the
policy-aware gate(), the extracted match_window() fill engine, and the policy
matrix itself. No network access anywhere in this file.
"""
import calendar
import importlib.util
import pathlib
import time

_SPEC = importlib.util.spec_from_file_location(
    "mps5", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "maker_paper_sim_v5.py")
mps5 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mps5)

P = mps5.POLICIES
NOW = calendar.timegm((2026, 7, 17, 20, 0, 0))     # 20:00Z — after last_hours cutoff
MSZ = 100.0


def mk(**kw):
    m = {"id": "1234", "sector": "politics", "v": 0.03, "msz": MSZ,
         "yes": "Y", "no": "N", "q": "?", "pool": 100.0,
         "end": "2099-01-01", "end_ts": None, "game_start": None}
    m.update(kw)
    return m


# ── policy matrix sanity ─────────────────────────────────────────────────────
def test_policy_matrix_shape():
    assert set(P) == {"P0_base", "P1_volfit", "P2_ramp", "P3_tapevel",
                      "P4_all", "P5_ungated"}
    assert not P["P5_ungated"]["gated"]
    assert P["P1_volfit"]["vol_pts"] < P["P0_base"]["vol_pts"]
    assert P["P1_volfit"]["vol_s"] > P["P0_base"]["vol_s"]
    assert P["P4_all"]["ramp_h"] == P["P2_ramp"]["ramp_h"] == 9.0
    assert P["P4_all"]["tapevel"] and P["P3_tapevel"]["tapevel"]


# ── gate truth table ─────────────────────────────────────────────────────────
def test_in_play_gates_all_but_ungated():
    m = mk(sector="esports", game_start=NOW - 600)
    for name, pol in P.items():
        g = mps5.gate(m, {}, {}, pol, NOW, 0.5)
        assert (g is None) if name == "P5_ungated" else (g == "in_play"), name


def test_extreme_wx_only_weather():
    m = mk(sector="weather")
    assert mps5.gate(m, {}, {}, P["P0_base"], NOW, 0.95) == "extreme_wx"
    assert mps5.gate(m, {}, {}, P["P0_base"], NOW, 0.5) is None
    assert mps5.gate(mk(sector="politics"), {}, {}, P["P0_base"], NOW, 0.95) is None


def test_winddown_replaces_last_hours():
    m = mk(end="2026-07-17", end_ts=NOW + 8 * 3600)     # ends in 8h, TODAY
    # ramp policies: inside the 9h window -> winddown
    assert mps5.gate(m, {}, {}, P["P2_ramp"], NOW, 0.5) == "winddown"
    assert mps5.gate(m, {}, {}, P["P4_all"], NOW, 0.5) == "winddown"
    # base policy: same market gates via the calendar rule instead
    assert mps5.gate(m, {}, {}, P["P0_base"], NOW, 0.5) == "last_hours"
    # outside the ramp window: ramp policies quote where base still gates
    far = mk(end="2026-07-17", end_ts=NOW + 20 * 3600)
    assert mps5.gate(far, {}, {}, P["P2_ramp"], NOW, 0.5) is None
    assert mps5.gate(far, {}, {}, P["P0_base"], NOW, 0.5) == "last_hours"
    # missing end_ts never crashes a ramp policy
    assert mps5.gate(mk(end_ts=None), {}, {}, P["P2_ramp"], NOW, 0.5) is None


def test_tapevel_gates_only_tapevel_policies():
    sh = {"hot_until": NOW + 60}
    m = mk()
    assert mps5.gate(m, sh, {}, P["P3_tapevel"], NOW, 0.5) == "tapevel"
    assert mps5.gate(m, sh, {}, P["P4_all"], NOW, 0.5) == "tapevel"
    assert mps5.gate(m, sh, {}, P["P0_base"], NOW, 0.5) is None
    assert mps5.gate(m, sh, {}, P["P5_ungated"], NOW, 0.5) is None


def test_vol_pull_is_per_policy_state():
    m = mk()
    st_pulled = {"pull_until": NOW + 100}
    assert mps5.gate(m, {}, st_pulled, P["P0_base"], NOW, 0.5) == "vol_pull"
    assert mps5.gate(m, {}, {}, P["P0_base"], NOW, 0.5) is None
    assert mps5.gate(m, {}, st_pulled, P["P5_ungated"], NOW, 0.5) is None


# ── match_window fill engine ─────────────────────────────────────────────────
def tr(ts, price, asset="Y", size=25.0, tx="0x1"):
    return {"timestamp": ts, "price": price, "asset": asset, "size": size,
            "transactionHash": tx}


def test_buy_and_sell_through_quotes():
    st = {}
    qh = [[0.0, 0.48, 0.52]]
    fills, mts = mps5.match_window(
        [tr(1.0, 0.47), tr(2.0, 0.53)], qh, st, MSZ, "Y", 0.0)
    assert fills == 2 and mts == 2.0
    assert st["pos"] == 0.0                          # round trip
    assert st["real"] == round(MSZ * (0.52 - 0.48), 4)


def test_watermark_and_wrong_asset_skipped():
    st = {}
    qh = [[0.0, 0.48, 0.52]]
    fills, _ = mps5.match_window(
        [tr(1.0, 0.47), tr(1.5, 0.47, asset="N"), tr(2.0, 0.47)],
        qh, st, MSZ, "Y", 1.0)                       # ts<=1.0 excluded
    assert fills == 1 and st["pos"] == MSZ


def test_inventory_cap_blocks_fills():
    st = {}
    qh = [[0.0, 0.48, 0.52]]
    prints = [tr(float(i + 1), 0.47, tx="0x%d" % i) for i in range(5)]
    fills, _ = mps5.match_window(prints, qh, st, MSZ, "Y", 0.0)
    assert fills == mps5.INV_CAP_MULT                # 3x msz hard cap
    assert st["pos"] == mps5.INV_CAP_MULT * MSZ


def test_quote_history_time_matching():
    st = {}
    qh = [[0.0, 0.48, 0.52], [5.0, None, None]]      # quotes pulled at t=5
    fills, _ = mps5.match_window([tr(6.0, 0.40)], qh, st, MSZ, "Y", 0.0)
    assert fills == 0                                # pulled quote can't fill


def test_policy_isolation_same_inputs():
    prints = [tr(1.0, 0.47), tr(2.0, 0.53)]
    qh = [[0.0, 0.48, 0.52]]
    a, b = {}, {}
    fa, _ = mps5.match_window(list(prints), list(qh), a, MSZ, "Y", 0.0)
    fb, _ = mps5.match_window(list(prints), list(qh), b, MSZ, "Y", 0.0)
    assert fa == fb and a == b                       # deterministic, no bleed


# ── inherited pieces that must stay correct ──────────────────────────────────
def test_batched_price_change_port():
    mps5.BOOKS.clear()
    mps5.BOOKS["a1"] = {"bids": {0.4: 10.0}, "asks": {0.6: 10.0}, "ts": 0.0}
    mps5._apply_price_change_batched(
        {"price_changes": [{"asset_id": "a1", "price": "0.41", "size": "7",
                            "side": "BUY"},
                           {"asset_id": "a1", "price": "0.6", "size": "0",
                            "side": "SELL"}]})
    assert mps5.BOOKS["a1"]["bids"][0.41] == 7.0
    assert 0.6 not in mps5.BOOKS["a1"]["asks"]
    assert mps5.BOOKS["a1"]["ts"] > 0


def test_parse_iso_short_offset():
    assert mps5.parse_iso("2026-07-17 00:00:00+00") is not None
    assert mps5.parse_iso("2026-07-17T00:00:00Z") is not None
    assert mps5.parse_iso(None) is None
