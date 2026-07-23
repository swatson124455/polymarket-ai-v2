"""Tests for kalshi_attribution_ledger's cash-flow model (the rewards_residual root fix).

Run: python -m pytest test_attribution.py -q   (from kalshi_live/)

WHY THIS FILE EXISTS
--------------------
`rewards_residual` printed +$57.82/day and +$75.17/day on a ~$65 account. Three prior fix
attempts were tested and refuted (see the settlement_revenue docstring). The defect is in
`fill_cashflow`: it read the legacy `action`/`side` pair literally, so an `action=sell,
side=no` fill — which on this account is our NO *bid* filling, i.e. cash OUT — was booked as
cash IN. 156 of 317 fills on the live tape carry that shape.

THE FIXTURES BELOW ARE REAL VENUE PAYLOADS, not synthetic. They were pulled read-only from
/portfolio/fills on 2026-07-23 and each one is tied to an independent venue receipt
(`position_fp`, `market_exposure_dollars`, settlement `revenue`, or the transactions CSV).
That is what makes them pins rather than restatements of the code.
"""
import importlib.util
import os
import sys

import pytest


def _load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(os.path.dirname(__file__), f"{n}.py"))
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


L = _load("kalshi_attribution_ledger")


# --------------------------------------------------------------------------------------
# REAL FILL TAPES (read-only pull 2026-07-23, /trade-api/v2/portfolio/fills)
# --------------------------------------------------------------------------------------

# KXAAAGASW-26JUL27-4.080 — ONE fill, the whole position.
# VENUE RECEIPT: position_fp = "-20.00", market_exposure_dollars = "1.000000".
# 20 NO contracts at $0.05 = $1.00 paid. The old code booked this as +$1.00 RECEIVED.
GASW_080 = [
    dict(fill_id="f080", ticker="KXAAAGASW-26JUL27-4.080",
         created_time="2026-07-22T12:32:50.652246Z", count_fp="20.00",
         yes_price_dollars="0.9500", no_price_dollars="0.0500",
         book_side="ask", outcome_side="no", action="sell", side="no",
         is_taker=False, fee_cost="0.000000"),
]
GASW_080_VENUE_POSITION_FP = -20.00
GASW_080_VENUE_EXPOSURE_USD = 1.000000

# KXTEMPDCH-26JUL2123-T73.99 — settled market, full lifecycle.
# VENUE RECEIPTS: settlement revenue = 2200 cents ($22.00), market_result "yes";
# transactions CSV realized_pnl_with_fees for this ticker = +$8.290.
DCH_73 = [
    dict(fill_id="d1", ticker="KXTEMPDCH-26JUL2123-T73.99",
         created_time="2026-07-22T02:27:17.442639Z", count_fp="11.00",
         yes_price_dollars="0.6600", no_price_dollars="0.3400",
         book_side="bid", outcome_side="yes", action="buy", side="yes",
         is_taker=False, fee_cost="0.000000"),
    dict(fill_id="d2", ticker="KXTEMPDCH-26JUL2123-T73.99",
         created_time="2026-07-22T02:29:53.390603Z", count_fp="20.00",
         yes_price_dollars="0.3300", no_price_dollars="0.6700",
         book_side="bid", outcome_side="yes", action="buy", side="yes",
         is_taker=False, fee_cost="0.000000"),
    dict(fill_id="d3", ticker="KXTEMPDCH-26JUL2123-T73.99",
         created_time="2026-07-22T02:32:27.808722Z", count_fp="8.00",
         yes_price_dollars="0.3000", no_price_dollars="0.7000",
         book_side="bid", outcome_side="yes", action="buy", side="yes",
         is_taker=False, fee_cost="0.000000"),
    # the netting fill: our NO bid at $0.85 fills while we are long 39 YES.
    dict(fill_id="d4", ticker="KXTEMPDCH-26JUL2123-T73.99",
         created_time="2026-07-22T02:34:04.261271Z", count_fp="17.00",
         yes_price_dollars="0.1500", no_price_dollars="0.8500",
         book_side="ask", outcome_side="no", action="sell", side="no",
         is_taker=False, fee_cost="0.000000"),
]
DCH_73_SETTLEMENT = dict(ticker="KXTEMPDCH-26JUL2123-T73.99", market_result="yes",
                         revenue=2200, settled_time="2026-07-22T11:44:00.000000Z")
DCH_73_CSV_REALIZED_PNL = 8.290       # transactions CSV receipt

# KXAAAGASW-26JUL27-4.140 — 25 fills, both book sides, fractional counts, still OPEN.
# VENUE RECEIPT: position_fp = "+30.00".
GASW_140 = [
    ("2026-07-23T01:14:19.664376Z", "9.00", "0.8000", "bid"),
    ("2026-07-23T01:52:09.910673Z", "2.00", "0.7200", "bid"),
    ("2026-07-23T04:37:22.466194Z", "8.00", "0.6900", "bid"),
    ("2026-07-23T04:42:08.452447Z", "10.00", "0.7000", "bid"),
    ("2026-07-23T09:02:42.346258Z", "8.00", "0.7700", "ask"),
    ("2026-07-23T09:17:53.832626Z", "6.39", "0.7700", "ask"),
    ("2026-07-23T10:19:11.487642Z", "2.55", "0.7700", "ask"),
    ("2026-07-23T11:28:15.28123Z", "2.06", "0.7700", "ask"),
    ("2026-07-23T12:14:19.488178Z", "9.00", "0.7600", "bid"),
    ("2026-07-23T12:34:47.742677Z", "9.00", "0.7500", "ask"),
    ("2026-07-23T12:42:16.872065Z", "10.00", "0.7500", "bid"),
    ("2026-07-23T12:54:27.226065Z", "7.00", "0.7600", "ask"),
    ("2026-07-23T12:56:29.999082Z", "10.00", "0.7500", "bid"),
    ("2026-07-23T12:57:01.102794Z", "3.00", "0.7600", "ask"),
    ("2026-07-23T13:07:48.041284Z", "7.00", "0.7400", "bid"),
    ("2026-07-23T13:12:00.092451Z", "1.47", "0.7600", "ask"),
    ("2026-07-23T13:36:05.971629Z", "12.94", "0.7600", "ask"),
    ("2026-07-23T13:50:26.969422Z", "2.59", "0.7600", "ask"),
    ("2026-07-23T13:51:30.960792Z", "17.00", "0.7900", "ask"),
    ("2026-07-23T14:03:09.047339Z", "12.00", "0.8000", "ask"),
    ("2026-07-23T14:07:59.133134Z", "18.00", "0.8000", "bid"),
    ("2026-07-23T14:11:49.268663Z", "1.00", "0.7500", "bid"),
    ("2026-07-23T14:45:36.954672Z", "10.00", "0.7400", "bid"),
    ("2026-07-23T14:47:50.12185Z", "10.00", "0.7400", "bid"),
    ("2026-07-23T14:52:34.799814Z", "10.00", "0.7100", "bid"),
]
GASW_140 = [
    dict(fill_id=f"g{i}", ticker="KXAAAGASW-26JUL27-4.140", created_time=t, count_fp=c,
         yes_price_dollars=y, no_price_dollars=f"{1 - float(y):.4f}", book_side=b,
         outcome_side=("yes" if b == "bid" else "no"),
         action=("buy" if b == "bid" else "sell"), side=("yes" if b == "bid" else "no"),
         is_taker=False, fee_cost="0.000000")
    for i, (t, c, y, b) in enumerate(GASW_140)
]
GASW_140_VENUE_POSITION_FP = 30.00


def _legacy_position_delta(f):
    """The action/side reading the ledger used to imply. Kept ONLY to assert it is wrong."""
    c = float(f["count_fp"])
    if f["side"] == "yes":
        return c if f["action"] == "buy" else -c
    return -c if f["action"] == "buy" else c


# --------------------------------------------------------------------------------------
# 1. FILL SHAPE / POSITION SIGN  (verified against position_fp)
# --------------------------------------------------------------------------------------

def test_fill_outcome_is_read_from_book_side():
    assert L.fill_outcome(GASW_080[0]) == "no"
    assert L.fill_outcome(DCH_73[0]) == "yes"


def test_position_delta_ask_fill_is_negative():
    """ask == our NO bid == we ACQUIRE no == position moves negative."""
    assert L.fill_position_delta(GASW_080[0]) == pytest.approx(-20.0)


def test_position_delta_bid_fill_is_positive():
    assert L.fill_position_delta(DCH_73[0]) == pytest.approx(11.0)


def test_reconstructed_position_matches_venue_single_fill():
    """RECEIPT: /portfolio/positions position_fp = -20.00 for KXAAAGASW-26JUL27-4.080."""
    _, pos = L.replay_fills(GASW_080)
    assert pos["KXAAAGASW-26JUL27-4.080"] == pytest.approx(GASW_080_VENUE_POSITION_FP)


def test_reconstructed_position_matches_venue_25_fill_tape():
    """RECEIPT: position_fp = +30.00 for KXAAAGASW-26JUL27-4.140 after these 25 fills."""
    _, pos = L.replay_fills(GASW_140)
    assert pos["KXAAAGASW-26JUL27-4.140"] == pytest.approx(GASW_140_VENUE_POSITION_FP)


def test_legacy_action_side_reading_is_refuted_by_the_venue():
    """The convention the ledger used gives +198 where the venue reports +30, and +20 where
    the venue reports -20. Documented so nobody 'restores' it."""
    assert sum(_legacy_position_delta(f) for f in GASW_140) == pytest.approx(198.0)
    assert sum(_legacy_position_delta(f) for f in GASW_080) == pytest.approx(+20.0)
    assert GASW_080_VENUE_POSITION_FP == -20.00


def test_reconstruction_predicts_settlement_revenue():
    """RECEIPT: settlement revenue = 2200 cents. Net position after the tape is +22 YES and
    market_result is 'yes', so the venue owes exactly $22."""
    _, pos = L.replay_fills(DCH_73)
    net = pos["KXTEMPDCH-26JUL2123-T73.99"]
    assert net == pytest.approx(22.0)
    assert L.settlement_revenue(DCH_73_SETTLEMENT) == pytest.approx(net)


# --------------------------------------------------------------------------------------
# 2. CASH FLOW SIGN  (the actual bug)
# --------------------------------------------------------------------------------------

def test_ask_fill_is_cash_OUT():
    """THE BUG. Old code: side=='no' -> price 0.05, action=='sell' -> +0.05*20 = +$1.00 IN.
    Truth: we bought 20 NO at $0.05 = $1.00 OUT."""
    assert L.fill_cashflow(GASW_080[0], 0.0) == pytest.approx(-1.00)


def test_ask_fill_cash_equals_venue_cost_basis():
    """RECEIPT: market_exposure_dollars = 1.000000 — the venue's own cost basis for the
    position this single fill created. |cash| must equal it."""
    cash = L.fill_cashflow(GASW_080[0], 0.0)
    assert abs(cash) == pytest.approx(GASW_080_VENUE_EXPOSURE_USD)
    assert cash < 0


def test_bid_fill_is_cash_OUT_at_yes_price():
    assert L.fill_cashflow(DCH_73[0], 0.0) == pytest.approx(-0.66 * 11)


def test_netting_fill_receives_the_pair_release_not_the_no_price():
    """Fill d4: our NO bid at $0.85 fills for 17 while we are long 39 YES. Kalshi nets the
    17 pairs and releases $1 each, so we receive 1 - 0.85 = $0.15/contract = +$2.55.
    Old code booked +0.85*17 = +$14.45 — an $11.90 phantom inflow on ONE fill.
    A naive sign flip books -$14.45, which is $17 wrong in the other direction."""
    assert L.fill_cashflow(DCH_73[3], 39.0) == pytest.approx(+2.55)


def test_full_lifecycle_cash_plus_settlement_equals_the_csv_receipt():
    """RECEIPT: kalshi_transactions_2026-07-23.csv realized_pnl_with_fees for this ticker
    sums to +$8.290. Replayed fill cash (-13.71) + settlement revenue (+22.00) must equal it."""
    events, _ = L.replay_fills(DCH_73)
    cash = sum(e["cash"] for e in events)
    assert cash == pytest.approx(-13.71)
    assert cash + L.settlement_revenue(DCH_73_SETTLEMENT) == pytest.approx(DCH_73_CSV_REALIZED_PNL)


def test_partial_offset_then_flip():
    """Long 5 YES, our NO bid at $0.30 fills for 8: 5 net out (+0.70 each = +3.50), 3 open a
    NO position (-0.30 each = -0.90). Net +2.60, ending position -3."""
    f = dict(fill_id="x", ticker="T", created_time="2026-07-22T00:00:00Z", count_fp="8.00",
             yes_price_dollars="0.7000", no_price_dollars="0.3000", book_side="ask",
             outcome_side="no", action="sell", side="no", is_taker=False, fee_cost="0.000000")
    assert L.fill_cashflow(f, 5.0) == pytest.approx(+2.60)
    assert L.fill_position_delta(f) == pytest.approx(-8.0)


def test_open_then_close_roundtrip_is_the_price_difference():
    buy = dict(fill_id="a", ticker="T", created_time="2026-07-22T00:00:00Z", count_fp="10.00",
               yes_price_dollars="0.6000", no_price_dollars="0.4000", book_side="bid",
               outcome_side="yes", action="buy", side="yes", is_taker=False, fee_cost="0.000000")
    close = dict(fill_id="b", ticker="T", created_time="2026-07-22T00:01:00Z", count_fp="10.00",
                 yes_price_dollars="0.6500", no_price_dollars="0.3500", book_side="ask",
                 outcome_side="no", action="sell", side="no", is_taker=False, fee_cost="0.000000")
    events, pos = L.replay_fills([buy, close])
    assert sum(e["cash"] for e in events) == pytest.approx(0.05 * 10)
    assert pos.get("T", 0.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------------------
# 3. FEES  (canon §M9/§M10 — fee_cost is dollars; leaking it inflates rewards)
# --------------------------------------------------------------------------------------

def test_fee_cost_is_subtracted_in_dollars():
    """fee_cost is receipt-verified DOLLARS: the 59 fee-bearing fills on the live tape sum to
    $2.5823, exactly the transactions CSV's open+close fee total."""
    f = dict(fill_id="x", ticker="T", created_time="2026-07-22T00:00:00Z", count_fp="13.00",
             yes_price_dollars="0.8900", no_price_dollars="0.1100", book_side="bid",
             outcome_side="yes", action="buy", side="yes", is_taker=True, fee_cost="0.035000")
    assert L.fill_cashflow(f, 0.0) == pytest.approx(-0.89 * 13 - 0.035)


def test_fee_never_flips_sign_of_a_credit():
    """A fee is always a cost, whatever sign the venue reports it with."""
    f = dict(fill_id="x", ticker="T", created_time="2026-07-22T00:00:00Z", count_fp="10.00",
             yes_price_dollars="0.7000", no_price_dollars="0.3000", book_side="ask",
             outcome_side="no", action="sell", side="no", is_taker=True, fee_cost="-0.020000")
    assert L.fill_cashflow(f, 10.0) == pytest.approx(0.70 * 10 - 0.02)


# --------------------------------------------------------------------------------------
# 4. FAIL-CLOSED ON AN UNRECOGNISED PAYLOAD
# --------------------------------------------------------------------------------------

def test_unknown_fill_shape_raises_rather_than_guessing():
    """Every fill on the live tape is bid/yes or ask/no. If Kalshi ever emits something else,
    emitting a WRONG rewards number is worse than emitting none."""
    f = dict(fill_id="x", ticker="T", created_time="2026-07-22T00:00:00Z", count_fp="1.00",
             yes_price_dollars="0.5000", no_price_dollars="0.5000",
             action="sell", side="yes", is_taker=False, fee_cost="0.000000")
    with pytest.raises(ValueError):
        L.fill_outcome(f)


def test_replay_sorts_by_created_time():
    """Netting is order-dependent, so an out-of-order tape must not change the answer."""
    events, pos = L.replay_fills(list(reversed(DCH_73)))
    assert sum(e["cash"] for e in events) == pytest.approx(-13.71)
    assert pos["KXTEMPDCH-26JUL2123-T73.99"] == pytest.approx(22.0)


# --------------------------------------------------------------------------------------
# 5. END-TO-END: THE RESIDUAL MUST EQUAL THE CREDIT
# --------------------------------------------------------------------------------------

def test_residual_equals_the_credit_end_to_end():
    """Replays the shape of the live 07-22 window: a $1.00 NO-bid fill, a settled market
    paying $22.00, and a $2.23 LIP credit posting. residual = dBalance - cash - revenue.

    LIVE VALIDATION of the same identity (read-only pull 2026-07-23, 27 ledger intervals,
    168 fills, 26 settlements): the corrected model reproduces the balance-implied cash
    EXACTLY every interval, and the residual isolates the credits to the cent —
    +$4.35 in the 07-22T06:29Z interval ($1.88+$2.47) and +$2.23 in the 07-22T06:58Z one.
    The old model was $25.63 wrong over the same window."""
    fills = GASW_080 + DCH_73
    events, _ = L.replay_fills(fills)
    cash = sum(e["cash"] for e in events)
    revenue = L.settlement_revenue(DCH_73_SETTLEMENT)
    credit = 2.23
    prev_bal = 100.0
    bal = prev_bal + cash + revenue + credit
    assert round(bal - prev_bal - cash - revenue, 4) == pytest.approx(credit)


def test_residual_is_wrong_under_the_legacy_convention():
    """Same scenario scored with the old action/side cash reading: the residual comes out
    -$11.67 where $2.23 was actually credited — a $13.90 error on five fills. On the live
    tape the same defect ran the other way and printed tens of dollars of phantom rewards."""
    fills = GASW_080 + DCH_73
    events, _ = L.replay_fills(fills)
    cash = sum(e["cash"] for e in events)
    revenue = L.settlement_revenue(DCH_73_SETTLEMENT)
    credit = 2.23
    prev_bal = 100.0
    bal = prev_bal + cash + revenue + credit

    def legacy(f):
        c = float(f["count_fp"])
        yp = float(f["yes_price_dollars"])
        price = yp if f["side"] == "yes" else 1.0 - yp
        return (-price * c if f["action"] == "buy" else price * c) - abs(float(f["fee_cost"]))

    legacy_cash = sum(legacy(f) for f in fills)
    legacy_residual = round(bal - prev_bal - legacy_cash - revenue, 4)
    assert legacy_residual == pytest.approx(-11.67, abs=0.02)
    assert abs(legacy_residual - credit) > 13.0
