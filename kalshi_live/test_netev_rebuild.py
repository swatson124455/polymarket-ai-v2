"""NET-EV TABLE REBUILD FROM RECEIPTS (C3, 2026-08-03).

Builds the same table shape as the CSV calibrator from pure venue reads, closing its two data
gaps: per-family credit attribution was operator-SCREENSHOT derived (CSV credit rows carry an
empty market_ticker, canon §M11) and per-trade realized P&L "had no direct API substitute".
credit_history carries the EVENT in its reason string, and the position-aware fill model plus
settlement revenue reproduces realized P&L — validated against the plan's own proof criterion:
KXAAAGASD-26JUL21 computes to -$5.2676 against the canon -$5.27, to the cent (36 fills, 5
markets; API read 2026-08-03T15:00:40Z).

THE WINDOW IS A POLICY CHOICE AND HAS NO DEFAULT. Most of this account's realized losses were
AGENT DEFECTS rather than family economics, so a window over the defect era would encode our
own bugs as a permanent verdict on a family. build_table REFUSES to run without one.
"""
import datetime as dt

import pytest

import kalshi_netev_rebuild as nr


def _fam(t):
    if not t:
        return None
    if t.startswith("KXAAAGAS"):
        return "gas"
    if t.startswith("KXTEMP"):
        return "temp"
    return None


def _fill(ticker, outcome, price, count, fee=0.0, ts="2026-07-21T12:00:00Z", fid="f1"):
    yes_px = price if outcome == "yes" else round(1.0 - price, 4)
    return {"ticker": ticker, "market_ticker": ticker, "outcome_side": outcome,
            "side": outcome, "book_side": "bid" if outcome == "yes" else "ask",
            "action": "buy" if outcome == "yes" else "sell",
            "count_fp": f"{count:.2f}", "yes_price_dollars": f"{yes_px:.4f}",
            "no_price_dollars": f"{1.0 - yes_px:.4f}", "fee_cost": f"{fee:.6f}",
            "fill_id": fid, "trade_id": fid, "created_time": ts, "is_taker": False}


def _credit(event, cents, ts="2026-07-22T06:00:00Z"):
    return {"amount_cents": cents, "created_at": ts, "type": "incentive", "status": "applied",
            "reason": f"Liquidity Incentive for event {event}"}


def _settle(ticker, revenue_cents=0, ts="2026-07-21T20:00:00Z"):
    return {"ticker": ticker, "revenue": revenue_cents, "settled_time": ts}


WIN = (dt.datetime(2026, 7, 21, tzinfo=dt.timezone.utc),
       dt.datetime(2026, 7, 23, tzinfo=dt.timezone.utc))


def test_window_is_required():
    with pytest.raises(ValueError):
        nr.build_table([], [], [], _fam, None)


def test_date_only_window_bounds_are_utc_aware_not_naive():
    """REGRESSION (2026-08-03): the CLI raised TypeError on its own documented example.

    main() builds the window with _iso(--since); "2026-07-21" parsed NAIVE, and _in_window
    compared it against tz-aware created_time. Every test passed a tz-aware tuple straight to
    build_table, so this path had no coverage at all. Fails before the fix, passes after.
    """
    lo, hi = nr._iso("2026-07-21"), nr._iso("2026-07-23")
    assert lo.tzinfo is not None and hi.tzinfo is not None
    assert nr._in_window("2026-07-21T12:00:00Z", (lo, hi)) is True
    assert nr._in_window("2026-07-20T12:00:00Z", (lo, hi)) is False
    assert nr._in_window("2026-07-24T12:00:00Z", (lo, hi)) is False


def test_an_offset_bearing_timestamp_keeps_its_own_offset():
    """The UTC default must apply ONLY to naive values — never override a real offset.
    2026-07-21T01:36:39-04:00 is 05:36:39Z, which is INSIDE a window starting 07-21T00:00Z."""
    assert nr._iso("2026-07-21T01:36:39-04:00") == nr._iso("2026-07-21T05:36:39Z")
    assert nr._in_window("2026-07-20T22:00:00-04:00", (nr._iso("2026-07-21"), nr._iso("2026-07-23")))


def test_build_table_accepts_a_window_built_the_way_the_cli_builds_it():
    """End-to-end: the exact main() construction must reach a verdict, not raise."""
    win = (nr._iso("2026-07-21"), nr._iso("2026-07-23"))
    doc = nr.build_table([_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50)],
                         [_settle("KXAAAGASD-26JUL21-4.02", 0)],
                         [_credit("KXAAAGASD-26JUL21", 2500)], _fam, win)
    assert doc["families"]["gas"]["trading_pnl"] == -20.0


def test_credit_event_is_parsed_from_the_reason_string():
    assert nr.credit_event(_credit("KXAAAGASD-26JUL21", 215)) == "KXAAAGASD-26JUL21"
    assert nr.credit_event({"reason": "Referral bonus"}) is None
    assert nr.credit_event({}) is None


def test_family_net_combines_credits_and_trading():
    # 50 ct of gas yes at $0.40 = -$20.00 cash, settles worthless, $25.00 of credits.
    doc = nr.build_table([_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50)],
                         [_settle("KXAAAGASD-26JUL21-4.02", 0)],
                         [_credit("KXAAAGASD-26JUL21", 2500)], _fam, WIN)
    g = doc["families"]["gas"]
    assert g["trading_pnl"] == -20.0
    assert g["credits"] == 25.0
    assert g["net"] == 5.0
    assert g["notional"] == 20.0
    assert g["net_pct_notional"] == 0.25
    # 2026-08-03 — THIS ASSERTION USED TO READ "receipt" AND IT WAS ASSERTING THE DEFECT.
    # One fill is not a receipt-grade sample. The arithmetic above is what this test is for;
    # the grade belongs to test_receipt_grade_requires_measured_trading below.
    assert g["confidence"] == "unproven"


def test_a_family_with_no_credits_is_unproven_not_a_verdict():
    # UNPROVEN routes to the quoter's model fallback; it must never read as "calibrated bad".
    doc = nr.build_table([_fill("KXTEMPCHIH-26JUL21-T69", "yes", 0.40, 50)], [], [], _fam, WIN)
    assert doc["families"]["temp"]["confidence"] == "unproven"


def test_unattributable_credits_are_recorded_not_smeared(monkeypatch):
    # The referral credit belongs to no family. It must not inflate a family's net.
    doc = nr.build_table([_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50)], [],
                         [_credit("KXAAAGASD-26JUL21", 100),
                          {"amount_cents": 1500, "created_at": "2026-07-22T06:00:00Z",
                           "reason": "Referral bonus"}], _fam, WIN)
    assert doc["families"]["gas"]["credits"] == 1.0
    assert doc["credits_unattributed"] == 15.0


def test_out_of_window_rows_are_excluded_on_every_leg():
    out = "2026-07-30T12:00:00Z"
    doc = nr.build_table([_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50, ts=out)],
                         [_settle("KXAAAGASD-26JUL21-4.02", 500, ts=out)],
                         [_credit("KXAAAGASD-26JUL21", 2500, ts=out)], _fam, WIN)
    assert doc["families"] == {}, "nothing in-window -> no family verdicts at all"


def test_settlement_revenue_lands_in_trading_pnl():
    # 50 ct at $0.40 = -$20.00; pays $1/ct = +$50.00 -> +$30.00.
    doc = nr.build_table([_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50)],
                         [_settle("KXAAAGASD-26JUL21-4.02", 5000)], [], _fam, WIN)
    assert doc["families"]["gas"]["trading_pnl"] == 30.0


def test_fees_reduce_the_net():
    doc = nr.build_table([_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50, fee=1.25)],
                         [], [_credit("KXAAAGASD-26JUL21", 2500)], _fam, WIN)
    assert doc["families"]["gas"]["fees"] == 1.25
    assert doc["families"]["gas"]["net"] == 25.0 - 20.0 - 1.25


def test_paired_position_nets_to_the_price_improvement():
    tape = [_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 50, ts="2026-07-21T12:00:00Z", fid="a"),
            _fill("KXAAAGASD-26JUL21-4.02", "no", 0.55, 50, ts="2026-07-21T13:00:00Z", fid="b")]
    doc = nr.build_table(tape, [], [], _fam, WIN)
    assert round(doc["families"]["gas"]["trading_pnl"], 6) == 2.5


def test_zero_notional_reports_none_not_a_division():
    doc = nr.build_table([], [], [_credit("KXAAAGASD-26JUL21", 500)], _fam, WIN)
    assert doc["families"]["gas"]["net_pct_notional"] is None


def test_credits_alone_never_earn_a_receipt_verdict():
    """DEFECT C REGRESSION (2026-08-03). Credits with ZERO trading used to grade 'receipt'.

    That row carries net_pct_notional=None, and the armed gate's test is
    `poor = net_pct is not None and net_pct < MIN` — None can never be poor, so the family
    opened FULL TWO-SIDED while a genuinely unknown ('unproven') family was skipped. No data
    ranked ABOVE unknown data. Verified by executing the real gate, not by reading it.
    """
    doc = nr.build_table([], [], [_credit("KXAAAGASD-26JUL21", 500)], _fam, WIN)
    g = doc["families"]["gas"]
    assert g["credits"] == 5.0
    assert g["net_pct_notional"] is None
    assert g["confidence"] == "unproven", "credits alone must route to the model fallback"
    assert "thin" in g["evidence"]


def test_receipt_grade_requires_measured_trading():
    """At/above the bar with credits -> receipt. One fill below it -> unproven."""
    tape = [_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 1, fid=f"f{i}")
            for i in range(nr.MIN_RECEIPT_FILLS)]
    doc = nr.build_table(tape, [], [_credit("KXAAAGASD-26JUL21", 2500)], _fam, WIN)
    g = doc["families"]["gas"]
    assert g["n_fills"] == nr.MIN_RECEIPT_FILLS
    assert g["confidence"] == "receipt"
    assert "thin" not in g["evidence"]

    doc2 = nr.build_table(tape[:-1], [], [_credit("KXAAAGASD-26JUL21", 2500)], _fam, WIN)
    assert doc2["families"]["gas"]["confidence"] == "unproven", "one fill below the bar"


def test_the_receipt_bar_is_tunable_without_editing_code():
    tape = [_fill("KXAAAGASD-26JUL21-4.02", "yes", 0.40, 1, fid=f"f{i}") for i in range(3)]
    creds = [_credit("KXAAAGASD-26JUL21", 2500)]
    assert nr.build_table(tape, [], creds, _fam, WIN,
                          min_receipt_fills=3)["families"]["gas"]["confidence"] == "receipt"
    assert nr.build_table(tape, [], creds, _fam, WIN,
                          min_receipt_fills=4)["families"]["gas"]["confidence"] == "unproven"


def test_the_document_carries_its_window_and_caveats():
    doc = nr.build_table([], [], [], _fam, WIN)
    assert doc["window"][0].startswith("2026-07-21")
    assert any("OPERATOR CHOICE" in c for c in doc["caveats"]), \
        "the window caveat must travel with the table, not live only in a commit message"
