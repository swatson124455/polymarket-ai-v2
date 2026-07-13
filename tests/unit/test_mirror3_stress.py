"""Stress test — full pure-pipeline functionality of the V2 shadow watcher.

Exercises every offline stage the way a hostile chain would: burst volume,
malformed logs interleaved with real ones, split fills, duplicate wagers,
adversarial order books, flapping canaries, shuffled receipts. The network
edges (eth_getLogs, receipts, CLOB HTTP) are exercised on the VPS; every
decision the watcher makes between those edges is covered here.

Deterministic: seeded RNG (seed=20260713), no wall clock, no network.
Run: python3 -m pytest tests/unit/test_mirror3_stress.py --noconftest \
       --override-ini "addopts=" -q        (target: < 10s)

Invariants asserted (the contract of the pipeline):
  I1  decode never raises on arbitrary log dicts; accepts EXACTLY the
      well-formed roster fills, rejects everything else
  I2  merge_same_tx conserves total USD and volume-weights price per
      (tx, trader, token) — nothing created, nothing lost
  I3  FirstBuyDedup marks exactly one first per (trader, token)
  I4  conviction multiplier is always one of {1.0, 1.25, 1.5}; cold
      start (<20 obs) is always 1.0
  I5  gates return one of the 4 verdicts; a fill price exists iff OK
  I6  trim_book output is always sorted, capped at BOOK_DEPTH, or None;
      never raises on garbage
  I7  every shadow record is JSON-serializable and carries the full
      field set; JSONL round-trips
  I8  canary alarms iff >= CANARY_ALARM_AFTER consecutive zeros, and
      announces recovery only after having alarmed
  I9  direction: 1155 evidence for THE token beats pUSD hints; no
      evidence => None; never raises on shuffled/garbage receipts
  I10 end-to-end conservation: decoded BUY wagers == JSONL lines out
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from mirror_v3 import copy_watcher as cw  # noqa: E402
from mirror_v3.sizing import (  # noqa: E402
    TrailingMedians, conviction_multiplier, merge_same_tx)

SEED = 20260713
EX = "0xe111180000d2663c0091e4f400237545b87b996b"
ROSTER = [f"0x{i:040x}" for i in range(1, 17)]          # 16 synthetic traders
ROSTER_SET = set(ROSTER)
STRANGERS = [f"0x{i:040x}" for i in range(100, 120)]


def _pad(n: int) -> str:
    return f"{n:064x}"


def _addr_topic(a: str) -> str:
    return "0x" + "0" * 24 + a[2:].lower()


def good_log(rng, owner=None, tx=None, token=None, usdc=None, tok=None):
    owner = owner or rng.choice(ROSTER)
    token = token if token is not None else rng.randrange(10 ** 60, 10 ** 76)
    tok = tok if tok is not None else rng.randrange(1_000_000, 10 ** 12)
    usdc = usdc if usdc is not None else max(1, int(tok * rng.uniform(0.02, 0.98)))
    return {
        "address": EX,
        "topics": [cw.FILL_TOPIC_V2, "0x" + "ab" * 32,
                   _addr_topic(owner), _addr_topic(rng.choice(STRANGERS))],
        "data": "0x" + _pad(0) + _pad(token) + _pad(usdc) + _pad(tok)
                + _pad(0) * 3,
        "transactionHash": tx or ("0x" + rng.randbytes(32).hex()),
        "blockNumber": rng.randrange(90_000_000, 90_200_000),
    }


def bad_log(rng):
    """One of many malformation classes; must never decode nor raise."""
    kind = rng.randrange(7)
    lg = good_log(rng)
    if kind == 0:
        lg["topics"][0] = "0x" + "00" * 32           # wrong event
    elif kind == 1:
        lg["topics"][2] = _addr_topic(rng.choice(STRANGERS))  # not roster
    elif kind == 2:
        lg["data"] = "0x" + _pad(0)                   # short data
    elif kind == 3:
        lg["data"] = "0xzznotHEX"                     # unparseable data
        lg["topics"] = lg["topics"][:1]               # and missing topics
    elif kind == 4:
        lg["topics"] = []                             # no topics at all
    elif kind == 5:
        d = cw._words(lg["data"])
        lg["data"] = "0x" + _pad(0) + _pad(d[1]) + _pad(d[2]) + _pad(0) \
            + _pad(0) * 3                             # zero token amount
    else:
        lg = {"address": EX}                          # barely a log
    return lg


# ── I1: burst decode over hostile input ──────────────────────────────────────
def test_burst_decode_hostile_mix():
    rng = random.Random(SEED)
    n_good = 0
    logs = []
    for _ in range(5000):
        if rng.random() < 0.55:
            logs.append(good_log(rng))
            n_good += 1
        else:
            logs.append(bad_log(rng))
    decoded = []
    for lg in logs:
        try:
            sig = cw.decode_fill_v2(lg, ROSTER_SET)
        except Exception as e:  # pragma: no cover
            raise AssertionError(f"decode raised on {lg!r}: {e!r}")
        if sig:
            decoded.append(sig)
    assert len(decoded) == n_good
    for s in decoded:
        assert s["trader"] in ROSTER_SET
        assert s["whale_size_usd"] > 0
        assert 0 < s["whale_price"]
        assert s["token_id"].isdigit()


# ── I2: merge conservation under split-fill storms ───────────────────────────
def test_merge_same_tx_conserves_usd_and_weights_price():
    rng = random.Random(SEED + 1)
    sigs, expect = [], {}
    for _ in range(400):
        tx = "0x" + rng.randbytes(8).hex()
        trader = rng.choice(ROSTER)
        token = str(rng.randrange(10 ** 60, 10 ** 76))
        for _ in range(rng.randrange(1, 8)):          # up to 7 partial fills
            lg = good_log(rng, owner=trader, tx=tx, token=int(token))
            s = cw.decode_fill_v2(lg, ROSTER_SET)
            s["tx"] = tx
            sigs.append(s)
            k = (tx, trader, token)
            usd, tok = expect.get(k, (0.0, 0.0))
            expect[k] = (usd + s["whale_size_usd"],
                         tok + s["whale_size_usd"] / s["whale_price"])
    rng.shuffle(sigs)
    merged = merge_same_tx(sigs)
    assert len(merged) == len(expect)
    for m in merged:
        usd, tok = expect[(m["tx"], m["trader"], m["token_id"])]
        assert abs(m["whale_size_usd"] - usd) < 1e-6 * max(usd, 1)
        assert abs(m["whale_price"] - usd / tok) < 1e-9 + 1e-6 * (usd / tok)


# ── I3: dedup exactness at volume ────────────────────────────────────────────
def test_first_buy_dedup_exactly_one_first_per_pair():
    rng = random.Random(SEED + 2)
    pairs = [(rng.choice(ROSTER), str(rng.randrange(10 ** 8)))
             for _ in range(2000)]
    d = cw.FirstBuyDedup()
    firsts = sum(d.is_first(t, k) for t, k in pairs)
    assert firsts == len(set(pairs))
    assert all(not d.is_first(t, k) for t, k in set(pairs))  # all seen now


# ── I4: conviction multiplier bounds under extreme wagers ────────────────────
def test_conviction_multiplier_bounds_and_cold_start():
    rng = random.Random(SEED + 3)
    tm = TrailingMedians()
    trader = ROSTER[0]
    for i in range(200):
        wager = rng.choice([0.01, 1.0, 50.0, 10_000.0, 1e12])
        med, n = tm.stats(trader)
        mult, r = conviction_multiplier(wager, med, n)
        assert mult in (1.0, 1.25, 1.5)
        if n < 20:
            assert mult == 1.0                        # cold start locked
        tm.observe(trader, wager)
    # the real first record's extreme: r≈67x must cap at 1.5, never above
    mult, r = conviction_multiplier(67.78 * 100, 100.0, 50)
    assert mult == 1.5 and r > 4


# ── I5: gate fuzz — verdict domain + fill iff OK ─────────────────────────────
def test_gates_fuzz_verdict_domain():
    rng = random.Random(SEED + 4)
    verdicts = set()
    for _ in range(4000):
        whale = rng.uniform(0.01, 0.99)
        bid = rng.choice([None, 0.0, -1.0, rng.uniform(0.001, 0.999)])
        ask = rng.choice([None, 0.0, -1.0, rng.uniform(0.001, 0.999)])
        v, fill = cw.evaluate_gates(whale, bid, ask,
                                    max_chase=0.02, max_spread=0.05)
        verdicts.add(v)
        assert v in ("OK", "NO_BOOK", "SPREAD_TOO_WIDE", "PRICE_RAN_AWAY")
        assert (fill is not None) == (v == "OK")
        if v == "OK":
            assert fill == ask
    assert verdicts == {"OK", "NO_BOOK", "SPREAD_TOO_WIDE", "PRICE_RAN_AWAY"}


# ── I6: adversarial order books ──────────────────────────────────────────────
def test_trim_book_adversarial():
    rng = random.Random(SEED + 5)
    huge = {"asks": [{"price": str(rng.uniform(0.01, 0.99)),
                      "size": str(rng.uniform(0.1, 9999))}
                     for _ in range(10_000)],
            "bids": [{"price": rng.uniform(0.01, 0.99),
                      "size": rng.uniform(0.1, 9999)}
                     for _ in range(10_000)]}
    b = cw.trim_book(huge)
    assert len(b["asks"]) == cw.BOOK_DEPTH and len(b["bids"]) == cw.BOOK_DEPTH
    assert b["asks"] == sorted(b["asks"], key=lambda x: x["price"])
    assert b["bids"] == sorted(b["bids"], key=lambda x: -x["price"])
    for garbage in (None, 42, "book", [], {"asks": "no"}, {"asks": [{}]},
                    {"asks": [{"price": "-1", "size": "5"}], "bids": []},
                    {"asks": [{"price": "0.5", "size": "nan-ish"}]}):
        try:
            out = cw.trim_book(garbage)
        except Exception as e:  # pragma: no cover
            raise AssertionError(f"trim_book raised on {garbage!r}: {e!r}")
        assert out is None or isinstance(out, dict)


# ── I8: canary state machine under flapping ──────────────────────────────────
def test_canary_flapping_invariants():
    rng = random.Random(SEED + 6)
    streak, alarmed_ever = 0, False
    zeros_in_a_row = 0
    for _ in range(3000):
        n = rng.choice([0, 0, 1, 40, 25055])
        prev_streak = streak
        streak, msg = cw.canary_state(streak, n)
        zeros_in_a_row = zeros_in_a_row + 1 if n == 0 else 0
        assert streak == zeros_in_a_row
        if msg and "ALARM" in msg:
            alarmed_ever = True
            assert zeros_in_a_row >= cw.CANARY_ALARM_AFTER
        if msg and "RECOVERED" in msg:
            assert n > 0 and prev_streak >= cw.CANARY_ALARM_AFTER
        if n > 0 and prev_streak < cw.CANARY_ALARM_AFTER:
            assert msg is None                        # silent healthy path
    assert alarmed_ever                               # the fuzz hit the alarm


# ── I9: direction from shuffled hostile receipts ─────────────────────────────
def test_side_inference_shuffled_receipts():
    rng = random.Random(SEED + 7)
    trader = ROSTER[0]
    other = STRANGERS[0]
    tid = 10 ** 70 + 7

    def t1155(frm, to, token):
        return {"address": "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",
                "topics": [cw.T1155_SINGLE, "0x" + "0" * 64,
                           _addr_topic(frm), _addr_topic(to)],
                "data": "0x" + _pad(token) + _pad(5_000_000)}

    def pusd(frm, to):
        return {"address": cw.PUSD_CONTRACT,
                "topics": [cw.T20_TRANSFER, _addr_topic(frm),
                           _addr_topic(to)],
                "data": "0x" + _pad(1_000_000)}

    for _ in range(300):
        # noise: transfers of OTHER tokens + pUSD both directions + garbage
        logs = [t1155(other, trader, tid + 5), t1155(trader, other, tid + 9),
                pusd(other, trader), {"address": EX, "topics": []},
                {"topics": ["0xdead"]}]
        truth = rng.choice(["BUY", "SELL", None])
        if truth == "BUY":
            logs.append(t1155(other, trader, tid))
        elif truth == "SELL":
            logs.append(t1155(trader, other, tid))
        rng.shuffle(logs)
        got = cw.side_from_receipt_logs(logs, trader, str(tid))
        if truth is None:
            # no 1155 for THE token; pUSD hint (trader received) says SELL
            assert got == "SELL"
        else:
            assert got == truth


# ── I7 + I10: end-to-end pure pipeline, conservation + serialization ─────────
def test_end_to_end_pipeline_conservation():
    rng = random.Random(SEED + 8)
    windows = []
    n_good_logs = 0
    for _ in range(50):                               # 50 poll windows
        w = []
        for _ in range(rng.randrange(0, 60)):
            if rng.random() < 0.4:
                w.append(good_log(rng))
                n_good_logs += 1
            else:
                w.append(bad_log(rng))
        windows.append(w)

    tm = TrailingMedians()
    dedup = cw.FirstBuyDedup()
    jsonl: list[str] = []
    decoded_wagers = 0
    for w in windows:
        decoded = []
        for lg in w:
            sig = cw.decode_fill_v2(dict(lg), ROSTER_SET)
            if sig is None:
                continue
            sig["tx"] = cw._hex(lg.get("transactionHash", "0x00"))
            decoded.append(sig)
        for sig in merge_same_tx(decoded):
            decoded_wagers += 1
            sig["side"] = "BUY"                       # receipt says BUY
            sig["first_buy"] = dedup.is_first(sig["trader"], sig["token_id"])
            med, n = tm.stats(sig["trader"])
            mult, r = conviction_multiplier(sig["whale_size_usd"], med, n)
            tm.observe(sig["trader"], sig["whale_size_usd"])
            sig["trailing_median_usd"] = med
            sig["conviction_r"] = round(r, 4) if r is not None else None
            sig["size_multiplier"] = mult
            bid = rng.choice([None, rng.uniform(0.01, 0.98)])
            ask = rng.choice([None, rng.uniform(0.01, 0.98)])
            verdict, fill = cw.evaluate_gates(sig["whale_price"], bid, ask,
                                              0.02, 0.05)
            book = cw.trim_book({"asks": [{"price": ask or 0.5, "size": 10}],
                                 "bids": [{"price": bid or 0.4, "size": 10}]})
            rec = cw.shadow_record(sig, verdict, fill, bid, ask,
                                   block_ts=1_752_000_000,
                                   now_ts=1_752_000_003.1,
                                   tx=sig.pop("tx", ""), book=book)
            jsonl.append(json.dumps(rec))

    # I10: every merged BUY wager produced exactly one JSONL line
    assert len(jsonl) == decoded_wagers and decoded_wagers > 100
    # I7: every line round-trips with the full field contract
    required = {"trader", "token_id", "side", "whale_price", "whale_size_usd",
                "verdict", "shadow_fill", "best_bid", "best_ask", "book_asks",
                "book_bids", "block_ts", "detect_ts", "detect_lag_s", "tx",
                "first_buy", "conviction_r", "size_multiplier",
                "trailing_median_usd", "was_taker"}
    for line in jsonl:
        rec = json.loads(line)
        assert required <= set(rec)
        assert rec["side"] == "BUY"
        assert rec["size_multiplier"] in (1.0, 1.25, 1.5)
        assert (rec["shadow_fill"] is not None) == (rec["verdict"] == "OK")
    # I3 within the pipeline: firsts equal distinct (trader, token) pairs
    firsts = sum(json.loads(x)["first_buy"] for x in jsonl)
    distinct = len({(json.loads(x)["trader"], json.loads(x)["token_id"])
                    for x in jsonl})
    assert firsts == distinct
