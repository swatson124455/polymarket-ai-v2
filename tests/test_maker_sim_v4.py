"""Unit tests for maker_paper_sim_v4 NEW mechanisms (D2-D7).

Inherited v3 code (WS books, tape fetch, discovery pagination, persistence)
is live-verified on the running arms and mostly not re-tested here — EXCEPT
the price_change parser: the "live-verified" assumption failed there (the
legacy-only parser silently dropped every batched-shape message; found and
fixed in v3 as 0c9708f, ported here), so the batched shape IS covered below.
Everything NEW in v4 is: the inverted/settled gates, the parity A/B, the
split-inventory lifecycle, the D5 both-token fill engine with cross-view
dedup, and the D6 one-sided score haircut. No network access anywhere in
this file.
"""
import importlib.util
import json
import pathlib
import time

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mps4", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "maker_paper_sim_v4.py")
mps4 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mps4)

MSZ = 10.0
NOW = time.time()


def tr(ts, price, asset, size=25.0, tx="0xabc"):
    return {"timestamp": ts, "price": price, "asset": asset, "size": size,
            "transactionHash": tx}


def mk(game_start=None):
    return {"id": "77", "game_start": game_start, "v": 0.03, "msz": MSZ}


# ── D4: parity assignment ────────────────────────────────────────────────────
def test_arm_parity():
    assert mps4.arm_of("1234") == "classic"      # even
    assert mps4.arm_of("12345") == "split"       # odd
    assert mps4.arm_of("x") == "classic"         # non-numeric -> classic
    assert mps4.arm_of("") == "classic"


# ── D2/D7: gate truth table ──────────────────────────────────────────────────
def test_gate_pre_game_gated_in_play_quoted():
    st = {}
    assert mps4.gate(mk(NOW + 600), st, NOW, 0.5) == "pre_game"
    assert mps4.gate(mk(NOW - 600), st, NOW, 0.5) is None


def test_gate_settled_pulls_finished_games():
    st = {}
    assert mps4.gate(mk(NOW - 600), st, NOW, 0.93) == "settled"
    assert mps4.gate(mk(NOW - 600), st, NOW, 0.07) == "settled"
    assert mps4.gate(mk(NOW - 600), st, NOW, 0.91) is None


def test_gate_precedence_and_vol_pull():
    # pre_game wins over settled-looking mid
    assert mps4.gate(mk(NOW + 600), {}, NOW, 0.95) == "pre_game"
    st = {"pull_until": NOW + 100}
    assert mps4.gate(mk(NOW - 600), st, NOW, 0.5) == "vol_pull"
    # deliberately NO last_hours gate in v4 — a live mid-band game never
    # gets gated by wall-clock hour (v3 would have gated at 19:00Z)
    assert mps4.gate(mk(NOW - 600), {}, NOW, 0.5) is None


def test_gate_missing_game_start_treated_as_in_play():
    # discovery guarantees game_start, but a None must not crash or gate
    assert mps4.gate(mk(None), {}, NOW, 0.5) is None


# ── quote-change detection with None sides ───────────────────────────────────
def test_quote_changed_none_transitions():
    qc = mps4._quote_changed
    assert qc(None, 0.5) and qc(0.5, None)
    assert not qc(None, None)
    assert not qc(0.5000, 0.5001)                # < 2 ticks
    assert qc(0.5000, 0.5021)                    # >= 2 ticks


# ── D6: one-sided score haircut ──────────────────────────────────────────────
def test_qmine_two_sided_full_score():
    st = {"bid": 0.49, "ask": 0.51}
    q, n = mps4.my_qmine(st, 0.50, 0.03, MSZ)
    assert n == 2
    assert q == pytest.approx(((0.03 - 0.01) / 0.03) ** 2 * MSZ)


def test_qmine_one_sided_third_in_band_zero_outside():
    st = {"bid": None, "ask": 0.51}
    full = ((0.03 - 0.01) / 0.03) ** 2 * MSZ
    q, n = mps4.my_qmine(st, 0.50, 0.03, MSZ)
    assert n == 1 and q == pytest.approx(full / 3.0)
    st2 = {"bid": 0.94, "ask": None}
    q2, n2 = mps4.my_qmine(st2, 0.95, 0.03, MSZ)   # outside [0.10, 0.90]
    assert n2 == 1 and q2 == 0.0


def test_qmine_no_quotes():
    assert mps4.my_qmine({}, 0.5, 0.03, MSZ) == (0.0, 0)


# ── D4: split lifecycle ──────────────────────────────────────────────────────
def test_split_init_once():
    st = {}
    mps4.split_init(st, MSZ, NOW)
    assert st["capital"] == MSZ and st["yes_inv"] == MSZ and st["no_inv"] == MSZ
    st["cash"] = 3.0
    mps4.split_init(st, MSZ, NOW)                # idempotent
    assert st["cash"] == 3.0 and st["capital"] == MSZ


def test_resplit_only_when_both_empty_and_capped():
    st = {}
    mps4.split_init(st, MSZ, NOW)
    st["yes_inv"] = 0.0
    assert not mps4.maybe_resplit(st, MSZ, NOW)  # one side still stocked
    st["no_inv"] = 0.0
    for i in range(mps4.RESPLIT_MAX_PER_DAY):
        assert mps4.maybe_resplit(st, MSZ, NOW)
        st["yes_inv"] = st["no_inv"] = 0.0
    assert not mps4.maybe_resplit(st, MSZ, NOW)  # daily cap
    assert st["capital"] == MSZ * (1 + mps4.RESPLIT_MAX_PER_DAY)
    st["rs_day"] = "19990101"                    # day rollover resets counter
    assert mps4.maybe_resplit(st, MSZ, NOW)


# ── D5: fill engine ──────────────────────────────────────────────────────────
def _quoted_split_state():
    st = {"qh": [[0.0, 0.48, 0.52]]}
    mps4.split_init(st, MSZ, NOW)
    return st


def test_split_round_trip_realizes_spread():
    st = _quoted_split_state()
    tape = [tr(1.0, 0.53, "Y", tx="0x1"),        # lifts our YES ask 0.52
            tr(2.0, 0.53, "N", tx="0x2")]        # NO print -> py 0.47 < bid 0.48
    fills = mps4.match_prints(st, "split", "Y", "N", MSZ, tape, 0.0)
    assert fills == 2
    assert st["yes_inv"] == 0.0 and st["no_inv"] == 0.0
    assert st["cash"] == pytest.approx(MSZ * 0.52 * 2)
    # full pair round-trip banks exactly the quoted spread, mark-independent
    assert mps4.net_of(st, "split", 0.5) == pytest.approx(2 * MSZ * 0.02)
    assert mps4.net_of(st, "split", 0.9) == pytest.approx(2 * MSZ * 0.02)


def test_split_inventory_never_negative():
    st = _quoted_split_state()
    tape = [tr(1.0, 0.53, "Y", tx="0x1"), tr(2.0, 0.54, "Y", tx="0x2")]
    fills = mps4.match_prints(st, "split", "Y", "N", MSZ, tape, 0.0)
    assert fills == 1                            # second sell blocked: no YES left
    assert st["yes_inv"] == 0.0


def test_split_loss_bounded_by_capital():
    # sell one side high, other side goes worthless: loss < capital
    st = _quoted_split_state()
    mps4.match_prints(st, "split", "Y", "N", MSZ,
                      [tr(1.0, 0.53, "Y")], 0.0)  # sold YES at 0.52
    worst = mps4.net_of(st, "split", 1.0)         # NO side -> worth 0
    assert worst == pytest.approx(MSZ * 0.52 - MSZ)   # -4.8 > -capital(-10)
    assert worst > -st["capital"]


def test_d5_complement_print_fills_classic_bid():
    st = {"qh": [[0.0, 0.39, 0.45]]}
    tape = [tr(1.0, 0.62, "N")]                  # py = 0.38 < bid 0.39
    fills = mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0)
    assert fills == 1 and st["pos"] == MSZ
    assert st["cost"] == pytest.approx(MSZ * 0.39)


def test_same_second_prints_both_fill():
    # scan #2: data-api timestamps are integer seconds; ~24% of live prints
    # share a second. Old watermark dropped the second one — both must count.
    st = {"qh": [[0.0, 0.48, 0.52]]}
    tape = [tr(1.0, 0.47, "Y", tx="0x1"),        # fills our bid (buy)
            tr(1.0, 0.53, "Y", tx="0x2")]        # SAME second: fills our ask
    fills = mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0)
    assert fills == 2 and st["pos"] == 0.0       # offsetting round trip
    assert st["real"] == pytest.approx(MSZ * (0.52 - 0.48))


def test_edge_second_exactly_once_across_ticks():
    # prints AT the watermark are re-fetched next tick; the persisted
    # edge-identity set must skip them while accepting NEW same-second prints
    st = {"qh": [[0.0, 0.48, 0.52]]}
    p1 = tr(5.0, 0.47, "Y", tx="0x1")
    assert mps4.match_prints(st, "classic", "Y", "N", MSZ, [p1], 0.0) == 1
    assert st["last_trade_ts"] == 5.0 and st["seen_edge"]
    # persistence round-trip (seen_edge survives as JSON lists)
    st2 = json.loads(json.dumps(st))
    p2 = tr(5.0, 0.53, "Y", tx="0x2")            # new print, same edge second
    fills = mps4.match_prints(st2, "classic", "Y", "N", MSZ, [p1, p2], 0.0)
    assert fills == 1                            # p1 deduped, p2 fills
    assert st2["pos"] == 0.0                     # round trip completed


def test_watermark_advances_on_unquoted_prints():
    # a print during a gated window must advance the watermark (no reprocess)
    st = {"qh": [[0.0, 0.48, 0.52], [5.0, None, None]]}
    tape = [tr(6.0, 0.40, "Y")]
    assert mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0) == 0
    assert st["last_trade_ts"] == 6.0


def test_classic_cap_is_one_msz():
    st = {"qh": [[0.0, 0.48, 0.52]]}
    tape = [tr(1.0, 0.47, "Y", tx="0x1"), tr(2.0, 0.46, "Y", tx="0x2")]
    fills = mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0)
    assert fills == 1 and st["pos"] == MSZ       # D3: +/-1x, second buy blocked


def test_prints_before_since_and_unknown_assets_ignored():
    st = {"qh": [[0.0, 0.48, 0.52]]}
    tape = [tr(-5.0, 0.40, "Y"), tr(1.0, 0.40, "OTHER")]
    assert mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0) == 0
    assert st.get("pos", 0) == 0


def test_gated_quote_window_blocks_fills():
    # quotes pulled at t=5 ([None, None]); print at t=6 must not fill
    st = {"qh": [[0.0, 0.48, 0.52], [5.0, None, None]]}
    tape = [tr(6.0, 0.40, "Y")]
    assert mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0) == 0


def test_one_sided_quote_still_fills_active_side():
    # split arm out of NO inventory -> bid None, ask live
    st = {"qh": [[0.0, None, 0.52]]}
    mps4.split_init(st, MSZ, NOW)
    st["no_inv"] = 0.0
    fills = mps4.match_prints(st, "split", "Y", "N", MSZ,
                              [tr(1.0, 0.53, "Y")], 0.0)
    assert fills == 1 and st["yes_inv"] == 0.0


def test_net_of_classic_matches_manual():
    st = {"real": 1.5, "pos": MSZ, "cost": 4.8}
    assert mps4.net_of(st, "classic", 0.50) == pytest.approx(1.5 + MSZ * 0.5 - 4.8)
    assert mps4.net_of({}, "split", 0.5) == 0.0  # uninitialized split


# ── D1: universe filter ──────────────────────────────────────────────────────
def test_game_sector_labels_never_exclude():
    # game_sector is ATTRIBUTION ONLY (operator: v4 covers all it can);
    # the sole scope filter is gameStartTime presence, applied in discover()
    gs = mps4.game_sector
    assert gs({"slug": "mlb-yankees-vs-red-sox", "question": ""}) == "sports"
    assert gs({"category": "Sports", "slug": "", "question": ""}) == "sports"
    assert gs({"slug": "lol-t1-vs-geng", "question": ""}) == "esports"
    assert gs({"slug": "cs2-ewc-final", "question": ""}) == "esports"
    assert gs({"category": "Esports", "slug": "world-cup-showmatch",
               "question": ""}) == "esports"
    # esports keyword beats sports keyword when both appear
    assert gs({"slug": "ewc-world-cup-cs2", "question": ""}) == "esports"
    assert gs({"slug": "overwatch-grand-final", "question": ""}) == "esports"
    # category is authoritative as a LABEL; nothing maps to None
    assert gs({"category": "Politics",
               "slug": "candidate-a-vs-candidate-b", "question": ""}) == "politics"
    # full KW labeling (gamma stamps gameStartTime on dailies too)
    assert gs({"slug": "bitcoin-up-or-down", "question": ""}) == "crypto"
    assert gs({"slug": "spy-up-or-down-july-16", "question": ""}) == "finance"
    assert gs({"slug": "", "question": "Will the highest temperature in "
               "Paris be 33°C on July 16?"}) == "weather"
    assert gs({"slug": "some-novel-thing", "question": "???"}) == "other"


def test_parse_iso_short_offset():
    # gamma live format (scan HIGH): "+00" chokes fromisoformat on py<=3.10
    ts = mps4.parse_iso("2026-07-16 00:00:00+00")
    assert ts is not None
    assert ts == mps4.parse_iso("2026-07-16T00:00:00+00:00")
    assert mps4.parse_iso("2026-07-16T00:00:00Z") == ts
    assert mps4.parse_iso("") is None and mps4.parse_iso(None) is None


# ── resolution backfill (scan CRITICAL #1) ───────────────────────────────────
def test_finalize_dropped_marks_residual_to_resolution(monkeypatch):
    st = {"arm": "classic", "pos": MSZ, "cost": MSZ * 0.5, "real": 0.0,
          "bid": 0.49, "ask": 0.51, "qh": [[0.0, 0.49, 0.51]],
          "net": 4.2}                            # stale mark at mid 0.92
    state = {"123": st}
    monkeypatch.setattr(mps4, "get", lambda url, timeout=10:
                        {"closed": True, "outcomePrices": '["0", "1"]'})
    mps4.finalize_dropped(state, set())          # market left the universe
    assert st["final"] == 1 and st["final_mid"] == 0.0
    # long-the-loser marked to 0, not the frozen 0.92-ish mid
    assert st["net"] == pytest.approx(0.0 + MSZ * 0.0 - MSZ * 0.5)
    assert st["bid"] is None and st["ask"] is None and st["qh"] == []


def test_finalize_dropped_retries_until_closed(monkeypatch):
    st = {"arm": "split", "capital": MSZ, "cash": 5.2,
          "yes_inv": 0.0, "no_inv": MSZ, "pos": 0.0}
    state = {"9": st}
    monkeypatch.setattr(mps4, "get", lambda url, timeout=10: {"closed": False})
    mps4.finalize_dropped(state, set())
    assert "final" not in st                     # not closed -> retry later
    monkeypatch.setattr(mps4, "get", lambda url, timeout=10:
                        {"closed": True, "outcomePrices": '["1", "0"]'})
    mps4.finalize_dropped(state, set())
    # split residual: NO side worthless at YES=1.0
    assert st["final"] == 1
    assert st["net"] == pytest.approx(5.2 + 0.0 + MSZ * 0.0 - MSZ)


def test_finalize_skips_universe_members_and_flat_states(monkeypatch):
    live = {"arm": "classic", "pos": MSZ, "bid": 0.5, "ask": 0.52}
    flat = {"arm": "classic", "pos": 0.0}
    state = {"in": live, "out": flat}
    calls = []
    monkeypatch.setattr(mps4, "get", lambda url, timeout=10: calls.append(url))
    mps4.finalize_dropped(state, {"in"})
    assert live.get("bid") == 0.5                # untouched: still in universe
    assert flat["final"] == 1 and not calls      # flat: finalized w/o a fetch


# ── WS price_change parsing (batched 2026 shape; port of v3 fix 0c9708f) ─────
def _seed_book(asset, bids=None, asks=None):
    mps4.BOOKS[asset] = {"bids": dict(bids or {}), "asks": dict(asks or {}),
                         "ts": 0.0}


def test_batched_price_change_updates_multiple_assets():
    mps4.BOOKS.clear()
    _seed_book("a1", bids={0.40: 10.0}, asks={0.60: 10.0})
    _seed_book("a2", bids={0.30: 5.0})
    msg = {"event_type": "price_change", "market": "0xm",
           "price_changes": [
               {"asset_id": "a1", "price": "0.41", "size": "7", "side": "BUY"},
               {"asset_id": "a1", "price": "0.60", "size": "0", "side": "SELL"},
               {"asset_id": "a2", "price": "0.35", "size": "3", "side": "SELL"},
           ]}
    mps4._apply_price_change_batched(msg)
    assert mps4.BOOKS["a1"]["bids"][0.41] == 7.0
    assert 0.60 not in mps4.BOOKS["a1"]["asks"]      # size 0 removes the level
    assert mps4.BOOKS["a2"]["asks"][0.35] == 3.0
    assert mps4.BOOKS["a1"]["ts"] > 0 and mps4.BOOKS["a2"]["ts"] > 0


def test_batched_price_change_ignores_unknown_and_malformed():
    mps4.BOOKS.clear()
    _seed_book("a1", bids={0.40: 10.0})
    msg = {"event_type": "price_change",
           "price_changes": [
               {"asset_id": "ghost", "price": "0.5", "size": "1", "side": "BUY"},
               {"asset_id": "a1", "price": "oops", "size": "1", "side": "BUY"},
               "not-a-dict",
               {"asset_id": "a1", "price": "1.5", "size": "1", "side": "BUY"},
           ]}
    mps4._apply_price_change_batched(msg)            # must not raise
    assert mps4.BOOKS["a1"]["bids"] == {0.40: 10.0}  # out-of-range/bad: no-op


def test_legacy_single_asset_price_change_still_applies():
    mps4.BOOKS.clear()
    _seed_book("a1", asks={0.55: 4.0})
    msg = {"event_type": "price_change", "asset_id": "a1",
           "changes": [{"price": "0.55", "size": "9", "side": "SELL"}]}
    mps4._apply_price_change("a1", msg)
    assert mps4.BOOKS["a1"]["asks"][0.55] == 9.0
