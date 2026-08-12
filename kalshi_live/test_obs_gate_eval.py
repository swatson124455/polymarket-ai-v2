"""obs_hold_gate_eval — pins for the pure gate arithmetic (Pre-registration 2, §10)."""
import obs_hold_gate_eval as ge


def row(ev, paid, floor, acc):
    return {"event": ev, "paid_usd": paid, "any_floor_within_fresh": floor,
            "accrued_at_conclusion": acc}


def test_unpowered_below_n():
    rows = [row(f"E{i}", 2.0, True, 2.0) for i in range(9)]
    assert ge.evaluate(rows)["verdict"] == "UNPOWERED"


def test_unpowered_below_payers():
    rows = [row(f"E{i}", 0.0, False, 0.3) for i in range(11)] + [row("P1", 2.0, True, 2.0)]
    assert ge.evaluate(rows)["verdict"] == "UNPOWERED"      # only 1 payer


def test_pass_clean():
    rows = ([row(f"P{i}", 2.0, True, 2.1) for i in range(3)]
            + [row(f"D{i}", 0.0, False, 0.2) for i in range(8)])
    out = ge.evaluate(rows)
    assert out["verdict"] == "PASS" and out["false_bench_rate"] == 0.0


def test_false_bench_fails():
    """A paid event whose strikes never crossed the floor while fresh = the hold HURT a
    payer. One of two payers benched = 50% >> 10% -> FAIL."""
    rows = ([row("P1", 2.0, True, 2.1), row("P2", 3.0, False, 3.0)]
            + [row(f"D{i}", 0.0, False, 0.2) for i in range(9)])
    out = ge.evaluate(rows)
    assert out["verdict"] == "FAIL"
    assert out["false_bench_events"] == ["P2"]


def test_fidelity_fail():
    """The KXAPRPOTUS shape: paid < 0.5 x accrued-at-conclusion -> sensor over-predicts,
    the floor constant cannot be trusted -> FAIL even with zero false-bench."""
    rows = ([row("P1", 2.0, True, 2.1), row("P2", 1.63, True, 3.3881)]
            + [row(f"D{i}", 0.0, False, 0.2) for i in range(9)])
    out = ge.evaluate(rows)
    assert out["verdict"] == "FAIL"
    assert out["fidelity_fail_events"] == ["P2"]


def test_pending_events_never_in_rows_is_callers_contract():
    """The F4 lesson lives in main() (concluded-only assembly); evaluate() trusts its rows.
    Documented here so the contract is pinned in prose."""
    assert ge.evaluate([])["verdict"] == "UNPOWERED"
