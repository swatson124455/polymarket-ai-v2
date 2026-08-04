"""SETTLEMENT PAYOUT IS GROSS OF FEES (defect 8 root fix, operator-named 2026-08-02).

A settlement's `fee_cost` is a REPORTING ROLL-UP of fees already charged on that market's fills.
fill_cash() subtracts them once; settlement_payout() used to subtract them AGAIN, so
cum_settle_payout fell over time (8 decreases / 0 increases observed 2026-08-02) even though a
payout is >= 0 by construction and the cumulative sum must therefore be monotone non-decreasing.

Measured against the live account 2026-08-02T21:27:34Z over the complete history
(n=1,233 fills / 127 settlements): settlement.fee_cost == SUM(fills.fee_cost) on the same ticker
for 127 of 127 to the cent; 0 settlements carry a fee with no fills on that ticker; the
double-count was $35.5619 lifetime and made 56 of 127 settlements contribute negative.

These pins keep the fee term from being reintroduced.
"""
import kalshi_cash_recorder as cr


def _sett(yes=0, no=0, value=100, fee=0.0, ticker="T", revenue=0):
    # `revenue` is the venue's own payout in cents — since the W8 fix it is THE source;
    # yes/no/value stay in the fixture because the pins document the scenarios they encode.
    return {"ticker": ticker, "yes_count_fp": yes, "no_count_fp": no,
            "value": value, "fee_cost": fee, "revenue": revenue}


def test_winning_yes_payout_ignores_the_fee_rollup():
    # 50 yes @ value 100c settles at $1.00/ct = $50.00 GROSS. A $2.50 roll-up must not reduce it:
    # those dollars were already subtracted by fill_cash() when the fills printed.
    assert cr.settlement_payout(_sett(yes=50, value=100, fee=2.50, revenue=5000)) == 50.0


def test_losing_side_payout_is_zero_not_negative():
    # THE DEFECT'S SIGNATURE: a worthless position paid 0 but was booked at -fee, which is what
    # dragged the cumulative sum down. Live worst case 2026-08-02: KXMUSKNW-26JUL31-T700
    # pay=0.0000 fee=8.1987 -> booked -8.1987.
    assert cr.settlement_payout(_sett(yes=50, value=0, fee=8.1987, revenue=0)) == 0.0


def test_net_no_position_pays_the_complement_gross():
    # net = -20 -> payout = 20 x (1 - 0.35) = 13.00, fee roll-up irrelevant.
    assert cr.settlement_payout(_sett(no=20, value=35, fee=1.75, revenue=1300)) == 13.0


def test_payout_is_never_negative_for_any_fee_size():
    # The monotonicity guarantee: cum_settle_payout is a sum of these, so none may be < 0.
    for fee in (0.0, 0.01, 1.0, 8.1987, 999.0):
        for yes, no, value in ((10, 0, 0), (0, 10, 100), (7, 7, 50), (0, 0, 50)):
            p = cr.settlement_payout(_sett(yes=yes, no=no, value=value, fee=fee))
            assert p >= 0.0, f"payout {p} < 0 at fee={fee} yes={yes} no={no} value={value}"


def test_cumulative_payout_is_monotone_across_a_settlement_stream():
    # Replays the live shape: mostly-worthless settlements carrying real fee roll-ups. Under the
    # old code this sequence walked the cumulative DOWN; it must now never decrease.
    stream = [_sett(yes=50, value=0, fee=8.1987, revenue=0),
              _sett(yes=30, value=0, fee=2.2269, revenue=0),
              _sett(no=25, value=100, fee=2.0448, revenue=0),
              _sett(yes=40, value=100, fee=1.4760, revenue=4000)]
    cum, prev = 0.0, 0.0
    for s in stream:
        cum += cr.settlement_payout(s)
        assert cum >= prev, f"cum_settle_payout decreased: {prev} -> {cum}"
        prev = cum
    assert cum == 40.0        # only the last settlement was in the money


def test_missing_value_probe_still_fires():
    # The 2026-07-30 audit probe must survive the fee-term removal (venue field-rename alarm).
    before = cr.MISSING_VALUE_FIELDS[0]
    cr.settlement_payout({"ticker": "T", "yes_count_fp": 1, "no_count_fp": 0})
    assert cr.MISSING_VALUE_FIELDS[0] == before + 1
