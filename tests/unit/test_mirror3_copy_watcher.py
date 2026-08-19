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


def test_shadow_record_book_fields():
    sig = {"trader": A, "token_id": "777", "side": "BUY",
           "whale_price": 0.6, "whale_size_usd": 60.0, "first_buy": True}
    book = {"asks": [{"price": 0.61, "size": 50.0}],
            "bids": [{"price": 0.59, "size": 40.0}]}
    rec = cw.shadow_record(sig, "OK", 0.61, 0.59, 0.61,
                           block_ts=1, now_ts=2.0, tx="0x1", book=book)
    assert rec["book_asks"] == book["asks"] and rec["book_bids"] == book["bids"]
    json.dumps(rec)
    # no book -> explicit nulls, and the pre-ladder call shape still works
    rec = cw.shadow_record(sig, "OK", 0.61, 0.59, 0.61,
                           block_ts=1, now_ts=2.0, tx="0x1")
    assert rec["book_asks"] is None and rec["book_bids"] is None
    json.dumps(rec)


# ── trim_book / fetch_book ────────────────────────────────────────────────────
def test_trim_book_sorts_coerces_truncates():
    raw = {"asks": [{"price": "0.65", "size": "10"},
                    {"price": "0.61", "size": "50"},
                    {"price": "bad", "size": "1"},
                    {"size": "5"}],
           "bids": [{"price": "0.55", "size": "20"},
                    {"price": "0.59", "size": "40"}]}
    book = cw.trim_book(raw)
    assert [l["price"] for l in book["asks"]] == [0.61, 0.65]  # ascending
    assert [l["price"] for l in book["bids"]] == [0.59, 0.55]  # descending
    assert all(isinstance(l["price"], float) for l in book["asks"])
    deep = {"asks": [{"price": str(0.5 + i / 1000), "size": "1"}
                     for i in range(100)], "bids": []}
    assert len(cw.trim_book(deep)["asks"]) == cw.BOOK_DEPTH


def test_trim_book_rejects_empty_and_garbage():
    assert cw.trim_book(None) is None
    assert cw.trim_book("nope") is None
    assert cw.trim_book({}) is None
    assert cw.trim_book({"asks": [], "bids": None}) is None
    assert cw.trim_book({"asks": [{"price": "0", "size": "5"}]}) is None
    # one valid side is enough
    one = cw.trim_book({"asks": [{"price": "0.6", "size": "5"}]})
    assert one["asks"] and one["bids"] == []


def test_fetch_book_fail_soft():
    import asyncio

    class BoomSession:
        def get(self, *a, **k):
            raise RuntimeError("network down")

    class Resp:
        def __init__(self, payload):
            self._p = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            return self._p

    class OkSession:
        def get(self, *a, **k):
            return Resp({"asks": [{"price": "0.61", "size": "50"}],
                         "bids": [{"price": "0.59", "size": "40"}]})

    assert asyncio.run(cw.fetch_book(BoomSession(), "777")) is None
    book = asyncio.run(cw.fetch_book(OkSession(), "777"))
    assert book["asks"][0] == {"price": 0.61, "size": 50.0}


# ── V2 decode + direction (layout validated on-chain 2026-07-12) ─────────────
TOK = 61122849617506817837555062824416940793332636909568241444602408963781913414334


def _pad32(n):
    return f"{n:064x}"


def _v2_log(owner=A, taker="0x" + "9" * 40, usdc=23_141_920_320,
            tok=23_808_560_000, token=TOK, topic0=None,
            address="0xe111180000d2663c0091e4f400237545b87b996b"):
    return {"address": address,
            "topics": [topic0 or cw.FILL_TOPIC_V2,
                       "0x" + "ab" * 32,                      # orderHash
                       "0x" + "0" * 24 + owner[2:].lower(),   # order owner
                       "0x" + "0" * 24 + taker[2:].lower()],
            "data": "0x" + _pad32(0) + _pad32(token) + _pad32(usdc)
                    + _pad32(tok) + _pad32(0) + _pad32(0) + _pad32(0)}


def test_decode_fill_v2_roster_order():
    sig = cw.decode_fill_v2(_v2_log(), ROSTER)
    assert sig is not None and sig["trader"] == A
    assert sig["token_id"] == str(TOK)
    assert abs(sig["whale_price"] - 0.972) < 1e-4      # ground-truth trade
    assert abs(sig["whale_size_usd"] - 23141.92032) < 1e-6
    assert sig["was_taker"] is False


def test_decode_fill_v2_taker_summary_and_rejects():
    # taker summary: topic3 == emitting exchange
    ex = "0xe111180000d2663c0091e4f400237545b87b996b"
    sig = cw.decode_fill_v2(_v2_log(taker=ex, address=ex), ROSTER)
    assert sig is not None and sig["was_taker"] is True
    # non-roster owner, wrong topic0, zero amounts, short data
    assert cw.decode_fill_v2(_v2_log(owner=B), ROSTER) is None
    assert cw.decode_fill_v2(_v2_log(topic0="0x" + "00" * 32), ROSTER) is None
    assert cw.decode_fill_v2(_v2_log(tok=0), ROSTER) is None
    bad = _v2_log()
    bad["data"] = "0x" + _pad32(0)
    assert cw.decode_fill_v2(bad, ROSTER) is None


def test_side_from_receipt_logs():
    def t1155(frm, to, token=TOK):
        return {"address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",
                "topics": [cw.T1155_SINGLE, "0x" + "0" * 64,
                           "0x" + "0" * 24 + frm[2:].lower(),
                           "0x" + "0" * 24 + to[2:].lower()],
                "data": "0x" + _pad32(token) + _pad32(5_000_000)}

    def pusd(frm, to):
        return {"address": cw.PUSD_CONTRACT,
                "topics": [cw.T20_TRANSFER,
                           "0x" + "0" * 24 + frm[2:].lower(),
                           "0x" + "0" * 24 + to[2:].lower()],
                "data": "0x" + _pad32(1_000_000)}

    X = "0x" + "c" * 40
    assert cw.side_from_receipt_logs([t1155(X, A)], A, str(TOK)) == "BUY"
    assert cw.side_from_receipt_logs([t1155(A, X)], A, str(TOK)) == "SELL"
    # wrong token id -> no 1155 evidence; pUSD fallback decides
    assert cw.side_from_receipt_logs(
        [t1155(X, A, token=42), pusd(A, X)], A, str(TOK)) == "BUY"
    assert cw.side_from_receipt_logs(
        [t1155(X, A, token=42), pusd(X, A)], A, str(TOK)) == "SELL"
    # 1155 evidence beats the pUSD hint
    assert cw.side_from_receipt_logs(
        [pusd(X, A), t1155(X, A)], A, str(TOK)) == "BUY"
    assert cw.side_from_receipt_logs([], A, str(TOK)) is None


def test_hex_words_helpers():
    class HB:  # HexBytes-alike
        def __init__(self, s):
            self._s = s

        def hex(self):
            return self._s

    assert cw._hex(HB("0xab")) == "0xab" and cw._hex(HB("ab")) == "0xab"
    assert cw._hex("0xab") == "0xab"
    assert cw._words("0x" + _pad32(7) + _pad32(9)) == [7, 9]
    assert cw._topic_addr("0x" + "0" * 24 + "AB" * 20) == "0x" + "ab" * 20


def test_canary_state_alarm_and_recovery():
    # healthy: events seen, streak stays 0, no message
    assert cw.canary_state(0, 37) == (0, None)
    # first zero: streak 1, silent (single quiet window can happen)
    s, msg = cw.canary_state(0, 0)
    assert s == 1 and msg is None
    # second zero: alarm fires and keeps firing while blind
    s, msg = cw.canary_state(1, 0)
    assert s == 2 and "CANARY ALARM" in msg and "BLIND" in msg
    s, msg = cw.canary_state(2, 0)
    assert s == 3 and "CANARY ALARM (3x)" in msg
    # recovery after an alarm is announced
    s, msg = cw.canary_state(3, 12)
    assert s == 0 and "RECOVERED" in msg
    # recovery from a single silent zero is not announced
    assert cw.canary_state(1, 12) == (0, None)


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


# ── quote_book /price side mapping (REGRESSION — bug found 2026-07-13) ───────
# The CLOB /price endpoint's `side` names the BOOK SIDE read: side=BUY is the
# best BID, side=SELL is the best ASK (receipt-verified against /book on
# 31/31 live shadow records + live probe). The first deployment had it
# reversed: every record's bid/ask swapped, shadow fills quoted at the bid.
def test_quote_book_side_mapping_pins_endpoint_semantics():
    import asyncio

    class _Resp:
        def __init__(self, price):
            self._price = price

        async def json(self):
            return {"price": self._price}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        """side=BUY serves the bid (0.08), side=SELL serves the ask (0.09) —
        exactly what the live endpoint does."""
        def __init__(self):
            self.sides_called = []

        def get(self, url, params=None, timeout=None):
            self.sides_called.append(params["side"])
            return _Resp({"BUY": "0.08", "SELL": "0.09"}[params["side"]])

    s = _Session()
    bid, ask = asyncio.run(cw.quote_book(s, "123"))
    assert bid == 0.08, f"side=BUY must land in bid, got bid={bid}"
    assert ask == 0.09, f"side=SELL must land in ask, got ask={ask}"
    assert ask >= bid, "a correct mapping yields an uncrossed book"
    assert sorted(s.sides_called) == ["BUY", "SELL"]


def test_quote_book_error_isolation_per_side():
    import asyncio

    class _Boom:
        def get(self, url, params=None, timeout=None):
            if params["side"] == "SELL":
                raise RuntimeError("ask fetch died")

            class _R:
                async def json(self):
                    return {"price": "0.4"}

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False
            return _R()

    bid, ask = asyncio.run(cw.quote_book(_Boom(), "123"))
    assert bid == 0.4 and ask is None  # one side failing never poisons the other


# ── quote_sanity_msg (crossed-book structural guard) ─────────────────────────
def test_quote_sanity_crossed_book_alarms():
    msg = cw.quote_sanity_msg(0.90, 0.87)  # bid > ask = crossed
    assert msg is not None and "CROSSED" in msg


def test_quote_sanity_normal_and_partial_books_stay_silent():
    assert cw.quote_sanity_msg(0.87, 0.90) is None   # normal
    assert cw.quote_sanity_msg(0.90, 0.90) is None   # touching is legal
    assert cw.quote_sanity_msg(None, 0.90) is None   # one-sided: not provable
    assert cw.quote_sanity_msg(0.87, None) is None
    assert cw.quote_sanity_msg(None, None) is None


# ── rpc_call timeout guard (2026-07-16 hang-class landmine) ──────────────────
def test_rpc_call_times_out_a_parked_await():
    """A dead socket must RAISE, never park the poll loop forever — the exact
    silent-failure class that hung the deep-dive batch 13h on this endpoint.
    TimeoutError is a plain Exception, so every existing per-site try/except
    (head retry, window retry-don't-skip, canary count, receipt skip) absorbs
    it without behavior change."""
    import asyncio
    orig = cw.RPC_TIMEOUT_S
    cw.RPC_TIMEOUT_S = 0.05
    try:
        async def parked():
            await asyncio.sleep(60)

        async def main():
            try:
                await cw.rpc_call(parked())
                return "no-timeout"
            except TimeoutError:
                return "timed-out"
        assert asyncio.run(main()) == "timed-out"
        assert issubclass(TimeoutError, Exception)  # absorbed by except Exception
    finally:
        cw.RPC_TIMEOUT_S = orig


def test_rpc_call_passes_results_through():
    import asyncio

    async def quick():
        return 42

    async def main():
        return await cw.rpc_call(quick())
    assert asyncio.run(main()) == 42


# -- RTDS A/B consumer (2026-07-30) ------------------------------------------
def _rtds_frame(**kw):
    row = {"proxyWallet": "0xAbC0000000000000000000000000000000000001",
           "asset": "9" * 20, "side": "BUY", "price": 0.63, "size": 10.0,
           "timestamp": 1785368141, "transactionHash": "0xTT"}
    row.update(kw)
    return {"type": "trades", "payload": row, "timestamp": 1785368141}


def test_rtds_parse_trade_row():
    rows = cw.parse_rtds_trades(_rtds_frame())
    assert len(rows) == 1
    r = rows[0]
    assert r["trader"].startswith("0xabc")            # lower-cased
    assert r["side"] == "BUY" and r["price"] == 0.63 and r["size"] == 10.0
    assert r["trade_ts"] == 1785368141
    assert r["tx"] == "0xtt"                          # _hex lower-cases


def test_rtds_parse_rejects_non_trade_frames():
    assert cw.parse_rtds_trades({"statusCode": 200, "body": ""}) == []
    assert cw.parse_rtds_trades({"type": "book"}) == []
    assert cw.parse_rtds_trades(None) == []
    assert cw.parse_rtds_trades({"type": "trades", "payload": {"foo": 1}}) == []
    # bad numerics dropped, never raise
    assert cw.parse_rtds_trades(_rtds_frame(price="x")) == []


def test_rtds_parse_list_payload_and_ms_timestamps():
    f = {"type": "trades", "payload": [
        _rtds_frame()["payload"],
        {**_rtds_frame()["payload"], "timestamp": 1785368141000},  # ms
    ]}
    rows = cw.parse_rtds_trades(f)
    assert len(rows) == 2
    assert rows[0]["trade_ts"] == rows[1]["trade_ts"] == 1785368141


def test_rtds_sig_schema_matches_chain_sig():
    row = cw.parse_rtds_trades(_rtds_frame())[0]
    sig = cw.rtds_sig(row)
    # the fields the chain path guarantees before shadow_record
    for k in ("trader", "token_id", "whale_price", "whale_size_usd",
              "was_taker", "side"):
        assert k in sig
    assert sig["side"] == "BUY" and sig["source"] == "rtds"
    assert sig["whale_size_usd"] == round(0.63 * 10.0, 6)
    assert sig["was_taker"] is None  # unknowable from RTDS — disclosed


def test_rtds_config_gating():
    base = {"MIRROR3_ROSTER_PATH": "r.json", "MIRROR3_RPC_URL": "http://x"}
    # off by default
    assert cw.WatcherConfig.from_env(dict(base)).rtds_ab is False
    # on requires the url — refusing a silently-disabled A/B
    try:
        cw.WatcherConfig.from_env({**base, "MIRROR3_RTDS_AB": "1"})
        assert False, "expected ValueError"
    except ValueError:
        pass
    cfg = cw.WatcherConfig.from_env(
        {**base, "MIRROR3_RTDS_AB": "1", "RTDS_WS_URL": "wss://x"})
    assert cfg.rtds_ab is True and cfg.rtds_url == "wss://x"
    assert cfg.rtds_sink.endswith("mirror3_shadow_rtds.jsonl")


# -- $1.00-fill gate + RTDS keepalive (2026-08-19) ----------------------------
def test_max_fill_gate_blocks_no_upside_fills():
    # measured defect: 281/3257 OK first-buys filled at >=0.999 - deterministic
    # losers. whale 0.999 -> ask 1.000 passes the CHASE gate (0.001), so only
    # an absolute fill ceiling can catch it.
    v, f = cw.evaluate_gates(0.999, 0.999, 1.0, 0.02, 0.05, max_fill=0.98)
    assert v == "PRICE_NO_UPSIDE" and f is None
    v, f = cw.evaluate_gates(0.985, 0.98, 0.99, 0.02, 0.05, max_fill=0.98)
    assert v == "PRICE_NO_UPSIDE" and f is None
    # below the ceiling: unchanged behavior
    v, f = cw.evaluate_gates(0.96, 0.96, 0.97, 0.02, 0.05, max_fill=0.98)
    assert v == "OK" and f == 0.97


def test_max_fill_default_none_is_byte_identical():
    # omitted max_fill must reproduce the historical gate exactly - the fix is
    # forward-only via config, never a rewrite of the record
    cases = [(0.999, 0.999, 1.0), (0.5, 0.49, 0.51), (0.5, 0.4, 0.51),
             (0.5, None, None), (0.5, 0.5, 0.58)]
    for wp, bid, ask in cases:
        assert (cw.evaluate_gates(wp, bid, ask, 0.02, 0.05)
                == cw.evaluate_gates(wp, bid, ask, 0.02, 0.05, max_fill=None))
    # and a 1.00 fill with max_fill=None still returns OK (old behavior)
    assert cw.evaluate_gates(0.999, 0.999, 1.0, 0.02, 0.05)[0] == "OK"


def test_max_fill_config_env_and_default():
    base = {"MIRROR3_ROSTER_PATH": "r.json", "MIRROR3_RPC_URL": "http://x"}
    assert cw.WatcherConfig.from_env(dict(base)).max_fill == 0.98
    assert cw.WatcherConfig.from_env(
        {**base, "MIRROR3_MAX_FILL_C": "95"}).max_fill == 0.95


def test_rtds_pong_frames_are_not_trades():
    # the keepalive reply must never be parsed as data
    assert cw.parse_rtds_trades("PONG") == []
    assert cw.parse_rtds_trades("pong") == []
    assert cw.RTDS_APP_PING_S == 5.0  # reference-consumer interval (rtds_websocket.py:22)
