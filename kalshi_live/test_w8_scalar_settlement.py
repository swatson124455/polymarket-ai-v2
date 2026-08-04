"""W8 — settlement_payout must trust the venue's own `revenue` (B6 root fix).

FAILING-BEFORE PIN: the exact live row that exposed the defect (the only disagreement in the
complete n=147 settlement history, snapshot cash_identity_snapshot_2026-08-03T233338Z.json):
KXCLUBFBTTS-26JUL26ERKHIL-BTTS settled market_result="scalar" with yes_count_fp=19.00 /
no_count_fp=18.86 / value=45 and the venue paid revenue=6 cents. The binary net*value
reconstruction returns 0.0630; the venue paid 0.0600. The recorder must report what the venue
paid — kalshi_attribution_ledger.settlement_revenue and kalshi_netev_rebuild already do.
"""
import kalshi_cash_recorder as cr


def test_scalar_settlement_uses_venue_revenue_not_net_times_value():
    s = {"ticker": "KXCLUBFBTTS-26JUL26ERKHIL-BTTS", "market_result": "scalar",
         "yes_count_fp": "19.00", "no_count_fp": "18.86", "value": 45, "revenue": 6}
    assert cr.settlement_payout(s) == 0.06


def test_binary_settlement_agrees_with_revenue_field():
    # On binaries the venue's revenue and the old reconstruction agree (146 of 147 rows);
    # the fix must not change the binary answer.
    s = {"ticker": "T-BIN", "yes_count_fp": "50.00", "no_count_fp": "0.00",
         "value": 100, "revenue": 5000}
    assert cr.settlement_payout(s) == 50.0


def test_missing_revenue_fires_the_rename_alarm_and_pays_zero():
    # The 2026-07-30 field-rename alarm now guards the load-bearing field, which is `revenue`.
    before = cr.MISSING_VALUE_FIELDS[0]
    p = cr.settlement_payout({"ticker": "T", "yes_count_fp": 1, "no_count_fp": 0})
    assert p == 0.0
    assert cr.MISSING_VALUE_FIELDS[0] == before + 1
