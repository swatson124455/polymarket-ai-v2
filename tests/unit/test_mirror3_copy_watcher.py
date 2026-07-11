"""Unit tests for mirror_v3/copy_watcher.py — pure core only (the network
runner is exercised on the VPS; every decision function is offline here).
Run: python3 -m pytest tests/unit/test_mirror3_copy_watcher.py --override-ini "addopts=" """
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mirror_v3 import copy_watcher as cw  # noqa: E402

A = "0xabcd000000000000000000000000000000000001"
B = "0x9999000000000000000000000000000000000009"
ROSTER = {A}


def _ev(maker=A, taker=B, m_id=0, t_id=777, m_amt=60_000_000, t_amt=100_000_000):
    return {"maker": maker, "taker": taker, "makerAssetId": m_id,
            "takerAssetId": t_id, "makerAmountFilled": m_amt,
            "takerAmountFilled": t_amt}


# ── decode_fill ───────────────────────────────────────────────────────────────
def test_decode_maker_buy():
    sig = cw.decode_fill(_ev(), ROSTER)
    assert sig is not None
    assert sig["trader"] == A and sig["token_id"] == "777"
    assert abs(sig["whale_price"] - 0.60) < 1e-9
    assert abs(sig["whale_size_usd"] - 60.0) < 1e-9
    assert sig["side"] == "BUY"


def test_decode_taker_buy_reversed_assets():
    ev = _ev(maker=B, taker=A, m_id=777, t_id=0,
             m_amt=100_000_000, t_amt=60_000_000)
    sig = cw.decode_fill(ev, ROSTER)
    assert sig is not None and sig["trader"] == A
    assert abs(sig["whale_price"] - 0.60) < 1e-9


def test_decode_ignores_sells_and_strangers():
    # roster addr SELLING (gives token, receives USDC) -> not a BUY signal
    sell = _ev(m_id=777, t_id=0)          # maker=A gives token
    assert cw.decode_fill(sell, ROSTER) is None
    # non-roster trade
    assert cw.decode_fill(_ev(maker=B, taker=B), ROSTER) is None
    # zero-amount garbage
    assert cw.decode_fill(_ev(m_amt=0, t_amt=0), ROSTER) is None
    # token-for-token (no USDC leg) — must not divide or signal
    assert cw.decode_fill(_ev(m_id=555, t_id=777), ROSTER) is None


# ── gates ─────────────────────────────────────────────────────────────────────
def test_gates_ok_fills_at_ask():
    v, fill = cw.evaluate_gates(0.60, 0.59, 0.61, max_chase=0.02, max_spread=0.05)
    assert v == "OK" and fill == 0.61


def test_gates_no_book():
    assert cw.evaluate_gates(0.6, None, None, 0.02, 0.05) == ("NO_BOOK", None)
    assert cw.evaluate_gates(0.6, 0.5, None, 0.02, 0.05) == ("NO_BOOK", None)


def test_gates_spread_too_wide():
    v, fill = cw.evaluate_gates(0.60, 0.50, 0.60, max_chase=0.02, max_spread=0.05)
    assert v == "SPREAD_TOO_WIDE" and fill is None


def test_gates_price_ran_away():
    v, fill = cw.evaluate_gates(0.60, 0.62, 0.63, max_chase=0.02, max_spread=0.05)
    assert v == "PRICE_RAN_AWAY" and fill is None
    # exactly at the cap still fills
    v, fill = cw.evaluate_gates(0.60, 0.61, 0.62, max_chase=0.02, max_spread=0.05)
    assert v == "OK" and fill == 0.62


# ── roster / config / dedup / record ─────────────────────────────────────────
def test_load_roster_valid_and_empty(tmp_path):
    p = tmp_path / "audit.json"
    p.write_text(json.dumps({"clean": [A.upper(), "not-an-addr", "0xshort"]}))
    assert cw.load_roster(str(p)) == [A]
    p.write_text(json.dumps({"clean": []}))
    try:
        cw.load_roster(str(p))
        assert False, "empty roster must raise"
    except ValueError:
        pass


def test_config_requires_explicit_env():
    try:
        cw.WatcherConfig.from_env({"MIRROR3_ROSTER_PATH": "/x"})
        assert False, "missing RPC URL must raise"
    except ValueError as e:
        assert "MIRROR3_RPC_URL" in str(e)
    cfg = cw.WatcherConfig.from_env({
        "MIRROR3_ROSTER_PATH": "/x", "MIRROR3_RPC_URL": "https://r",
        "MIRROR3_MAX_CHASE_C": "3", "MIRROR3_MAX_SPREAD_C": "8",
        "MIRROR3_POLL_S": "1.5"})
    assert cfg.max_chase == 0.03 and cfg.max_spread == 0.08 and cfg.poll_s == 1.5


def test_first_buy_dedup():
    d = cw.FirstBuyDedup()
    assert d.is_first(A, "777") is True
    assert d.is_first(A, "777") is False
    assert d.is_first(A, "888") is True
    assert d.is_first(B, "777") is True


def test_addr_topic_padding():
    t = cw.addr_topic(A)
    assert t.startswith("0x") and len(t) == 66
    assert t.endswith(A[2:])


def test_shadow_record_shape_and_lag():
    sig = {"trader": A, "token_id": "777", "side": "BUY",
           "whale_price": 0.6, "whale_size_usd": 60.0, "first_buy": True}
    rec = cw.shadow_record(sig, "OK", 0.61, 0.59, 0.61,
                           block_ts=1_000_000, now_ts=1_000_003.25, tx="0xdead")
    assert rec["detect_lag_s"] == 3.25
    assert rec["verdict"] == "OK" and rec["shadow_fill"] == 0.61
    assert rec["trader"] == A and rec["tx"] == "0xdead"
    json.dumps(rec)  # must be JSONL-serializable as-is


def test_block_chunks_cap():
    spans = cw.block_chunks(0, 2000)
    assert all(hi - lo + 1 <= cw.GETLOGS_CHUNK for lo, hi in spans)
    assert spans[0][0] == 0 and spans[-1][1] == 2000


def test_get_logs_compat_both_signatures():
    import asyncio

    class V7:
        async def get_logs(self, argument_filters=None, from_block=None,
                           to_block=None, block_hash=None):
            return [("v7", from_block, to_block)]

    class V6:
        async def get_logs(self, argument_filters=None, fromBlock=None,
                           toBlock=None, block_hash=None):
            return [("v6", fromBlock, toBlock)]

    assert asyncio.run(cw.get_logs_compat(V7(), 1, 2, {})) == [("v7", 1, 2)]
    assert asyncio.run(cw.get_logs_compat(V6(), 1, 2, {})) == [("v6", 1, 2)]
