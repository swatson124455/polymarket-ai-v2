"""Pins for CAPITAL-AWARE RANKING TELEMETRY (KALSHI_CAPRANK_TELEMETRY) — task #1, 2026-07-29.

The shadow score is  (capture x calib − fill_cost) / max(commit, floor)  — net $reward per
$committed per day. TELEMETRY-FIRST: nothing here may alter live selection.

  T1 COMMIT MODEL CANNOT DRIFT — est_commit_usd is byte-aligned with the quoter's _capped_join
     sizing at JOIN_SIZE=0, checked BY CALLING BOTH on the same prices.
  T2 UNKNOWN REF IS CHARGED MAX — an unscored market gets the largest plausible denominator.
  T3 CAPITAL EFFICIENCY RANKS  — equal capture, the skewed (cheaper-commitment) book wins.
  T4 FILL COST PENALIZES       — equal else, a bleeding market ranks below a quiet one.
  T5 POOL IS NOT DISCARDED     — with everything else equal, the bigger pool still wins.
  T6 FAILS OPEN                — corrupt feed file / garbage rows never raise, never block.
  T7 SHIPS OFF                 — default flag 0; flag-off writes nothing and changes nothing.
  T8 OBSERVATION ONLY          — flag ON: a file is written, selection is byte-identical, and
     a telemetry fault lands in _SILENT instead of the cycle.
"""
import json
import os

import pytest

import kalshi_capital_rank as kcr
import kalshi_market_scores as ks
from test_live_hardening import q


def _row(t, pool):
    return {"ticker": t, "usd_day": pool}


# ---- T1: the commit estimate and the live sizing are the same function ----

@pytest.mark.parametrize("ref", [0.05, 0.10, 0.13, 0.37, 0.50, 0.63, 0.90, 0.95])
def test_commit_estimate_matches_capped_join_exactly(monkeypatch, ref):
    monkeypatch.setattr(q, "JOIN_SIZE", 0)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 60.0)
    monkeypatch.setattr(q, "INV_HARD_CT", 60.0)
    p_yes, p_no = ref, round(1.0 - ref, 2)
    expected = (q._capped_join(p_yes, p_no) * p_yes) + (q._capped_join(p_no, p_yes) * p_no)
    got = kcr.est_commit_usd(p_yes, 60.0, 60.0)
    assert abs(got - expected) < 1e-9, f"drift at ref={ref}: est {got} vs _capped_join {expected}"


def test_commit_no_hard_cap_and_degenerate_refs():
    # inv_hard 0 -> dollars alone govern (quantized): mirrors _capped_join's `else dollar_ct`
    assert kcr.est_commit_usd(0.50, 60.0, 0) == pytest.approx(60.0)
    # degenerate refs clamp to the venue grid instead of exploding / dividing by zero
    for bad in (0.0, 1.0, -3, 7, "garbage", None):
        v = kcr.est_commit_usd(bad, 60.0, 60.0)
        assert 0 < v <= 120.0


# ---- T2: unknown ref = maximum commitment (conservative denominator) ----

def test_unknown_ref_is_charged_the_max_commitment():
    unknown = kcr.est_commit_usd(None, 60.0, 60.0)
    for ref in (0.05, 0.10, 0.25, 0.75, 0.90):
        assert unknown >= kcr.est_commit_usd(ref, 60.0, 60.0) - 1e-9
    assert unknown == kcr.est_commit_usd(0.5, 60.0, 60.0)


# ---- T3/T4/T5: the ranking itself ----

def test_equal_capture_the_cheaper_commitment_wins():
    """A 0.10/0.90 book is contract-capped on its cheap side, so it commits less than a
    0.50/0.50 book at the same per-market cap — same measured capture => it MUST rank higher."""
    m = {}
    ks.update(m, "SKEWED", 20.0, 0.10, now=1000.0)
    ks.update(m, "MID", 20.0, 0.50, now=1000.0)
    out = kcr.shadow_rank([_row("MID", 500), _row("SKEWED", 500)], m, {}, 60.0, 60.0, now=1000.0)
    assert [d["ticker"] for d in out] == ["SKEWED", "MID"]
    skew = next(d for d in out if d["ticker"] == "SKEWED")
    mid = next(d for d in out if d["ticker"] == "MID")
    assert skew["commit_usd"] < mid["commit_usd"]
    assert skew["base_usd_day"] == mid["base_usd_day"]


def test_fill_cost_penalizes_a_bleeding_market():
    m = {}
    ks.update(m, "BLEEDS", 20.0, 0.50, now=1000.0)
    ks.update(m, "QUIET", 20.0, 0.50, now=1000.0)
    costs = {"BLEEDS": {"cost_usd_day": 5.0}}
    out = kcr.shadow_rank([_row("BLEEDS", 500), _row("QUIET", 500)], m, costs,
                          60.0, 60.0, now=1000.0)
    assert [d["ticker"] for d in out] == ["QUIET", "BLEEDS"]
    assert next(d for d in out if d["ticker"] == "BLEEDS")["cost_usd_day"] == 5.0


def test_pool_is_not_discarded():
    """reward = share x pool. Same ref, same (zero) cost, both unknown -> the base term is the
    pool prior, and the bigger pool must still win. A ranker that buried the pool would be
    wrong, not conservative (same non-negotiable as test_score_rank T1)."""
    out = kcr.shadow_rank([_row("SMALL", 100), _row("BIG", 1000)], {}, {}, 60.0, 60.0, now=1000.0)
    assert [d["ticker"] for d in out] == ["BIG", "SMALL"]


# ---- T6: fails open ----

def test_feed_file_fails_open_on_garbage(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert kcr.load_fill_costs(str(p)) == {}
    p.write_text(json.dumps({"schema": 999, "markets": {"X": {"cost_usd_day": 5}}}))
    assert kcr.load_fill_costs(str(p)) == {}, "wrong schema must fail open, not be trusted"
    assert kcr.load_fill_costs(str(tmp_path / "nope.json")) == {}


def test_garbage_cost_rows_never_raise():
    out = kcr.shadow_rank([_row("A", 100)], {}, {"A": {"cost_usd_day": "wat"}},
                          60.0, 60.0, now=1000.0)
    assert out[0]["cost_usd_day"] == 0.0
    out = kcr.shadow_rank([_row("A", 100)], {}, {"A": "notadict"}, 60.0, 60.0, now=1000.0)
    assert out[0]["cost_usd_day"] == 0.0


# ---- T7: ships off ----

def test_ships_off():
    assert q.CAPRANK_TELEMETRY == 0, "installing this must change nothing until switched on"
    assert q.CAPRANK_CALIB == 1.0, "calibration stays 1.0 until the first real receipt lands"


def test_flag_off_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(q, "CAPRANK_TELEMETRY", 0)
    q._caprank_telemetry([_row("A", 100)], [_row("A", 100)], q.utcnow())
    assert not [f for f in os.listdir(tmp_path) if f.startswith("caprank-")]


# ---- T8: observation only ----

def test_flag_on_logs_and_cannot_alter_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(q, "CAPRANK_TELEMETRY", 1)
    monkeypatch.setattr(q, "_load_fill_costs", lambda: {"B": {"cost_usd_day": 3.0}})
    rows = [_row("A", 1000), _row("B", 100)]
    picked = [rows[0], rows[1]]
    before = json.dumps(picked, sort_keys=True)
    q._caprank_telemetry(rows, picked, q.utcnow())
    assert json.dumps(picked, sort_keys=True) == before, "telemetry must not mutate selection"
    files = [f for f in os.listdir(tmp_path) if f.startswith("caprank-")]
    assert len(files) == 1
    row = json.loads(open(os.path.join(str(tmp_path), files[0])).read().splitlines()[0])
    assert row["actual"] == ["A", "B"], "actual = the picked order, verbatim"
    assert set(row["shadow"]) == {"A", "B"}
    assert row["overlap"] == 2 and row["would_enter"] == [] and row["would_exit"] == []
    comp = {d["ticker"]: d for d in row["components"]}
    assert comp["B"]["cost_usd_day"] == 3.0
    assert comp["A"]["commit_usd"] > 0


def test_selection_is_byte_identical_flag_on_vs_off(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0)      # skip the close-time prefilter (network)
    progs = [{"incentive_type": "liquidity", "market_ticker": f"KXT{i}-26DEC31-T{i}",
              "period_reward": (10 + i) * 10000, "target_size_fp": "50",
              "discount_factor_bps": 5000, "start_date": "2026-07-01T00:00:00Z",
              "end_date": "2099-01-01T00:00:00Z"} for i in range(6)]
    now = q.utcnow()
    monkeypatch.setattr(q, "CAPRANK_TELEMETRY", 0)
    off = [r["ticker"] for r in q.select_footprint(progs, now)]
    monkeypatch.setattr(q, "CAPRANK_TELEMETRY", 1)
    on = [r["ticker"] for r in q.select_footprint(progs, now)]
    assert on == off and len(on) == 6
    assert [f for f in os.listdir(tmp_path) if f.startswith("caprank-")], \
        "flag on must actually log"


def test_telemetry_fault_lands_in_silent_not_the_cycle(monkeypatch, tmp_path):
    import kalshi_capital_rank as mod
    monkeypatch.setattr(q, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(q, "CAPRANK_TELEMETRY", 1)
    monkeypatch.setattr(mod, "shadow_rank",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    base = q._SILENT["caprank_fail"]
    q._caprank_telemetry([_row("A", 100)], [_row("A", 100)], q.utcnow())   # must not raise
    assert q._SILENT["caprank_fail"] == base + 1


# ---- PROSPECTIVE CAPTURE + RISK-AVERSION KNOBS (operator ask 2026-07-29) ----

def test_prospective_beats_pool_prior_and_defaults_are_noop():
    """A market the sweep measured must rank on its MEASURED prospective capture, not its pool
    guess — and with every knob at 1.0 a run WITHOUT sweep data is byte-identical to before."""
    rows = [_row("GUESS", 500), _row("SWEPT", 500)]
    pro = {"SWEPT": {"capture": 40.0, "ref": 0.10}}
    out = kcr.shadow_rank(rows, {}, {}, 60.0, 60.0, now=1000.0, prospective=pro)
    by = {d["ticker"]: d for d in out}
    assert by["SWEPT"]["kind"] == "prospective" and by["SWEPT"]["base_usd_day"] == 40.0
    assert by["SWEPT"]["ref"] == 0.10, "sweep ref feeds the commit estimate too"
    assert by["GUESS"]["kind"] == "unknown"
    # no sweep data + default knobs == the pre-knob behavior, exactly
    old = kcr.shadow_rank(rows, {}, {}, 60.0, 60.0, now=1000.0)
    assert old == kcr.shadow_rank(rows, {}, {}, 60.0, 60.0, now=1000.0, prospective={},
                                  risk_lambda=1.0, prospective_haircut=1.0, unknown_haircut=1.0)


def test_live_measured_score_outranks_sweep_data():
    """A FRESH live measurement is better evidence than an offline hypothetical join — the
    sweep must only fill gaps, never override a 'scored' market."""
    m = {}
    ks.update(m, "LIVE", 20.0, 0.50, now=1000.0)
    pro = {"LIVE": {"capture": 999.0, "ref": 0.50}}
    out = kcr.shadow_rank([_row("LIVE", 500)], m, {}, 60.0, 60.0, now=1000.0, prospective=pro)
    assert out[0]["kind"] == "scored" and out[0]["base_usd_day"] == pytest.approx(20.0)


def test_risk_lambda_punishes_bleeders_harder():
    """lambda=3: a market whose fill cost was survivable at lambda=1 must drop below a smaller
    but clean market."""
    m = {}
    ks.update(m, "BLEEDS", 30.0, 0.50, now=1000.0)
    ks.update(m, "CLEAN", 20.0, 0.50, now=1000.0)
    costs = {"BLEEDS": {"cost_usd_day": 5.0}}
    rows = [_row("BLEEDS", 500), _row("CLEAN", 500)]
    at1 = kcr.shadow_rank(rows, m, costs, 60.0, 60.0, now=1000.0, risk_lambda=1.0)
    assert [d["ticker"] for d in at1] == ["BLEEDS", "CLEAN"], "30-5 > 20 at lambda=1"
    at3 = kcr.shadow_rank(rows, m, costs, 60.0, 60.0, now=1000.0, risk_lambda=3.0)
    assert [d["ticker"] for d in at3] == ["CLEAN", "BLEEDS"], "30-15 < 20 at lambda=3"


def test_haircuts_stop_guesses_outranking_receipts():
    """THE CYCLE-1 FAILURE SHAPE (caprank row 17:28:04Z): stale pool-guesses outranked measured
    markets. unknown_haircut < 1 must invert that when the guess's edge is only nominal."""
    m = {}
    ks.update(m, "MEASURED", 18.0, 0.50, now=1000.0)
    rows = [_row("GUESS", 20), _row("MEASURED", 500)]
    plain = kcr.shadow_rank(rows, m, {}, 60.0, 60.0, now=1000.0)
    assert [d["ticker"] for d in plain] == ["GUESS", "MEASURED"], "pool guess wins at 1.0"
    averse = kcr.shadow_rank(rows, m, {}, 60.0, 60.0, now=1000.0, unknown_haircut=0.5)
    assert [d["ticker"] for d in averse] == ["MEASURED", "GUESS"]
    # prospective_haircut discounts hypothetical joins the same way
    pro = {"P": {"capture": 20.0, "ref": 0.50}}
    cut = kcr.shadow_rank([_row("P", 500)], {}, {}, 60.0, 60.0, now=1000.0,
                          prospective=pro, prospective_haircut=0.5)
    assert cut[0]["base_usd_day"] == pytest.approx(10.0)
    # STALE entries are guesses too (the actual cycle-1 kind) — the haircut must hit them
    m2 = {}
    ks.update(m2, "STALEG", 50.0, 0.50, now=0.0)
    outs = kcr.shadow_rank([_row("STALEG", 300)], m2, {}, 60.0, 60.0,
                           now=ks.STALE_S + 1, unknown_haircut=0.5)
    assert outs[0]["kind"] == "stale"
    assert outs[0]["base_usd_day"] == pytest.approx(150.0), "pool prior 300 x 0.5"


def test_prospective_file_and_rows_fail_open(tmp_path):
    p = tmp_path / "p.json"
    p.write_text("{not json")
    assert kcr.load_prospective(str(p)) == {}
    p.write_text(json.dumps({"schema": 999, "markets": {"X": {"capture": 5}}}))
    assert kcr.load_prospective(str(p)) == {}
    out = kcr.shadow_rank([_row("A", 100)], {}, {}, 60.0, 60.0, now=1000.0,
                          prospective={"A": {"capture": "wat"}})
    assert out[0]["kind"] == "unknown", "malformed sweep row -> pool prior, never a crash"


def test_new_knobs_ship_at_noop_defaults():
    assert q.CAPRANK_RISK_LAMBDA == 1.0
    assert q.CAPRANK_PROSPECTIVE_HAIRCUT == 1.0
    assert q.CAPRANK_UNKNOWN_HAIRCUT == 1.0


def test_env_knobs_actually_reach_the_shadow(monkeypatch, tmp_path):
    """WIRING PIN: the KALSHI_CAPRANK_* knobs must flow from the quoter into shadow_rank —
    a hardcoded 1.0 at the call site (knob silently disconnected) must fail here."""
    import kalshi_market_scores as kms
    m = {}
    kms.update(m, "BLEEDS", 30.0, 0.50, now=q.utcnow().timestamp())
    kms.update(m, "CLEAN", 20.0, 0.50, now=q.utcnow().timestamp())
    monkeypatch.setattr(q, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(q, "CAPRANK_TELEMETRY", 1)
    monkeypatch.setattr(q, "SCORES", m)
    monkeypatch.setattr(q, "_load_fill_costs", lambda: {"BLEEDS": {"cost_usd_day": 5.0}})
    monkeypatch.setattr(q, "_load_prospective", lambda: {})
    monkeypatch.setattr(q, "CAPRANK_RISK_LAMBDA", 3.0)
    rows = [_row("BLEEDS", 500), _row("CLEAN", 500)]
    q._caprank_telemetry(rows, rows, q.utcnow())
    f = [x for x in os.listdir(tmp_path) if x.startswith("caprank-")][0]
    logged = json.loads(open(os.path.join(str(tmp_path), f)).read().splitlines()[-1])
    assert logged["shadow"][0] == "CLEAN", "lambda=3 must flip the order INSIDE the quoter"
    # and a haircut knob must reach it too: unknown pool-guess discounted in the logged row
    monkeypatch.setattr(q, "CAPRANK_UNKNOWN_HAIRCUT", 0.5)
    rows2 = [_row("GUESS", 100)]
    q._caprank_telemetry(rows2, rows2, q.utcnow())
    logged2 = json.loads(open(os.path.join(str(tmp_path), f)).read().splitlines()[-1])
    assert logged2["components"][0]["base_usd_day"] == pytest.approx(50.0), \
        "unknown_haircut must reach the logged base"
    # and the sweep feed must reach it: a swept market logs kind=prospective, not a pool guess
    monkeypatch.setattr(q, "_load_prospective",
                        lambda: {"SWEPT": {"capture": 40.0, "ref": 0.10}})
    rows3 = [_row("SWEPT", 100)]
    q._caprank_telemetry(rows3, rows3, q.utcnow())
    logged3 = json.loads(open(os.path.join(str(tmp_path), f)).read().splitlines()[-1])
    assert logged3["components"][0]["kind"] == "prospective", \
        "the offline sweep feed must reach the logged shadow"


def test_sweep_capture_matches_live_telemetry_math_exactly():
    """ZERO-DRIFT PIN: the sweep's prospective capture on a book must equal what
    _market_telemetry_row computes for the same book with the same join — because the sweep
    literally delegates to it. If someone re-implements the math, this breaks."""
    import kalshi_capture_sweep as sw
    yes_raw = [["0.40", "80"], ["0.39", "50"]]
    no_raw = [["0.58", "70"], ["0.57", "30"]]
    row = {"ticker": "T-X", "usd_day": 100.0, "target": 50.0, "df": 0.5}
    cap, ref = sw.prospective_capture(q, row, yes_raw, no_raw, q.utcnow())
    (yl, _), (nl, _) = q._levels(yes_raw), q._levels(no_raw)
    quotes = [{"side": "yes", "price_dollars": 0.40, "count": q._capped_join(0.40, 0.58)},
              {"side": "no", "price_dollars": 0.58, "count": q._capped_join(0.58, 0.40)}]
    trow = q._market_telemetry_row(0, q.utcnow(), row, yl, nl, quotes, None, 0.0, None)
    assert cap == pytest.approx(float(trow["capture_usd_day"])) and cap > 0
    assert ref == trow["y_ref"] == 0.40
    # one-sided book: R3 pays nobody -> capture must be 0, not an exception
    assert sw.prospective_capture(q, row, yes_raw, [], q.utcnow())[0] == 0.0


def test_sweep_candidate_rows_r1_canon():
    """period_reward/10000 IS the daily pool (R1 canon) — and non-liquidity/null rows drop."""
    import kalshi_capture_sweep as sw
    progs = [{"incentive_type": "liquidity", "market_ticker": "A", "period_reward": 2000000,
              "target_size_fp": "50", "discount_factor_bps": 5000},
             {"incentive_type": "liquidity", "market_ticker": "B", "period_reward": 1000000,
              "target_size_fp": "50", "discount_factor_bps": 5000},
             {"incentive_type": "volume", "market_ticker": "V", "period_reward": 9990000,
              "target_size_fp": "50", "discount_factor_bps": 5000},
             {"incentive_type": "liquidity", "market_ticker": "N", "period_reward": 9990000,
              "target_size_fp": None, "discount_factor_bps": 5000}]
    rows = sw.candidate_rows(progs, 10)
    assert [r["ticker"] for r in rows] == ["A", "B"]
    assert rows[0]["usd_day"] == 200.0


# ---- the feed builder (kalshi_fill_costs.build is pure) ----

def test_fill_cost_feed_builder():
    import kalshi_fill_costs as kfc
    positions = [
        {"ticker": "LOSER", "realized_pnl_dollars": "-4.00", "fees_paid_dollars": "0.50"},
        {"ticker": "WINNER", "realized_pnl_dollars": "2.00", "fees_paid_dollars": "0.10"},
        {"ticker": "BLIP", "realized_pnl_dollars": "-1.00", "fees_paid_dollars": "0.00"},
    ]
    fills = [
        {"ticker": "LOSER", "created_time": "2026-07-27T00:00:00Z"},
        {"ticker": "LOSER", "created_time": "2026-07-29T00:00:00Z"},   # 2 active days
        {"ticker": "BLIP", "created_time": "2026-07-29T10:00:00Z"},    # one touch -> floor 1d
    ]
    m = kfc.build(positions, fills)
    assert m["LOSER"]["cost_usd_day"] == pytest.approx(2.0)     # $4 over 2 days
    assert m["WINNER"]["cost_usd_day"] == 0.0, "fill profits must not become a rank bonus"
    assert m["BLIP"]["cost_usd_day"] == pytest.approx(1.0), "active_days floors at 1"
    assert m["LOSER"]["fees_usd"] == pytest.approx(0.50)
    # and the file it writes is readable by the ranking side (schema agreement)
    assert kfc.SCHEMA == kcr.SCHEMA
