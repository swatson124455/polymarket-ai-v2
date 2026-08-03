"""THE FILL-COST FEED PRICES SETTLED MARKETS (defect 14 root fix, 2026-08-03).

The feed was built from /portfolio/positions alone, and that endpoint NEVER returns a SETTLED
market — measured read-only 2026-08-03T12:39:55Z against the live account: 0 of 129 settled
tickers appear under unfiltered, count_filter=total_traded OR count_filter=position. So a cost
feed meant to price completed round trips was blind to every completed round trip, and it is
the feed the net-EV calibration is built on.

A settled market's realized P&L is recoverable without that endpoint: settlement revenue plus
the position-aware cash of its own fills. `revenue` is CENTS (127/127, measured
2026-08-03T02:30:57Z); the fill model is kalshi_attribution_ledger.replay_fills, the same
validated model the cash recorder uses.

NOT used, and pinned as not used: the settlement row's yes/no_total_cost_dollars are GROSS
LIFETIME costs on BOTH legs and would manufacture enormous phantom losses.
"""
import kalshi_fill_costs as fc


def _fill(ticker, outcome, price, count, fee=0.0, ts="2026-08-01T00:00:00.000000Z", fid="f1"):
    """A live-shaped fill row: acquiring YES prints buy/bid, acquiring NO prints sell/ask."""
    yes_px = price if outcome == "yes" else round(1.0 - price, 4)
    return {"ticker": ticker, "market_ticker": ticker, "outcome_side": outcome,
            "side": outcome, "book_side": "bid" if outcome == "yes" else "ask",
            "action": "buy" if outcome == "yes" else "sell",
            "count_fp": f"{count:.2f}", "yes_price_dollars": f"{yes_px:.4f}",
            "no_price_dollars": f"{1.0 - yes_px:.4f}", "fee_cost": f"{fee:.6f}",
            "fill_id": fid, "trade_id": fid, "created_time": ts, "is_taker": False}


def _settle(ticker, revenue_cents=0):
    return {"ticker": ticker, "revenue": revenue_cents,
            "settled_time": "2026-08-02T00:00:00.000000Z", "market_result": "no",
            "yes_count_fp": "50.00", "no_count_fp": "0.00",
            # GROSS lifetime costs on both legs — deliberately present so a regression that
            # starts using them is caught by the value assertions below.
            "yes_total_cost_dollars": "999.00", "no_total_cost_dollars": "999.00", "value": 0}


def test_settled_market_is_absent_without_settlements():
    # The 3-arg signature is unchanged and reproduces the old (blind) output exactly.
    out = fc.build([], [_fill("T", "yes", 0.40, 50)])
    assert out == {}, "pre-fix behaviour must be reproducible by omitting settlements"


def test_settled_loser_is_priced_from_receipt_plus_fills():
    # Bought 50 yes at $0.40 = -$20.00 of cash; settles worthless -> realized -$20.00.
    out = fc.build([], [_fill("T", "yes", 0.40, 50)], settlements=[_settle("T", 0)])
    assert "T" in out, "a settled market must appear in the cost feed"
    assert out["T"]["realized_pnl_usd"] == -20.0
    assert out["T"]["cost_usd_day"] == 20.0     # active_days floors at 1
    assert out["T"]["settled"] == 1


def test_settled_winner_costs_nothing():
    # Bought 50 yes at $0.40 (-$20.00), pays $1/ct = $50.00 -> +$30.00. A winner is cost 0,
    # never a bonus — the module's stated rule.
    out = fc.build([], [_fill("T", "yes", 0.40, 50)], settlements=[_settle("T", 5000)])
    assert out["T"]["realized_pnl_usd"] == 30.0
    assert out["T"]["cost_usd_day"] == 0.0


def test_gross_settlement_cost_fields_are_never_used():
    # Both gross fields are $999.00 in the fixture. If a regression subtracts them the realized
    # number moves by three orders of magnitude; -$20.00 proves they were ignored.
    out = fc.build([], [_fill("T", "yes", 0.40, 50)], settlements=[_settle("T", 0)])
    assert out["T"]["realized_pnl_usd"] == -20.0


def test_a_live_market_is_never_shadowed_by_a_settlement_row():
    live = [{"ticker": "T", "realized_pnl_dollars": "-1.00", "fees_paid_dollars": "0.05"}]
    out = fc.build(live, [_fill("T", "yes", 0.40, 50)], settlements=[_settle("T", 0)])
    assert out["T"]["realized_pnl_usd"] == -1.0, "the live row wins"
    assert "settled" not in out["T"]


def test_paired_position_settles_to_its_true_pnl():
    # 50 yes at $0.40 (-$20.00) and 50 no at $0.55 (-$27.50) on the SAME ticker: the second
    # fill offsets the first, so the venue releases $1/ct -> +(1-0.55)*50 = +$22.50.
    # Net cash -$20.00 + $22.50 = +$2.50, nothing left to settle, revenue 0.
    tape = [_fill("T", "yes", 0.40, 50, ts="2026-08-01T00:00:00.000000Z", fid="a"),
            _fill("T", "no", 0.55, 50, ts="2026-08-01T01:00:00.000000Z", fid="b")]
    out = fc.build([], tape, settlements=[_settle("T", 0)])
    assert round(out["T"]["realized_pnl_usd"], 6) == 2.5
    assert out["T"]["cost_usd_day"] == 0.0


def test_fees_are_summed_for_a_settled_market():
    out = fc.build([], [_fill("T", "yes", 0.40, 50, fee=0.35)], settlements=[_settle("T", 0)])
    assert out["T"]["fees_usd"] == 0.35


def test_active_days_spans_the_fill_tape():
    tape = [_fill("T", "yes", 0.40, 25, ts="2026-08-01T00:00:00.000000Z", fid="a"),
            _fill("T", "yes", 0.40, 25, ts="2026-08-05T00:00:00.000000Z", fid="b")]
    out = fc.build([], tape, settlements=[_settle("T", 0)])
    assert out["T"]["active_days"] == 4.0


def test_settlement_for_a_ticker_with_no_fills_is_priced_on_the_receipt_alone():
    out = fc.build([], [], settlements=[_settle("T", 0)])
    assert out["T"]["realized_pnl_usd"] == 0.0, "no fills -> no cash -> receipt only"


def test_a_malformed_revenue_is_skipped_not_guessed():
    bad = _settle("T", 0)
    bad["revenue"] = "not-a-number"
    out = fc.build([], [_fill("T", "yes", 0.40, 50)], settlements=[bad])
    assert "T" not in out, "an unparseable receipt must not invent a cost"
