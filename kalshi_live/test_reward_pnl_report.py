"""reward_pnl_report — pins for the F gauge's pure core.

The one non-negotiable (self-review F4, 2026-08-11): PENDING vs LEAKAGE turns ONLY on the
program clock — a concluded-but-inside-envelope event must NEVER read as leakage, and a
not-yet-concluded event must NEVER read as leakage no matter how long we've watched it.
"""
import datetime

import reward_pnl_report as rp

NOW = datetime.datetime(2026, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)
MAP = {"p1": {"market_ticker": "KXA-26AUG10-T1", "end_date": "2026-08-10T14:00:00Z"},
       "p2": {"market_ticker": "KXA-26AUG10-T2", "end_date": "2026-08-10T14:00:00Z"},
       "p3": {"market_ticker": "KXB-26AUG15-H1", "end_date": "2026-08-15T14:00:00Z"},
       "p4": {"market_ticker": "KXC-26AUG11-T1", "end_date": "2026-08-11T20:00:00Z"}}


def snap(rows):
    return {"estimates": [{"program_id": p, "reward_centicents": cc} for p, cc in rows]}


def test_credit_reason_parse_and_unparsed_bucket():
    out = rp.credits_by_event([
        {"reason": "Liquidity Incentive for event KXA-26AUG10", "amount_cents": 163},
        {"reason": "Promo credit", "amount_cents": 1500}])
    assert out["KXA-26AUG10"]["paid"] == 1.63
    assert out["?"]["paid"] == 15.0            # counted, never attributed to a series


def test_accrued_join_sums_strikes_and_flags_unmapped():
    acc = rp.accrued_by_event(snap([("p1", 10000), ("p2", 5000), ("zzz", 777)]), MAP)
    assert acc["KXA-26AUG10"]["accrued"] == 1.5
    assert acc["?"]["accrued"] == 0.0777       # map gap visible, not silent


def test_pending_inside_envelope_is_not_leakage():
    """Concluded 2026-08-10T14:00Z, now 08-12T00:00Z = 34h past < 48h envelope -> PENDING."""
    acc = rp.accrued_by_event(snap([("p1", 20000)]), MAP)
    status, a, p = rp.classify("KXA-26AUG10", acc["KXA-26AUG10"], None, NOW)
    assert status == "PENDING"


def test_leakage_only_past_envelope():
    late = NOW + datetime.timedelta(hours=15)          # 49h past the 08-10T14:00Z end
    acc = rp.accrued_by_event(snap([("p1", 20000)]), MAP)
    status, a, p = rp.classify("KXA-26AUG10", acc["KXA-26AUG10"], None, late)
    assert status == "LEAKAGE"


def test_running_program_is_earning_never_leakage():
    acc = rp.accrued_by_event(snap([("p3", 20000)]), MAP)
    status, a, p = rp.classify("KXB-26AUG15", acc["KXB-26AUG15"], None, NOW)
    assert status == "EARNING"


def test_paid_partial_flags_overprediction():
    """The KXAPRPOTUS shape (self-review F3): accrued $3.3881, paid $1.63 -> PARTIAL."""
    acc = rp.accrued_by_event(snap([("p4", 33881)]), MAP)
    status, a, p = rp.classify("KXC-26AUG11", acc["KXC-26AUG11"],
                               {"paid": 1.63, "n": 1, "last": None}, NOW)
    assert status == "PAID_PARTIAL"


def test_paid_to_the_cent_is_clean_paid():
    acc = rp.accrued_by_event(snap([("p4", 24691)]), MAP)
    status, a, p = rp.classify("KXC-26AUG11", acc["KXC-26AUG11"],
                               {"paid": 2.46, "n": 2, "last": None}, NOW)
    assert status == "PAID"


def test_subfloor_accrual_is_dust_not_leakage():
    late = NOW + datetime.timedelta(hours=15)
    acc = rp.accrued_by_event(snap([("p1", 3550)]), MAP)   # $0.355 < $0.50 floor
    status, a, p = rp.classify("KXA-26AUG10", acc["KXA-26AUG10"], None, late)
    assert status == "DUST"


def test_build_report_totals():
    rep = rp.build_report(snap([("p1", 20000), ("p3", 10000)]), MAP,
                          [{"reason": "Liquidity Incentive for event KXC-26AUG11",
                            "amount_cents": 246, "created_at": "2026-08-10T18:25:53Z"}], NOW)
    assert rep["totals"]["accrued_open"] == 3.0          # PENDING 2.0 + EARNING 1.0
    assert rep["totals"]["paid_lifetime"] == 2.46
    assert rep["totals"]["n_leakage"] == 0
