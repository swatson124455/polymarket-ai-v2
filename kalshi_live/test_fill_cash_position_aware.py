"""THE CASH RECORDER'S FILL MODEL IS POSITION-AWARE (defect 13 root fix, 2026-08-02).

The recorder used to sign cash off `action` alone. Kalshi prints a NO ACQUISITION as
action="sell" (census over the complete tape, API read 2026-08-02T22:42:49Z:
{('buy','yes'): 582, ('sell','no'): 651} of 1,233 fills), so buying NO was booked as selling
YES — cash IN where collateral went OUT. kalshi_attribution_ledger root-fixed this exact model
on 2026-07-23 and records at :196-201 that "the signature WAS the bug"; the recorder now
DELEGATES to it instead of carrying a second model.

Measured impact over the complete history (n=1,233 fills / 127 settlements): cumulative fill
cash -$229.8397 -> -$595.4397, and the reconciliation residual $190.1000 -> $555.7000 against
venue-verified deposits $565.00 (KALSHI_HANDOFF_2026-08-02.md:67).

These pins are written on REAL venue field shapes (all 17 keys of a live fill row), so a
regression to any action-signed model fails them.
"""
import kalshi_cash_recorder as cr


def _fill(outcome, price, count, fee=0.0, ticker="T", ts="2026-08-02T00:00:00.000000Z",
          fid="f1"):
    """A live-shaped fill row. `action` is deliberately derived the way the VENUE derives it:
    acquiring YES prints buy/bid, acquiring NO prints sell/ask. A model that reads `action`
    therefore sees every NO acquisition as a sale."""
    yes_px = price if outcome == "yes" else round(1.0 - price, 4)
    return {"ticker": ticker, "market_ticker": ticker,
            "outcome_side": outcome,
            "side": outcome,
            "book_side": "bid" if outcome == "yes" else "ask",
            "action": "buy" if outcome == "yes" else "sell",
            "count_fp": f"{count:.2f}",
            "yes_price_dollars": f"{yes_px:.4f}",
            "no_price_dollars": f"{1.0 - yes_px:.4f}",
            "fee_cost": f"{fee:.6f}",
            "fill_id": fid, "trade_id": fid, "created_time": ts, "is_taker": False}


def test_buying_no_is_an_outflow_not_an_inflow():
    # THE DEFECT ITSELF. 50 ct of NO at $0.30 costs $15.00 of collateral. The old model read
    # action="sell" and booked +50 x yes_price(0.70) = +$35.00 — wrong sign AND wrong price.
    cash = cr.cum_fills_cash([_fill("no", 0.30, 50)])
    assert cash == -15.0, f"a NO acquisition must cost its own price: {cash}"


def test_buying_yes_is_an_outflow_at_its_own_price():
    assert cr.cum_fills_cash([_fill("yes", 0.35, 50)]) == -17.5


def test_offsetting_fill_releases_the_dollar_collateral():
    # Open 50 YES @0.35 (-17.50), then acquire 50 NO @0.60 against it. That CLOSES the pair:
    # the venue releases $1/contract, so the second leg is +(1-0.60) x 50 = +20.00.
    # Net +2.50 = 50 x (0.40 - 0.35), i.e. exactly the price improvement. An action-signed
    # model cannot produce this because it never books the collateral release.
    tape = [_fill("yes", 0.35, 50, ts="2026-08-02T00:00:00.000000Z", fid="a"),
            _fill("no", 0.60, 50, ts="2026-08-02T00:01:00.000000Z", fid="b")]
    assert round(cr.cum_fills_cash(tape), 6) == 2.5


def test_a_round_trip_nets_to_the_price_improvement_only():
    # Same shape, a LOSING round trip: buy 20 YES @0.60, exit into NO @0.55 (= selling yes at
    # 0.45). Net = 20 x (0.45 - 0.60) = -3.00.
    tape = [_fill("yes", 0.60, 20, ts="2026-08-02T00:00:00.000000Z", fid="a"),
            _fill("no", 0.55, 20, ts="2026-08-02T00:01:00.000000Z", fid="b")]
    assert round(cr.cum_fills_cash(tape), 6) == -3.0


def test_partial_offset_splits_into_closed_and_newly_opened():
    # Long 10 YES @0.40 (-4.00), then 25 NO @0.50: 10 offset (+0.50 x 10 = +5.00) and 15 newly
    # opened NO (-0.50 x 15 = -7.50). Net -6.50. Path-dependence is the whole point of the
    # replay — a per-fill function of one argument cannot express this split.
    tape = [_fill("yes", 0.40, 10, ts="2026-08-02T00:00:00.000000Z", fid="a"),
            _fill("no", 0.50, 25, ts="2026-08-02T00:01:00.000000Z", fid="b")]
    assert round(cr.cum_fills_cash(tape), 6) == -6.5


def test_netting_is_per_ticker_not_global():
    # A NO fill on ticker B must not offset a YES position on ticker A.
    tape = [_fill("yes", 0.40, 10, ticker="A", ts="2026-08-02T00:00:00.000000Z", fid="a"),
            _fill("no", 0.50, 10, ticker="B", ts="2026-08-02T00:01:00.000000Z", fid="b")]
    assert round(cr.cum_fills_cash(tape), 6) == -9.0      # -4.00 and -5.00, no release


def test_fees_are_subtracted_exactly_once_on_both_legs():
    tape = [_fill("yes", 0.35, 50, fee=0.25, ts="2026-08-02T00:00:00.000000Z", fid="a"),
            _fill("no", 0.60, 50, fee=0.40, ts="2026-08-02T00:01:00.000000Z", fid="b")]
    assert round(cr.cum_fills_cash(tape), 6) == round(2.5 - 0.65, 6)


def test_replay_order_follows_created_time_not_list_order():
    # Netting is path-dependent, so an out-of-order tape must still replay chronologically.
    # Fed backwards, the open must still be seen before the offset.
    late = _fill("no", 0.60, 50, ts="2026-08-02T00:01:00.000000Z", fid="b")
    early = _fill("yes", 0.35, 50, ts="2026-08-02T00:00:00.000000Z", fid="a")
    assert round(cr.cum_fills_cash([late, early]), 6) == 2.5


def test_naked_no_held_to_settlement_keeps_its_collateral_out():
    # The class the old model got most wrong: nothing offsets, so the whole cost stays out.
    # 3 ct NO @0.04 = -$0.12; the action-signed model booked +3 x 0.96 = +$2.88.
    assert round(cr.cum_fills_cash([_fill("no", 0.04, 3)]), 6) == -0.12


def test_empty_tape_is_zero_not_an_error():
    assert cr.cum_fills_cash([]) == 0.0
