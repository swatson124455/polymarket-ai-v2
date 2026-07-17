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
                      "P4_all", "P5_ungated", "P6_tilted"}
    assert not P["P5_ungated"]["gated"]
    assert P["P1_volfit"]["vol_pts"] < P["P0_base"]["vol_pts"]
    assert P["P1_volfit"]["vol_s"] > P["P0_base"]["vol_s"]
    assert P["P4_all"]["ramp_h"] == P["P2_ramp"]["ramp_h"] == 9.0
    assert P["P4_all"]["tapevel"] and P["P3_tapevel"]["tapevel"]
    # P6 = P0 gates verbatim + tilt flag; ONLY the tilt may differ
    for k in ("gated", "vol_pts", "vol_s", "ramp_h", "tapevel"):
        assert P["P6_tilted"][k] == P["P0_base"][k], k
    assert P["P6_tilted"]["tilt"] and not P["P0_base"].get("tilt")


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


# ── P6_tilted: WB feed parsing, trust weights, tilt math ─────────────────────
NOW_JUL = calendar.timegm((2026, 7, 20, 12, 0, 0))   # inside cold-start window
NOW_AUG = calendar.timegm((2026, 8, 2, 12, 0, 0))    # after cold-start ends


def test_wb_weight_tiers():
    assert mps5.wb_weight("Hong Kong", "weather_temperature", NOW_JUL) == 0.0
    assert mps5.wb_weight("Dallas", "weather_temperature", NOW_JUL) == 0.5
    assert mps5.wb_weight("Dallas", "weather_temperature", NOW_AUG) == 1.0
    assert mps5.wb_weight("New York City", "weather_temperature", NOW_JUL) == 1.0
    assert mps5.wb_weight("New York City", "weather_precipitation", NOW_JUL) == 0.5
    assert mps5.wb_weight("Seoul", "weather_wind", NOW_JUL) == 0.25
    # pseudo-station tier (operator brief: lowest trust) — Title Case as in
    # the live feed; survives past the cold-start boundary
    assert mps5.wb_weight("Guangzhou", "weather_temperature", NOW_JUL) == 0.25
    assert mps5.wb_weight("Busan", "weather_temperature", NOW_AUG) == 0.25
    assert mps5.wb_weight("Manila", "weather_wind", NOW_JUL) == 0.125


def test_wb_shift_no_forecast_is_exact_zero():
    # 0.0 keeps P6's quote arithmetic bit-identical to P0
    assert mps5.wb_shift(None, 0.5, 0.03, 0.01) == 0.0
    assert 0.5 + mps5.wb_shift(None, 0.5, 0.03, 0.01) - 0.01 == 0.5 - 0.01
    assert mps5.wb_shift({"prob": 0.7, "t": 0, "w": 0.0}, 0.5, 0.03, 0.01) == 0.0


def test_wb_shift_direction_and_cap():
    wb = {"prob": 0.70, "t": 0, "w": 1.0}
    # raw = 0.5*(0.70-0.55) = 0.075 -> capped at TILT_MAX
    assert mps5.wb_shift(wb, 0.55, 0.10, 0.02) == mps5.TILT_MAX
    # small disagreement passes through: 0.5*(0.70-0.69) = 0.005
    assert abs(mps5.wb_shift(wb, 0.69, 0.10, 0.02) - 0.005) < 1e-12
    # NO-ward tilt when market is above WB (prob >= 0.20 so guard silent)
    assert mps5.wb_shift({"prob": 0.40, "t": 0, "w": 1.0}, 0.50, 0.10, 0.02) < 0


def test_wb_shift_cheap_no_guard():
    # WB caveat: low buckets under-price YES -> NO-ward tilt suppressed...
    assert mps5.wb_shift({"prob": 0.05, "t": 0, "w": 1.0}, 0.15, 0.10, 0.02) == 0.0
    # ...but YES-ward tilt in the low bucket is allowed (consistent w/ caveat)
    assert mps5.wb_shift({"prob": 0.15, "t": 0, "w": 1.0}, 0.05, 0.10, 0.02) > 0


def test_wb_shift_band_room_cap():
    # v=0.03, s_mine=0.025 -> room = 0.03-0.025-0.002 = 0.003 < TILT_MAX
    wb = {"prob": 0.90, "t": 0, "w": 1.0}
    assert abs(mps5.wb_shift(wb, 0.50, 0.03, 0.025) - 0.003) < 1e-12
    # no room at all -> 0.0, never negative-cap weirdness
    assert mps5.wb_shift(wb, 0.50, 0.03, 0.03) == 0.0


def _write(p, data):
    with open(p, "ab") as f:
        f.write(data)


def test_wb_feed_incremental_and_partial_line(tmp_path):
    p = str(tmp_path / "wb.jsonl")
    mps5.WB_TILT.clear()
    mps5._wb_feed["off"] = 0
    t0 = float(NOW_JUL)
    line = ('{"t": %f, "city": "New York City", "date": "2026-07-20", '
            '"market_id": "0xabc", "prob": 0.30, "model": "weather_temperature"}\n')
    _write(p, (line % t0).encode())
    # partial trailing line must NOT be consumed
    _write(p, b'{"t": %f, "city": "Tok' % t0)
    mps5.load_wb_feed(t0, path=p)
    assert mps5.WB_TILT["0xabc"]["prob"] == 0.30
    assert len(mps5.WB_TILT) == 1
    # complete the partial line -> picked up on the next read
    _write(p, ('yo", "date": "2026-07-20", "market_id": "0xdef", '
               '"prob": 0.40, "model": "weather_temperature"}\n').encode())
    mps5.load_wb_feed(t0, path=p)
    assert mps5.WB_TILT["0xdef"]["prob"] == 0.40
    # newer line for same market wins
    _write(p, (line % (t0 + 60)).replace('0.30', '0.35').encode())
    mps5.load_wb_feed(t0, path=p)
    assert mps5.WB_TILT["0xabc"]["prob"] == 0.35


def test_wb_feed_rotation_reset_and_staleness(tmp_path):
    p = str(tmp_path / "wb.jsonl")
    mps5.WB_TILT.clear()
    mps5._wb_feed["off"] = 0
    t0 = float(NOW_JUL)
    _write(p, ('{"t": %f, "city": "Berlin", "date": "2026-07-20", '
               '"market_id": "0x111", "prob": 0.55, '
               '"model": "weather_temperature"}\n' % t0).encode())
    mps5.load_wb_feed(t0, path=p)
    assert "0x111" in mps5.WB_TILT
    # rotation: file replaced by a shorter one -> offset resets, map rebuilt
    with open(p, "wb") as f:
        f.write(('{"t": %f, "city": "Paris", "date": "2026-07-20", '
                 '"market_id": "0x222", "prob": 0.60, '
                 '"model": "weather_temperature"}\n' % t0).encode())
    mps5.load_wb_feed(t0, path=p)
    assert "0x222" in mps5.WB_TILT and "0x111" not in mps5.WB_TILT
    # staleness prune: entry older than WB_STALE_S dropped
    mps5.load_wb_feed(t0 + mps5.WB_STALE_S + 1, path=p)
    assert mps5.WB_TILT == {}
    # malformed lines and missing files never raise
    _write(p, b"not json at all\n")
    mps5.load_wb_feed(t0, path=p)
    mps5.load_wb_feed(t0, path=str(tmp_path / "missing.jsonl"))


def test_q_mine_two_sided_min_for_tilted_only():
    v, msz = 0.10, 100.0
    # P0-P5: historical bid-side proxy, EXACTLY as before (same abs() expr)
    assert mps5.q_mine_of(P["P0_base"], v, 0.50, 0.48, 0.52, msz, 0.0) == \
           mps5.S(v, abs(0.50 - 0.48), msz)
    # P6 untilted (tilt_q=0): same expression as P0 -> exact pairing
    assert mps5.q_mine_of(P["P6_tilted"], v, 0.50, 0.48, 0.52, msz, 0.0) == \
           mps5.q_mine_of(P["P0_base"], v, 0.50, 0.48, 0.52, msz, 0.0)
    # P6 tilted up: bid closer (0.015), ask farther (0.025) -> min side wins;
    # the bid-side proxy would have INFLATED the score (review finding 1)
    got = mps5.q_mine_of(P["P6_tilted"], v, 0.50, 0.485, 0.525, msz, 0.005)
    ask_side = mps5.S(v, abs(0.525 - 0.50), msz)
    bid_side = mps5.S(v, abs(0.50 - 0.485), msz)
    assert got == ask_side < bid_side


def test_wb_feed_nonfinite_and_implausible_t(tmp_path):
    p = str(tmp_path / "wb.jsonl")
    mps5.WB_TILT.clear()
    mps5._wb_feed["off"] = 0
    t0 = float(NOW_JUL)
    for bad_t in ("NaN", "Infinity", "1784314656000", "-5"):   # ms-epoch, neg
        _write(p, ('{"t": %s, "city": "Berlin", "date": "2026-07-20", '
                   '"market_id": "0xbad", "prob": 0.5, '
                   '"model": "weather_temperature"}\n' % bad_t).encode())
    mps5.load_wb_feed(t0, path=p)
    assert "0xbad" not in mps5.WB_TILT   # none may become an immortal entry


def test_wb_feed_longer_replace_detected(tmp_path):
    p = str(tmp_path / "wb.jsonl")
    mps5.WB_TILT.clear()
    mps5._wb_feed["off"] = 0
    t0 = float(NOW_JUL)
    _write(p, ('{"t": %f, "city": "Berlin", "date": "2026-07-20", '
               '"market_id": "0x111", "prob": 0.55, '
               '"model": "weather_temperature"}\n' % t0).encode())
    mps5.load_wb_feed(t0, path=p)
    assert "0x111" in mps5.WB_TILT
    # file REPLACED by a LONGER one (same size-check passes) — the head-
    # prefix guard must force a full re-read, not a misaligned tail read
    with open(p, "wb") as f:
        f.write(('{"t": %f, "city": "Madrid", "date": "2026-07-20", '
                 '"market_id": "0x333", "prob": 0.65, '
                 '"model": "weather_temperature"}\n' % t0).encode() * 3)
    mps5.load_wb_feed(t0, path=p)
    assert "0x333" in mps5.WB_TILT and "0x111" not in mps5.WB_TILT


def test_wb_feed_cid_lowercased(tmp_path):
    p = str(tmp_path / "wb.jsonl")
    mps5.WB_TILT.clear()
    mps5._wb_feed["off"] = 0
    t0 = float(NOW_JUL)
    _write(p, ('{"t": %f, "city": "Berlin", "date": "2026-07-20", '
               '"market_id": "0xAbCd", "prob": 0.55, '
               '"model": "weather_temperature"}\n' % t0).encode())
    mps5.load_wb_feed(t0, path=p)
    assert "0xabcd" in mps5.WB_TILT and "0xAbCd" not in mps5.WB_TILT


def test_p6_gates_identical_to_p0():
    for m, mid in ((mk(sector="esports", game_start=NOW - 600), 0.5),
                   (mk(sector="weather"), 0.95),
                   (mk(end="2026-07-17", end_ts=NOW + 8 * 3600), 0.5),
                   (mk(), 0.5)):
        assert mps5.gate(m, {}, {}, P["P6_tilted"], NOW, mid) == \
               mps5.gate(m, {}, {}, P["P0_base"], NOW, mid), m


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
