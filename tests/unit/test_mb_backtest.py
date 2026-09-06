"""Unit tests for scripts/mb_backtest.py — the walk-forward backtest
harness (build 1, 2026-09-06 mandate). Pure core only; the module's
self-test covers the same behaviors and the VPS runs the I/O paths.
Focus = the properties that make the harness honest: no lookahead,
holdout-only judging, measured haircut applied, money floor enforced,
UNKNOWN-concurrency alarmed, canon primitives consumed not re-implemented.
Run: python3 -m pytest tests/unit/test_mb_backtest.py --override-ini "addopts=" """
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import cohort5_qualification as cq  # noqa: E402
import mb_backtest as mbt  # noqa: E402

T0 = mbt.parse_iso_z("2026-09-01T00:00:00Z")
DAY = mbt.DAY_S


def _rec(tok, ts, fill=0.48):
    return {"trader": "0xw", "token_id": tok, "detect_ts": ts,
            "first_buy": True, "verdict": "OK", "shadow_fill": fill}


def test_daily_replay_no_lookahead():
    """Verdict may lock only once resolutions EXIST on the day grid —
    outcomes known to the analyst but resolved later must not leak."""
    recs = [_rec(f"m{i}", T0 + i) for i in range(30)]
    outc = {f"m{i}": 1 for i in range(30)}
    r_at = {f"m{i}": T0 + 3 * DAY + 60 for i in range(30)}
    rep = mbt.daily_replay(recs, outc, r_at, {}, {}, T0, T0 + 10 * DAY)
    exp_day = (int((T0 + 3 * DAY + 60) // DAY) + 1) * DAY
    assert rep["verdict"] == "QUALIFIES"
    assert rep["verdict_ts"] == exp_day  # NOT the day-1 boundary
    assert rep["n"] == 30


def test_daily_replay_money_floor():
    """e-pass at tiny throughput must fail the ruled $100/wk floor
    (a percentage on a handful of bets is never a pass)."""
    recs = [_rec(f"s{i}", T0 + i * 10 * DAY) for i in range(30)]
    outc = {f"s{i}": 1 for i in range(30)}
    r_at = {f"s{i}": T0 + 299 * DAY for i in range(30)}
    rep = mbt.daily_replay(recs, outc, r_at, {}, {}, T0, T0 + 400 * DAY)
    assert rep["verdict"] == "E-PASS BELOW MONEY FLOOR"


def test_daily_replay_futility():
    recs = [_rec(f"n{i}", T0 + i, fill=0.50) for i in range(300)]
    outc = {f"n{i}": (1 if i % 2 == 0 else 0) for i in range(300)}
    r_at = {f"n{i}": T0 + DAY / 2 for i in range(300)}
    rep = mbt.daily_replay(recs, outc, r_at, {}, {}, T0, T0 + 10 * DAY)
    assert rep["verdict"] == "NOT DEMONSTRATED (futility)"
    assert rep["n"] == cq.C1_FUTILITY_N


def test_daily_replay_unplaceable_counted():
    rep = mbt.daily_replay([_rec("m0", T0)], {"m0": 1}, {}, {}, {}, T0,
                           T0 + 2 * DAY)
    assert rep["unplaceable_resolved"] == 1 and rep["n"] == 0


def test_holdout_excludes_train():
    """The ranking number may never touch pre-split markets."""
    split = T0 + 5 * DAY
    recs = ([_rec(f"tr{i}", T0 + i) for i in range(10)]
            + [_rec(f"ho{i}", split + i) for i in range(5)])
    outc = {f"tr{i}": 1 for i in range(10)}
    outc.update({f"ho{i}": 1 for i in range(5)})
    hm = mbt.holdout_metrics(recs, outc, {}, {}, split, split + 7 * DAY)
    assert hm["n_holdout"] == 5
    assert hm["holdout_days"] == 7.0
    roi_exp = (1.0 - 0.48 - 0.02 * 0.48) / 0.48   # ruled ROI basis, exact
    assert abs(hm["roi_realized"] - roi_exp) < 1e-12
    assert abs(hm["wk_net_real"] - roi_exp * 100 * 5) < 1e-9


def test_holdout_label_lookahead_guard():
    """A market with a KNOWN resolved_at AFTER end_ts must not count —
    its label did not exist at judge time. Unknown res_at passes (the
    disclosed DB-label asymmetry)."""
    split = T0
    end = T0 + 4 * DAY
    recs = [_rec("early", split + 1), _rec("late", split + 2),
            _rec("norat", split + 3)]
    outc = {"early": 1, "late": 1, "norat": 1}
    r_at = {"early": end - DAY, "late": end + DAY}   # "late" leaks w/o guard
    hm = mbt.holdout_metrics(recs, outc, {}, {}, split, end, res_at=r_at)
    assert hm["n_holdout"] == 2      # early + norat; late excluded
    hm2 = mbt.holdout_metrics(recs, outc, {}, {}, split, end)  # no res_at
    assert hm2["n_holdout"] == 3     # legacy behavior unchanged


def test_synth_ladder_aware_wagers():
    """Operator hardcode 2026-09-06: same-tx fills merge to one wager at
    VWAP; ladder adds in new txs are SEPARATE wagers (first-buy-only was
    flawed); SELLs ignored; max_fill gate; haircut applied."""
    rows = [{"s": "BUY", "tok": "t1", "p": 0.50, "z": 10.0, "t": 100.0,
             "tx": "0xa"},
            {"s": "BUY", "tok": "t1", "p": 0.60, "z": 30.0, "t": 101.0,
             "tx": "0xa"},   # same tx -> VWAP 0.575
            {"s": "BUY", "tok": "t1", "p": 0.52, "z": 5.0, "t": 200.0,
             "tx": "0xb"},   # ladder add: its own wager
            {"s": "BUY", "tok": "t2", "p": 0.975, "z": 5.0, "t": 300.0,
             "tx": "0xc"},   # gated at 0.98
            {"s": "SELL", "tok": "t3", "p": 0.30, "z": 5.0, "t": 400.0,
             "tx": "0xd"}]   # not a BUY
    sy, gated = mbt.synth_records(rows, "0xw", 0.02)
    assert len(sy) == 2 and gated == 1
    assert abs(sy[0]["shadow_fill"] - 0.595) < 1e-12
    assert abs(sy[1]["shadow_fill"] - 0.54) < 1e-12
    assert sy[0]["first_buy"] is True and sy[1]["first_buy"] is False


def test_haircut_measured_from_ok_first_buys_only():
    recs = [{"first_buy": True, "verdict": "OK", "shadow_fill": 0.52,
             "whale_price": 0.50},
            {"first_buy": False, "verdict": "OK", "shadow_fill": 0.9,
             "whale_price": 0.1},
            {"first_buy": True, "verdict": "NO_BOOK", "shadow_fill": None,
             "whale_price": 0.5}]
    h = mbt.measure_haircut(recs)
    assert h["n"] == 1 and abs(h["med"] - 0.02) < 1e-12
    assert mbt.measure_haircut([]) is None


def test_screen_unknown_conc_is_alarmed_not_passed():
    wrows = [{"w": "0xa", "n": 30, "usd_sum": 1.0},
             {"w": "0xd", "n": 40, "usd_sum": 2.0}]      # conc UNKNOWN
    cands, _sens, unk = mbt.screen_candidates(
        wrows, {"0xa": 4}, mbt.ELIGIBILITY_MIN_TRADES, 5)
    assert [c["w"] for c in cands] == ["0xa"]
    assert unk == 1


def test_conc_replay_sell_exit_beats_resolution():
    recs = [{"token_id": "t1", "detect_ts": 100.0},
            {"token_id": "t3", "detect_ts": 120.0}]
    assert mbt.peak_concurrency_replay(recs, {"t1": 110.0}, {}, 1000.0) == 1
    assert mbt.peak_concurrency_replay(recs, {"t1": 150.0}, {}, 1000.0) == 2


def test_files_to_process_excludes_today_and_processed():
    """Today's file is still being written — never processed; processed
    files never re-read; non-matching names ignored."""
    files = ["/x/firehose_20260904.jsonl.gz", "/x/firehose_20260905.jsonl.gz",
             "/x/firehose_20260906.jsonl.gz", "/x/guard.log"]
    got = mbt.files_to_process(files, {"firehose_20260904.jsonl.gz"},
                               "20260906")
    assert got == ["/x/firehose_20260905.jsonl.gz"]
    assert mbt.files_to_process(files, set(), "20260904") == []


def test_should_rescreen():
    """Monday once/day, first run, or forced — nothing else."""
    assert mbt.should_rescreen(True, True, 3, "x", "y")        # forced
    assert mbt.should_rescreen(False, False, 3, "x", "y")      # first run
    assert mbt.should_rescreen(False, True, 0, "", "20260907")  # Monday
    assert not mbt.should_rescreen(False, True, 0, "20260907",
                                   "20260907")                 # already done
    assert not mbt.should_rescreen(False, True, 2, "", "20260908")  # weekday


def test_canon_consumed_not_reimplemented():
    src = inspect.getsource(mbt)
    for name in ("e_value", "per_market_edges", "lcb_edge", "canon_fee"):
        assert f"def {name}(" not in src
