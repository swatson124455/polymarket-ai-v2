"""Pins for kalshi_api_types (R5 part 3) — every fixture below is a RESPONSE RECORDED
FROM THE LIVE VENUE on 2026-08-25 in this session (timestamps noted), lightly truncated.
Plus negative pins: shape drift must RAISE ApiShapeError, never fall through to {}.
"""
import pytest

import kalshi_api_types as T


# ---- recorded fixtures (live venue, 2026-08-25) -----------------------------------------------
BALANCE = {  # 03:46:54Z read
    "balance": 31647, "balance_dollars": "316.4766", "portfolio_value": 45,
    "balance_breakdown": [{"balance": "316.4766", "exchange_index": 0}], "updated_ts": 1787629615}

POSITIONS = {"cursor": "", "market_positions": [  # 03:47Z read
    {"exchange_index": 0, "fees_paid_dollars": "0.000000",
     "last_updated_ts": "2026-08-25T01:32:57.439604Z", "market_exposure_dollars": "0.050000",
     "position_fp": "-5.00", "realized_pnl_dollars": "0.000000",
     "ticker": "KXAAAGASD-26AUG25-4.0600", "total_traded_dollars": "0.050000"}]}

ORDERS = {"orders": [  # 03:46Z read
    {"ticker": "KXAAAGASW-26AUG31-3.900", "side": "yes", "action": "buy",
     "order_id": "01a03a94-aaaa-7d44-851d-7acb7f150000",
     "yes_price_dollars": "0.9800", "no_price_dollars": "0.0200",
     "created_time": "2026-08-25T03:46:44.693321Z"}]}

AMEND = {  # 20:21Z supervised amend-decrease verify (2ct -> 1ct, SAME order_id)
    "fill_count": "0.00", "order_id": "01a03a95-c910-7d44-851d-7acb7f150786",
    "remaining_count": "1.00", "ts_ms": 1787689291848}

CANCEL = {  # 20:21Z, same test
    "order_id": "01a03a95-c910-7d44-851d-7acb7f150786", "reduced_by": "1.00",
    "ts_ms": 1787689293330}

PROGRAM = {  # incentive_programs row, 13:48:09Z read
    "discount_factor_bps": 5000, "end_date": "2026-08-31T03:59:00Z",
    "id": "e0269fe5-24b5-4fe2-9c51-05a1f22c4ced", "incentive_description": "series_lip",
    "incentive_type": "liquidity", "market_id": "dcf94adb-a7d3-4e0c-ab39-f48736f34e75",
    "market_ticker": "KXAAAGASW-26AUG31-3.900", "paid_out": False,
    "period_reward": 1000000, "start_date": "2026-08-24T14:15:00Z",
    "target_size_fp": "1000.00"}

ESTIMATES = {  # est-feed snapshot 16:04:47Z
    "ts": "2026-08-25T16:04:47.472693+00:00", "estimates": [
        {"program_id": "fbba5c22-f6d1-41b3-ae2b-e80887405256", "reward_centicents": 395},
        {"program_id": "eaa23a75-c892-4534-aacb-6a1468c3cd21", "reward_centicents": 0}]}


# ---- positive pins ----------------------------------------------------------------------------
def test_balance():
    assert T.parse_balance(BALANCE) == 316.4766     # dollars, never the cents int


def test_positions_uses_position_fp():
    p = T.parse_positions(POSITIONS)
    assert p[0].position == -5.0 and p[0].ticker == "KXAAAGASD-26AUG25-4.0600"
    assert p[0].exposure_dollars == 0.05


def test_orders():
    o = T.parse_orders(ORDERS)[0]
    assert o.yes_price_dollars == 0.98 and o.no_price_dollars == 0.02 and o.side == "yes"


def test_amend_and_cancel():
    a = T.parse_amend(AMEND)
    assert a.remaining_count == 1.0 and a.fill_count == 0.0
    c = T.parse_cancel(CANCEL)
    assert c.reduced_by == 1.0 and c.order_id == a.order_id


def test_order_create_both_shapes():
    top = {"order_id": "x-1", "status": "resting"}
    nested = {"order": {"order_id": "x-2"}}          # the 08-21 dual-shape incident
    assert T.parse_order_create(top).order_id == "x-1"
    assert T.parse_order_create(nested).order_id == "x-2"
    assert T.parse_order_create(nested).status == ""


def test_program_units():
    p = T.parse_incentive_program(PROGRAM)
    assert p.target_size == 1000.0
    assert p.discount_factor == 0.5                  # 5000 bps
    assert p.pool_usd_day == 100.0                   # 1,000,000 centicents / 10000
    assert p.program_id.startswith("e0269fe5")


def test_estimates_units_and_zero_rows():
    e = T.parse_estimates(ESTIMATES)
    assert abs(e["fbba5c22-f6d1-41b3-ae2b-e80887405256"] - 0.0395) < 1e-12
    assert e["eaa23a75-c892-4534-aacb-6a1468c3cd21"] == 0.0   # 0-rows are real (F4)


# ---- negative pins: drift RAISES, never {} ----------------------------------------------------
def test_missing_key_raises_not_defaults():
    with pytest.raises(T.ApiShapeError):
        T.parse_positions({"market_positions": [{"ticker": "T", "position": "-5.00",
                                                 "market_exposure_dollars": "0.05",
                                                 "realized_pnl_dollars": "0"}]})  # plain `position`
    with pytest.raises(T.ApiShapeError):
        T.parse_balance({"balance": 31647})           # cents-only, no dollars field
    with pytest.raises(T.ApiShapeError):
        T.parse_order_create({"status": "resting"})   # no order_id anywhere
    with pytest.raises(T.ApiShapeError):
        T.parse_amend({"order_id": "x", "remaining_count": "abc", "fill_count": "0"})
    with pytest.raises(T.ApiShapeError):
        T.parse_estimates({"ts": "t"})                # no estimates key
