"""Unit tests for maker_paper_sim_v4 NEW mechanisms (D2-D7).

Inherited v3 code (WS books, tape fetch, discovery pagination, persistence)
is live-verified on the running arms and not re-tested here. Everything NEW
in v4 is: the inverted/settled gates, the parity A/B, the split-inventory
lifecycle, the D5 both-token fill engine with cross-view dedup, and the D6
one-sided score haircut. No network access anywhere in this file.
"""
import importlib.util
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


def test_d5_cross_view_dedup_single_fill():
    st = {"qh": [[0.0, 0.48, 0.52]]}
    same = dict(ts=1.0, size=25.0, tx="0xsame")
    tape = [tr(same["ts"], 0.53, "Y", size=same["size"], tx=same["tx"]),
            tr(same["ts"], 0.47, "N", size=same["size"], tx=same["tx"])]
    fills = mps4.match_prints(st, "classic", "Y", "N", MSZ, tape, 0.0)
    assert fills == 1                            # one trade, two views


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
def test_is_sports_excludes_esports_and_others():
    assert mps4.is_sports({"slug": "mlb-yankees-vs-red-sox", "question": ""})
    assert mps4.is_sports({"category": "Sports", "slug": "", "question": ""})
    assert not mps4.is_sports({"slug": "lol-t1-vs-geng", "question": ""})
    assert not mps4.is_sports({"slug": "cs2-ewc-final", "question": ""})
    assert not mps4.is_sports({"slug": "bitcoin-up-or-down", "question": ""})
    # esports keyword beats sports keyword when both appear
    assert not mps4.is_sports({"slug": "ewc-world-cup-cs2", "question": ""})
