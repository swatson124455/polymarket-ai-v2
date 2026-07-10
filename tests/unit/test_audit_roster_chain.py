"""Unit tests for scripts/audit_roster_chain.py — pure matching core only
(network paths are exercised on the VPS; the self-test covers the same core).
Run: python3 -m pytest tests/unit/test_audit_roster_chain.py --override-ini "addopts=" """
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import audit_roster_chain as ac  # noqa: E402

A = "0xAbCd000000000000000000000000000000000001"


def _ev(maker=A, taker="0x9", m_id=0, t_id=777, m_amt=60_000_000, t_amt=100_000_000):
    return {"maker": maker, "taker": taker, "makerAssetId": m_id,
            "takerAssetId": t_id, "makerAmountFilled": m_amt,
            "takerAmountFilled": t_amt}


def test_maker_buy_verified():
    m = ac.match_fill([_ev()], A, 777, "BUY", 0.60, 0.02)
    assert m["status"] == "verified"
    assert abs(m["chain_price"] - 0.60) < 1e-9
    assert abs(m["chain_tokens"] - 100.0) < 1e-9


def test_taker_buy_reversed_assets():
    ev = _ev(maker="0x9", taker=A, m_id=777, t_id=0,
             m_amt=50_000_000, t_amt=33_000_000)
    m = ac.match_fill([ev], A, 777, "BUY", 0.66, 0.02)
    assert m["status"] == "verified"


def test_maker_sell_direction():
    ev = _ev(m_id=777, t_id=0, m_amt=100_000_000, t_amt=40_000_000)
    m = ac.match_fill([ev], A, 777, "SELL", 0.40, 0.02)
    assert m["status"] == "verified"


def test_price_mismatch_flagged():
    m = ac.match_fill([_ev()], A, 777, "BUY", 0.90, 0.02)
    assert m["status"] == "price_mismatch"
    assert m["chain_price"] is not None


def test_wrong_token_addr_direction_not_found():
    assert ac.match_fill([_ev()], A, 888, "BUY", 0.6, 0.02)["status"] == "not_found"
    assert ac.match_fill([_ev()], "0xDEAD", 777, "BUY", 0.6, 0.02)["status"] == "not_found"
    assert ac.match_fill([_ev()], A, 777, "SELL", 0.6, 0.02)["status"] == "not_found"


def test_partial_fills_aggregate():
    half = _ev(m_amt=30_000_000, t_amt=50_000_000)
    m = ac.match_fill([half, half], A, 777, "BUY", 0.60, 0.02)
    assert m["status"] == "verified"
    assert abs(m["chain_tokens"] - 100.0) < 1e-9


def test_case_insensitive_address():
    m = ac.match_fill([_ev()], A.upper().replace("0X", "0x"), 777, "BUY", 0.60, 0.02)
    assert m["status"] == "verified"


def test_zero_token_amount_ignored():
    ev = _ev(m_amt=0, t_amt=0)
    assert ac.match_fill([ev], A, 777, "BUY", 0.6, 0.02)["status"] == "not_found"


def test_trader_verdicts():
    assert ac.trader_verdict({"verified": 20}, 20, 0.2) == "CLEAN"
    assert ac.trader_verdict({"verified": 19, "price_mismatch": 1}, 20, 0.2) == "DISCREPANT"
    assert ac.trader_verdict({"verified": 14, "not_found": 6}, 20, 0.2) == "THIN"
    assert ac.trader_verdict({"rpc_error": 15, "verified": 5}, 20, 0.2) == "ERROR"
    assert ac.trader_verdict({}, 0, 0.2) == "ERROR"
    # one not-found in 20 stays CLEAN (RPC gaps happen; 20% is the tripwire)
    assert ac.trader_verdict({"verified": 19, "not_found": 1}, 20, 0.2) == "CLEAN"


def test_sample_fills_buys_only_time_spread():
    trades = [{"side": "BUY", "tokenId": str(i), "timestamp": i * 1000,
               "size": 5, "price": 0.5} for i in range(100)]
    trades += [{"side": "SELL", "tokenId": "999", "timestamp": 1, "size": 5,
                "price": 0.5},
               {"side": "BUY", "tokenId": "not-numeric", "timestamp": 2,
                "size": 5, "price": 0.5}]
    s = ac.sample_fills(trades, 10, 7)
    assert len(s) == 10
    assert all(t["side"] == "BUY" and t["tokenId"].isdigit() for t in s)
    assert s == sorted(s, key=lambda t: str(t["timestamp"]))


def test_sample_fills_small_history_returned_whole():
    trades = [{"side": "BUY", "tokenId": "1", "timestamp": 5, "size": 1,
               "price": 0.5}]
    assert len(ac.sample_fills(trades, 20, 7)) == 1
