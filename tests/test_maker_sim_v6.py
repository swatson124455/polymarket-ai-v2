"""Unit tests for maker_paper_sim_v6 — the NEGRISK LAB arm.

Inherited v5 core (WS books, tape fetch, match_window, gate) is covered by
test_maker_sim_v5.py against the shared lineage; what is NEW and tested here
is the two-policy matrix, the one-winner netted floor (event_worst), and the
N1 single-coverage semantics. No network access anywhere in this file.
"""
import calendar
import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "mps6", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "maker_paper_sim_v6.py")
mps6 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mps6)

P = mps6.POLICIES
NOW = calendar.timegm((2026, 7, 17, 20, 0, 0))
MSZ = 100.0


def mk(**kw):
    m = {"id": "1234", "sector": "politics", "v": 0.03, "msz": MSZ,
         "yes": "Y", "no": "N", "q": "?", "pool": 100.0, "ev": "0xev1",
         "flag": False, "nout_total": 5,
         "end": "2099-01-01", "end_ts": None, "game_start": None}
    m.update(kw)
    return m


# ── policy matrix ────────────────────────────────────────────────────────────
def test_policy_matrix_shape():
    assert set(P) == {"N0_all", "N1_single"}
    assert not P["N0_all"]["single"] and P["N1_single"]["single"]
    # gates must be IDENTICAL — coverage is the only experimental variable
    for k in ("gated", "vol_pts", "vol_s", "ramp_h", "tapevel"):
        assert P["N0_all"][k] == P["N1_single"][k], k


def test_gate_parity_between_policies():
    for m, mid in ((mk(sector="esports", game_start=NOW - 600), 0.5),
                   (mk(sector="weather"), 0.95),
                   (mk(end="2026-07-17"), 0.5),
                   (mk(), 0.5)):
        assert mps6.gate(m, {}, {}, P["N0_all"], NOW, mid) == \
               mps6.gate(m, {}, {}, P["N1_single"], NOW, mid), m


# ── one-winner netted floor ──────────────────────────────────────────────────
def test_event_worst_self_hedged_full_coverage():
    # long msz on ALL 5 outcomes of a 5-outcome event at avg price 0.2 each:
    # exactly one pays out msz, cost = 5*0.2*msz -> floor = 0 (self-hedged)
    poss = [MSZ] * 5
    cost = 5 * 0.2 * MSZ
    assert mps6.event_worst(poss, cost, 5, 5) == MSZ - cost == 0.0


def test_event_worst_partial_coverage_adds_zero_branch():
    # same book but only 4 of 5 outcomes covered: the uncovered one can win
    # and every covered position pays 0 -> floor = -cost
    poss = [MSZ] * 4
    cost = 4 * 0.2 * MSZ
    assert mps6.event_worst(poss, cost, 4, 5) == -cost


def test_event_worst_single_market_baseline():
    # N1 shape: one long position among idle siblings (pos 0 states exist)
    poss = [MSZ, 0.0, 0.0]
    cost = 0.4 * MSZ
    assert mps6.event_worst(poss, cost, 3, 5) == -cost   # other outcome wins


def test_event_worst_short_positions():
    # short one outcome: if it wins, pay out the short
    poss = [-MSZ, MSZ]
    cost = -0.3 * MSZ    # net credit from the short leg
    assert mps6.event_worst(poss, cost, 2, 2) == -MSZ + 0.3 * MSZ


def test_event_worst_empty():
    assert mps6.event_worst([], 0.0, 0, 5) == 0.0


# ── netted_rollup: departed inventory + coverage display ─────────────────────
def _st(pos=0.0, cost=0.0, acc=0.0, bid=None, ev=None, nout=None):
    st = {"pos": pos, "cost": cost, "acc": acc, "real": 0.0}
    if bid is not None:
        st["bid"] = bid
    if ev is not None:
        st["ev"] = ev
    if nout is not None:
        st["nout"] = nout
    return st


def test_rollup_departed_short_stays_in_floor():
    # review finding 1: a departed short must NOT vanish from the floor.
    # Live: long 100 @ cost 20. Departed (not in uinfo): short -100, credit -30.
    state = {
        "m1|N0_all": _st(pos=100.0, cost=20.0, acc=5.0),
        "m1|SH": {"last_mid": 0.2},
        "m2|N0_all": _st(pos=-100.0, cost=-30.0, ev="0xev1", nout=5),
        "m2|SH": {"last_mid": 0.4},
    }
    uinfo = {"m1": {"ev": "0xev1", "nout_total": 5}}
    (r,) = mps6.netted_rollup(state, uinfo)
    assert r["dep"] == 1
    # floor: winners = {m1: 100, m2: -100, 0-branch} -> minw=-100;
    # cost = 20 - 30 = -10 -> worst = -100 - (-10) = -90
    assert r["worst"] == -90.0 and r["cap"] == 90.0
    # without the departed leg the floor would have been 0 - 20 = -20 (wrong)


def test_rollup_cov_counts_quoted_or_held_only():
    # review finding 5: N1 has a state entry for EVERY market; cov must not
    # count idle entries
    state = {
        "m1|N1_single": _st(pos=50.0, cost=10.0, bid=0.4),
        "m2|N1_single": _st(),          # idle entry, no quote, no inventory
        "m3|N1_single": _st(),
        "m1|SH": {"last_mid": 0.4}, "m2|SH": {}, "m3|SH": {},
    }
    uinfo = {"m1": {"ev": "0xe", "nout_total": 6},
             "m2": {"ev": "0xe", "nout_total": 6},
             "m3": {"ev": "0xe", "nout_total": 6}}
    (r,) = mps6.netted_rollup(state, uinfo)
    assert r["cov"] == 1 and r["nout"] == 6
    assert r["worst"] == -10.0          # another outcome wins -> lose cost


def test_rollup_skips_unknown_and_legacy_keys():
    state = {"m9|SH": {"last_mid": 0.5},
             "m9|OLDPOLICY": _st(pos=1.0),      # not in POLICIES
             "m8|N0_all": _st(pos=1.0)}         # no ev anywhere -> skipped
    assert mps6.netted_rollup(state, {}) == []


# ── inherited pieces that must stay correct in the clone ─────────────────────
def test_match_window_inherited():
    st = {}
    qh = [[0.0, 0.48, 0.52]]
    fills, mts = mps6.match_window(
        [{"timestamp": 1.0, "price": 0.47, "asset": "Y", "size": 25.0,
          "transactionHash": "0x1"},
         {"timestamp": 2.0, "price": 0.53, "asset": "Y", "size": 25.0,
          "transactionHash": "0x2"}], qh, st, MSZ, "Y", 0.0)
    assert fills == 2 and st["pos"] == 0.0
    assert st["real"] == round(MSZ * (0.52 - 0.48), 4)


def test_batched_price_change_inherited():
    mps6.BOOKS.clear()
    mps6.BOOKS["a1"] = {"bids": {0.4: 10.0}, "asks": {0.6: 10.0}, "ts": 0.0}
    mps6._apply_price_change_batched(
        {"price_changes": [{"asset_id": "a1", "price": "0.41", "size": "7",
                            "side": "BUY"}]})
    assert mps6.BOOKS["a1"]["bids"][0.41] == 7.0
