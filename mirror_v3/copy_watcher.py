"""Copy-trade shadow watcher — on-chain detection of the chain-verified
roster's fills + pre-trade gates + shadow fill quoting. NO ORDERS.

WHY THIS EXISTS (2026-07-11): the walk-forward grader PASSED (edge exists:
+0.0237, P=1.000 on 19,281 mkts) and the per-fill chain audit verified 16
of 29 rostered traders CLEAN. The last question — does the edge survive
real spreads at our latency — is UNANSWERABLE retrospectively (fill gate:
0% orderbook_snapshots coverage; the roster trades outside the bots' old
snapshot universe). This module generates that missing dataset FORWARD, at
$0 risk: it detects roster fills on-chain (~2-3s vs ~10s via the REST
data-api), applies the pre-trade gates a live copier would apply, quotes
the REAL current ask as our hypothetical fill, and appends one JSONL
record per signal. The shadow log IS the fill-quality dataset.

DETECTION (REWORKED 2026-07-12 — V2 exchanges): trading migrated off the
V1 exchanges (WI-24, on-chain verified 2026-06-11); the original V1
OrderFilled watch was blind from its first minute (33h, zero records,
while the roster made 179 BUYs — receipt-traced). Fills now emit an
UNNAMED event (topic0 FILL_TOPIC_V2, layout reverse-engineered from
known trades via scripts/decode_v2_fill.py and validated against API
ground truth: price matched to 4 decimals) on the V2 exchanges:
  topics: [FILL_TOPIC_V2, orderHash, ORDER OWNER, counterparty-or-exchange]
  data:   [?, ctf_token_id, usdc_amount, token_amount, 0, 0, 0]  (6-dec)
The poll is ONE raw eth_getLogs per window: address=[both V2 exchanges],
topics=[FILL_TOPIC_V2, None, <roster as topic-2 list>]. BUY/SELL is NOT
in the event (seller legs have identical shape — proven); direction
comes from the tx receipt's token-transfer logs (outcome tokens TO the
trader = BUY; pUSD paid BY the trader corroborates), fetched only on
roster hits. Poll floor ~POLL_S + block time => ~3-4s detection lag,
measured and recorded per signal (detect_lag_s).

GATES (per signal, all recorded, none fatal to the run):
  NO_BOOK          could not quote a book (CLOB /price timeout/empty)
  SPREAD_TOO_WIDE  ask - bid > max_spread
  PRICE_RAN_AWAY   ask > whale_price + max_chase (the edge already left)
  PRICE_NO_UPSIDE  ask > max_fill (2026-08-19: 8.6% of OK first-buys filled
                   at >=0.999 - zero upside, full downside; the chase gate
                   cannot catch a 0.999->1.000 copy)
  OK               shadow-filled at the current ask (our copy price)

LADDER CAPTURE (2026-07-12, additive): each record also carries the top
BOOK_DEPTH levels of the CLOB /book ladder (book_asks/book_bids) so the
readout can price AT-SIZE fills with the PRECISE VWAP walk
(bots/mirror_backtest/fill_models.py) instead of assuming the whole copy
fills at best ask. Fail-soft: a /book error records null ladders and does
NOT change any gate verdict — gating stays on /price exactly as deployed.

SAFETY: read-only everywhere — RPC GETs, CLOB public GETs, JSONL append.
No orders (paper or live), no DB writes, no shared-module imports beyond
the ABI/contract constants. Runs inside mirror_v3's env-guarded process.

ENV (explicit when enabled; the v3 "unset is an error" rule):
  MIRROR3_COPY_WATCHER  'true' to start (run.py; default absent = off)
  MIRROR3_ROSTER_PATH   audit JSON with {"clean": [addresses...]}
  MIRROR3_RPC_URL       Polygon RPC serving eth_getLogs (probe-verified:
                        https://polygon.gateway.tenderly.co; publicnode 403s)
  MIRROR3_SHADOW_PATH   JSONL sink (default /opt/pa2-shared/mirror3_shadow.jsonl)
  MIRROR3_MAX_CHASE_C   gate: max cents over whale price (default 2)
  MIRROR3_MAX_SPREAD_C  gate: max spread in cents (default 5)
  MIRROR3_MAX_FILL_C    gate: max shadow fill price in cents (default 98 -
                        the zero-upside bound at the flat 2% fee)
  MIRROR3_POLL_S        poll interval seconds (default 2)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

USDC_ASSET_ID = 0
SCALE = 1e6  # USDC + CTF outcome tokens are 6-decimals
CLOB_PRICE_URL = "https://clob.polymarket.com/price"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
BOOK_DEPTH = 20  # ladder levels kept per side (max copy is ~1.5 units — ample)
GETLOGS_CHUNK = 900  # public-RPC eth_getLogs range cap (audit-proven)
MAX_WINDOW_RETRIES = 5  # same-window failures before a LOUD skip
# Blind-RPC canary (2026-07-12): Tenderly's gateway answered eth_getLogs
# with [] instead of data for 33h — no errors, zero detections, invisible
# to retry-don't-skip. The canary periodically counts UNFILTERED
# OrderFilled events in a settled window; Polymarket fills constantly, so
# repeated zeros mean the RPC serves no logs and detection is blind.
CANARY_EVERY_S = 600       # one canary query cycle per 10 min
CANARY_SETTLE_BLOCKS = 60  # window ends this far behind head (settled)
CANARY_SPAN = 20           # window size in blocks (~42s of chain). Measured
                           # 2026-07-12: ~83 V2 fills/block — 300 blocks
                           # pulled ~25k full logs per cycle (~20s, ~12MB)
                           # for a yes/no question 20 blocks answer fine
CANARY_ALARM_AFTER = 2     # consecutive zero cycles before the LOUD alarm

# ── V2 exchange constants (2026-07-12; see DETECTION in the docstring) ───────
# Main V2: base_engine/execution/contract_manager.py:34. NegRisk V2: resolved
# from py_clob_client_v2 on the VPS (scripts/rpc_logs_probe.find_negrisk_v2).
# Kept LOCAL to mirror_v3 on purpose — blockchain_client's V1 constants serve
# historical queries (the audit) and must not change under other consumers.
EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEGRISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
# The V2 fill event's topic0 (unnamed; receipt-traced 2026-07-12, one event
# per matched order, layout validated against data-api ground truth)
FILL_TOPIC_V2 = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
# Direction evidence in the tx receipt (standard signatures)
T1155_SINGLE = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
T1155_BATCH = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
T20_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
PUSD_CONTRACT = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"  # V2 trading collateral


# ── Pure, offline-testable core ──────────────────────────────────────────────
def load_roster(path: str) -> list[str]:
    """CLEAN roster from audit_roster_chain.py output. Empty/invalid is a
    hard error — a copy watcher with nobody to watch is a misconfiguration,
    never a silent no-op."""
    with open(path) as f:
        blob = json.load(f)
    roster = [str(a).lower() for a in blob.get("clean", [])
              if str(a).lower().startswith("0x") and len(str(a)) == 42]
    if not roster:
        raise ValueError(f"no valid CLEAN addresses in {path}")
    return roster


def addr_topic(addr: str) -> str:
    """0x + 24 zero chars + 40 addr chars — address as a 32-byte log topic."""
    return "0x" + "0" * 24 + addr.lower().replace("0x", "")


def decode_fill(args: dict, roster: set[str]) -> Optional[dict]:
    """OrderFilled args -> copy signal, or None if not a roster BUY.

    Same asset semantics as the (chain-verified) audit matcher, inverted to
    INFER token+side from the event instead of matching a known fill:
      roster is maker: makerAssetId==USDC -> maker pays USDC = BUY takerAssetId
      roster is taker: takerAssetId==USDC -> taker pays USDC = BUY makerAssetId
    SELLs are ignored — the graded estimand is first BUY per market."""
    mk = str(args.get("maker", "")).lower()
    tk = str(args.get("taker", "")).lower()
    m_id = int(args.get("makerAssetId", -1))
    t_id = int(args.get("takerAssetId", -1))
    m_amt = float(args.get("makerAmountFilled", 0))
    t_amt = float(args.get("takerAmountFilled", 0))

    if mk in roster and m_id == USDC_ASSET_ID and t_id != USDC_ASSET_ID:
        trader, token, usdc, tok = mk, t_id, m_amt, t_amt
    elif tk in roster and t_id == USDC_ASSET_ID and m_id != USDC_ASSET_ID:
        trader, token, usdc, tok = tk, m_id, t_amt, m_amt
    else:
        return None
    if tok <= 0 or usdc <= 0:
        return None
    return {
        "trader": trader,
        "token_id": str(token),
        "side": "BUY",
        "whale_price": (usdc / SCALE) / (tok / SCALE),
        "whale_size_usd": usdc / SCALE,
    }


def _hex(x: Any) -> str:
    """Normalize a topic/data value (HexBytes | bytes | str) to 0x-hex."""
    if hasattr(x, "hex"):
        s = x.hex()
    else:
        s = str(x)
    return s if s.startswith("0x") else "0x" + s


def _words(data: Any) -> list[int]:
    h = _hex(data)[2:]
    return [int(h[i:i + 64], 16) for i in range(0, len(h) - 63, 64)]


def _topic_addr(topic: Any) -> str:
    return "0x" + _hex(topic)[-40:].lower()


def decode_fill_v2(lg: dict, roster: set[str]) -> Optional[dict]:
    """Raw V2 fill log -> copy signal, or None if not a roster order.

    Layout (empirically validated, scripts/decode_v2_fill.py 2026-07-12):
      topics[2] = order owner (server-side filtered to the roster)
      data[1]   = ctf token id; data[2] = usdc*1e6; data[3] = tokens*1e6
      price = usdc/tokens (matched API ground truth to 4 decimals)
    Direction is NOT here — side_from_receipt_logs() supplies it."""
    topics = lg.get("topics") or []
    if len(topics) < 3 or _hex(topics[0]).lower() != FILL_TOPIC_V2:
        return None
    trader = _topic_addr(topics[2])
    if trader not in roster:
        return None
    w = _words(lg.get("data", "0x"))
    if len(w) < 4:
        return None
    token, usdc, tok = w[1], w[2], w[3]
    if tok <= 0 or usdc <= 0:
        return None
    was_taker = (len(topics) > 3
                 and _topic_addr(topics[3]) == str(lg.get("address", "")).lower())
    return {
        "trader": trader,
        "token_id": str(token),
        "whale_price": (usdc / SCALE) / (tok / SCALE),
        "whale_size_usd": usdc / SCALE,
        "was_taker": was_taker,
    }


def side_from_receipt_logs(logs: list, trader: str,
                           token_id: str) -> Optional[str]:
    """BUY | SELL | None from a tx receipt's transfer logs.

    Primary: ERC-1155 movement of THIS token id relative to the trader
    (to trader = BUY — covers both direct transfers and mint/split legs;
    from trader = SELL). Fallback: pUSD flow (trader pays = BUY)."""
    tid = int(token_id)
    pusd_hint: Optional[str] = None
    a = trader.lower()
    for lg in logs:
        topics = lg.get("topics") or []
        if not topics:
            continue
        t0 = _hex(topics[0]).lower()
        addr = str(lg.get("address", "")).lower()
        if t0 in (T1155_SINGLE, T1155_BATCH) and len(topics) >= 4:
            if tid not in _words(lg.get("data", "0x")):
                continue
            if _topic_addr(topics[3]) == a:
                return "BUY"
            if _topic_addr(topics[2]) == a:
                return "SELL"
        elif t0 == T20_TRANSFER and addr == PUSD_CONTRACT and len(topics) >= 3:
            if _topic_addr(topics[1]) == a:
                pusd_hint = pusd_hint or "BUY"
            elif _topic_addr(topics[2]) == a:
                pusd_hint = pusd_hint or "SELL"
    return pusd_hint


def evaluate_gates(whale_price: float, best_bid: Optional[float],
                   best_ask: Optional[float], max_chase: float,
                   max_spread: float,
                   max_fill: Optional[float] = None) -> tuple[str, Optional[float]]:
    """(verdict, shadow_fill_price). OK => we'd cross at the ask; every
    other verdict is a skip whose cost is zero by construction.

    max_fill (2026-08-19, operator-approved defect fix): a hard ceiling on
    the price we will shadow-pay. Measured over the full sink: 281/3,257 OK
    first-buys (8.6%) filled at >= 0.999 - ZERO upside, ~full downside, a
    deterministic loser after fees. The chase gate cannot catch it: whale at
    0.999 -> ask 1.000 is a 0.001 chase, "fine". The 0.98 default is the
    economic zero-upside bound at the flat 2% fee (1 - 1.02p <= 0 for
    p >= 0.9804). None (the default) preserves the old behavior exactly -
    existing callers and the historical record are untouched; the fix is
    FORWARD-ONLY via the config."""
    if best_ask is None or best_ask <= 0:
        return "NO_BOOK", None
    if max_fill is not None and best_ask > max_fill:
        return "PRICE_NO_UPSIDE", None
    if best_bid is not None and (best_ask - best_bid) > max_spread:
        return "SPREAD_TOO_WIDE", None
    if best_ask > whale_price + max_chase:
        return "PRICE_RAN_AWAY", None
    return "OK", best_ask


def trim_book(raw: Any, depth: int = BOOK_DEPTH) -> Optional[dict]:
    """CLOB /book JSON -> {"asks": [{price, size}, ...], "bids": [...]} with
    asks ascending / bids descending by price, truncated to `depth` levels.
    Sorted here — the API's level ordering is NOT relied upon. Levels that
    fail float coercion are dropped. None when no side has a valid level
    (record null rather than an empty ladder that looks measured)."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, list[dict]] = {}
    for side in ("asks", "bids"):
        levels = []
        for lvl in raw.get(side) or []:
            try:
                p, s = float(lvl["price"]), float(lvl["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if p > 0 and s > 0:
                levels.append({"price": p, "size": s})
        levels.sort(key=lambda x: x["price"], reverse=(side == "bids"))
        out[side] = levels[:depth]
    if not out["asks"] and not out["bids"]:
        return None
    return out


def block_chunks(b0: int, b1: int, chunk: int = GETLOGS_CHUNK) -> list[tuple[int, int]]:
    spans, lo = [], b0
    while lo <= b1:
        hi = min(lo + chunk - 1, b1)
        spans.append((lo, hi))
        lo = hi + 1
    return spans


RPC_TIMEOUT_S = 90.0


async def rpc_call(coro):
    """Timeout guard on EVERY web3 RPC await in the watcher. 2026-07-16
    landmine: an RPC await with no read-timeout on a dropped connection parks
    the coroutine FOREVER with zero CPU and zero sockets — the deep-dive batch
    hung 13h this way, and this watcher shares the library, the endpoint, and
    the vulnerability. A parked poll loop is the worst silent failure this
    instrument can have: systemd stays 'active', the Database heartbeat task
    keeps logging, and even the blind-RPC canary cannot fire because the loop
    itself is stuck. The timeout turns a hang into a normal exception that the
    existing per-site try/excepts already absorb (head-fetch retry, window
    retry-don't-skip, canary error count, receipt skip, fail-loud startup)."""
    async with asyncio.timeout(RPC_TIMEOUT_S):
        return await coro


async def get_logs_compat(event, b0: int, b1: int, filters: dict):
    """web3 v7 snake_case first, pre-v7 camelCase fallback (the 2026-07-10
    audit failure class — kept identical to the audited helper)."""
    try:
        return await event.get_logs(argument_filters=filters,
                                    from_block=b0, to_block=b1)
    except TypeError:
        return await event.get_logs(argument_filters=filters,
                                    fromBlock=b0, toBlock=b1)


@dataclass
class WatcherConfig:
    roster_path: str
    rpc_url: str
    shadow_path: str = "/opt/pa2-shared/mirror3_shadow.jsonl"
    median_cache: str = "/opt/pa2-shared/mb_copyable_data/copyable_cache"
    max_chase: float = 0.02
    max_spread: float = 0.05
    max_fill: float = 0.98  # $1.00-fill defect fix (see evaluate_gates)
    poll_s: float = 2.0
    # RTDS A/B consumer (2026-07-30, operator-approved latency build): OFF
    # unless MIRROR3_RTDS_AB=1 AND RTDS_WS_URL is set. Writes to its OWN sink
    # (rtds_sink), never shadow_path — the chain poll stays the canonical
    # stream, so enabling this cannot change any existing readout.
    rtds_url: str = ""
    rtds_sink: str = "/opt/pa2-shared/mirror3_shadow_rtds.jsonl"
    rtds_ab: bool = False
    # shadow-bid simulator (2026-08-19, docs/BIDSIM_DESIGN.md): band-only
    # maker-execution measurement. OFF unless MIRROR3_BIDSIM=1.
    bidsim: bool = False
    bidsim_path: str = "/opt/pa2-shared/mirror3_bidsim.jsonl"
    # GO-precondition #4 (operator "build all 5", 2026-09-06): roster SELLs
    # recorded to their OWN sink for the future with-exits estimand. A
    # separate file by design - the graded BUY pipeline and its consumers
    # (all-BUY sink assumption) are never touched.
    sell_sink: str = "/opt/pa2-shared/mirror3_shadow_sells.jsonl"

    @classmethod
    def from_env(cls, env) -> "WatcherConfig":
        missing = [k for k in ("MIRROR3_ROSTER_PATH", "MIRROR3_RPC_URL")
                   if not str(env.get(k, "")).strip()]
        if missing:
            raise ValueError(
                f"copy watcher enabled but required env missing: {missing} "
                f"(v3 rule: explicit presence, no code defaults for wiring)")
        rtds_ab = str(env.get("MIRROR3_RTDS_AB", "")).strip() == "1"
        rtds_url = str(env.get("RTDS_WS_URL", "")).strip()
        if rtds_ab and not rtds_url:
            raise ValueError(
                "MIRROR3_RTDS_AB=1 but RTDS_WS_URL is empty — refusing a "
                "silently-disabled A/B (explicit presence rule)")
        return cls(
            roster_path=env["MIRROR3_ROSTER_PATH"],
            rpc_url=env["MIRROR3_RPC_URL"],
            shadow_path=env.get("MIRROR3_SHADOW_PATH",
                                "/opt/pa2-shared/mirror3_shadow.jsonl"),
            median_cache=env.get(
                "MIRROR3_MEDIAN_CACHE",
                "/opt/pa2-shared/mb_copyable_data/copyable_cache"),
            max_chase=float(env.get("MIRROR3_MAX_CHASE_C", "2")) / 100.0,
            max_spread=float(env.get("MIRROR3_MAX_SPREAD_C", "5")) / 100.0,
            max_fill=float(env.get("MIRROR3_MAX_FILL_C", "98")) / 100.0,
            poll_s=float(env.get("MIRROR3_POLL_S", "2")),
            rtds_url=rtds_url,
            rtds_sink=env.get("MIRROR3_RTDS_SHADOW_PATH",
                              "/opt/pa2-shared/mirror3_shadow_rtds.jsonl"),
            rtds_ab=rtds_ab,
            bidsim=str(env.get("MIRROR3_BIDSIM", "")).strip() == "1",
            bidsim_path=env.get("MIRROR3_BIDSIM_PATH",
                                "/opt/pa2-shared/mirror3_bidsim.jsonl"),
            sell_sink=env.get("MIRROR3_SELL_SINK",
                              "/opt/pa2-shared/mirror3_shadow_sells.jsonl"),
        )


def sell_record(sig: dict, now: float) -> dict:
    """Minimal SELL record (GO-precondition #4, 2026-09-06). Pure - the
    watch loop serializes it to cfg.sell_sink. No quotes, no gates: this
    is raw material for the pre-registered with-exits estimand, not a
    graded signal."""
    return {"trader": sig["trader"], "token_id": sig["token_id"],
            "side": "SELL", "whale_price": sig["whale_price"],
            "whale_size_usd": sig["whale_size_usd"],
            "tx": sig.get("tx", ""), "detect_ts": now}


def shadow_record(sig: dict, verdict: str, fill: Optional[float],
                  best_bid: Optional[float], best_ask: Optional[float],
                  block_ts: int, now_ts: float, tx: str,
                  book: Optional[dict] = None,
                  quote_ts: Optional[float] = None) -> dict:
    # quote_ts (2026-08-25, operator-approved rec): the moment the /price
    # quote was actually taken. detect_ts is stamped BEFORE the per-signal
    # receipt/block RPC work, so quote_ts - detect_ts measures the fill
    # quote's staleness relative to detection - previously unmeasurable
    # (hygiene-review finding: the latency tail is recorder burst-queueing).
    return {
        **sig,
        "verdict": verdict,
        "shadow_fill": fill,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "book_asks": book["asks"] if book else None,
        "book_bids": book["bids"] if book else None,
        "block_ts": block_ts,
        "detect_ts": round(now_ts, 3),
        "detect_lag_s": round(now_ts - block_ts, 3),
        "quote_ts": round(quote_ts, 3) if quote_ts is not None else None,
        "quote_lag_s": (round(quote_ts - now_ts, 3)
                        if quote_ts is not None else None),
        "tx": tx,
    }


def quote_sanity_msg(bid: Optional[float],
                     ask: Optional[float]) -> Optional[str]:
    """LOUD message if /price returns a crossed book (ask < bid), else None.

    A persistently crossed book from /price means the endpoint's side
    semantics changed (again) or the quotes are garbage — either way the
    labels can't be trusted and the fix is verification, not use. This is
    the structural guard for the exact failure class found 2026-07-13
    (mapping reversed => every book 'crossed'); same philosophy as the RPC
    canary: an instrument must be able to notice it has gone blind."""
    if bid is None or ask is None or ask >= bid:
        return None
    return (f"QUOTE SANITY ALARM: /price returned a CROSSED book "
            f"(bid={bid} > ask={ask}) — side semantics may have changed; "
            f"run scripts/verify_clob_price_sides.py before trusting "
            f"any new record")


def canary_state(zero_streak: int, n_events: int) -> tuple[int, Optional[str]]:
    """Fold one canary observation into the zero-streak. Returns the new
    streak and a message to log (alarm on the threshold crossing and on
    every cycle while blind; recovery notice when events reappear)."""
    if n_events > 0:
        msg = ("canary RECOVERED: RPC serves logs again "
               f"({n_events} events in settled window)") if \
            zero_streak >= CANARY_ALARM_AFTER else None
        return 0, msg
    streak = zero_streak + 1
    if streak >= CANARY_ALARM_AFTER:
        return streak, (f"CANARY ALARM ({streak}x): 0 OrderFilled events in a "
                        f"settled {CANARY_SPAN}-block window — Polymarket "
                        "never sleeps that long. The RPC is serving empty "
                        "logs; DETECTION IS BLIND. Change MIRROR3_RPC_URL.")
    return streak, None


def seed_firstbuy(dedup: "FirstBuyDedup", sink_path: str) -> int:
    """Rehydrate first-buy dedup from the sink (2026-08-25, operator-approved
    fix for the restart artifact: 924 excess first-buy flags / 16.4% of
    first-buy records were duplicates from memory-only dedup). Marks every
    (trader, token) pair ever recorded so a restart can never re-flag a
    repeat as first. Returns pairs seeded; missing sink = 0 (fresh box)."""
    if not sink_path or not os.path.exists(sink_path):
        return 0
    n = 0
    with open(sink_path, errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t, tok = r.get("trader"), r.get("token_id")
            if t and tok and dedup.is_first(str(t), str(tok)):
                n += 1
    return n


class FirstBuyDedup:
    """The graded estimand is FIRST BUY per (trader, token); repeat fills
    (multi-fill orders, later adds) are recorded but marked duplicate so
    the analysis can use exactly the walk-forward's estimand."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def is_first(self, trader: str, token_id: str) -> bool:
        key = (trader, token_id)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


# ── Network runner (VPS; everything above is testable offline) ──────────────
async def quote_book(session, token_id: str,
                     timeout_s: float = 2.0
                     ) -> tuple[Optional[float], Optional[float], bool]:
    """(best_bid, best_ask) from the public CLOB /price endpoint.

    /price's `side` names the BOOK SIDE being read: side=BUY returns the
    best bid, side=SELL returns the best ask. Receipt-verified 2026-07-13
    two ways (31/31 shadow-record ladders + live /book probe; pinned by
    scripts/verify_clob_price_sides.py). The first deployment had this
    mapping REVERSED — every record's bid/ask were swapped and shadow
    fills quoted the bid, flattering edge by the spread."""
    bid = ask = None
    quote_err = False
    for side, out in (("BUY", "bid"), ("SELL", "ask")):
        try:
            async with session.get(
                    CLOB_PRICE_URL,
                    params={"token_id": token_id, "side": side},
                    timeout=timeout_s) as r:
                px = float((await r.json()).get("price", 0)) or None
        except Exception:
            # 2026-08-25 (hygiene review, operator-approved): a transport/
            # endpoint failure must be DISTINGUISHABLE from a genuinely empty
            # book - outage-correlated NO_BOOK exclusion silently selects the
            # OK population toward calm markets.
            px = None
            quote_err = True
        if out == "bid":
            bid = px
        else:
            ask = px
    return bid, ask, quote_err


async def fetch_book(session, token_id: str,
                     timeout_s: float = 2.0) -> Optional[dict]:
    """Trimmed /book ladder for the record, or None. Fail-soft by design:
    ladder capture must never turn a quotable signal into an error — gate
    verdicts come from quote_book alone."""
    try:
        async with session.get(CLOB_BOOK_URL,
                               params={"token_id": token_id},
                               timeout=timeout_s) as r:
            return trim_book(await r.json())
    except Exception:
        return None


async def watch(cfg: WatcherConfig, log: Callable[[str], None] = print) -> None:
    """Poll loop. Raises on wiring errors (fail-loud; run.py exits so
    systemd restarts); per-signal errors are recorded, never fatal."""
    import aiohttp
    from base_engine.data.blockchain_client import BlockchainClient
    from web3 import Web3

    from mirror_v3.sizing import merge_same_tx

    roster = load_roster(cfg.roster_path)
    roster_set = set(roster)
    roster_topics = [addr_topic(a) for a in roster]  # topic-2 = order owner
    v2_addrs = [Web3.to_checksum_address(a)
                for a in (EXCHANGE_V2, NEGRISK_EXCHANGE_V2)]
    # conviction median machinery RETIRED 2026-08-25 (rec 9 / ledger #30)
    log(f"[copy_watcher] roster={len(roster)} rpc={cfg.rpc_url} "
        f"gates: chase<={cfg.max_chase:.02f} spread<={cfg.max_spread:.02f} "
        f"poll={cfg.poll_s}s sink={cfg.shadow_path} "
        f"exchanges=V2 topic={FILL_TOPIC_V2[:10]}…")

    bidreg = bidsim_make(cfg)
    if bidreg is not None:
        log(f"[bidsim] ENABLED sink={cfg.bidsim_path} band=[{BIDSIM_LO},"
            f"{BIDSIM_HI}) expire={BIDSIM_EXPIRE_S/3600:.0f}h "
            f"rehydrated_open={bidreg.n_open}")
    bc = BlockchainClient(rpc_url=cfg.rpc_url)
    await rpc_call(bc.ensure_client())  # hang at startup -> raise -> systemd restart

    async def fill_logs(lo: int, hi: int, owner_topics: Optional[list]) -> list:
        """Raw V2 fill logs in [lo, hi]; owner_topics=None for the canary
        (unfiltered count). ONE eth_getLogs covers both exchanges."""
        topics: list = [FILL_TOPIC_V2]
        if owner_topics is not None:
            topics += [None, owner_topics]
        return await rpc_call(bc.w3.eth.get_logs({
            "fromBlock": lo, "toBlock": hi,
            "address": v2_addrs, "topics": topics}))

    os.makedirs(os.path.dirname(cfg.shadow_path) or ".", exist_ok=True)
    dedup = FirstBuyDedup()
    _seeded = seed_firstbuy(dedup, cfg.shadow_path)
    log(f"[watch] first-buy dedup seeded from sink: {_seeded} pairs")
    cursor = int(await rpc_call(bc.w3.eth.get_block_number())) + 1  # forward-only
    fail_streak = 0  # consecutive failures of the SAME window (stall guard)
    canary_zero_streak = 0
    canary_seen_first = False  # first result is logged either way
    canary_next = 0.0  # first canary fires on the first poll (fail fast)

    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(cfg.poll_s)
            try:
                head = int(await rpc_call(bc.w3.eth.get_block_number()))
            except Exception as e:
                log(f"[copy_watcher] head fetch error: {e!r}")
                continue
            if time.time() >= canary_next:
                canary_next = time.time() + CANARY_EVERY_S
                b1 = head - CANARY_SETTLE_BLOCKS
                try:
                    n_canary = len(await fill_logs(b1 - CANARY_SPAN, b1, None))
                except Exception as e:
                    log(f"[copy_watcher] canary query error: {e!r}")
                    n_canary = -1
                if n_canary >= 0:
                    if not canary_seen_first:
                        canary_seen_first = True
                        log(f"[copy_watcher] first canary: {n_canary} V2 fill "
                            f"events in the settled {CANARY_SPAN}-block window "
                            f"({'detection sees the market' if n_canary else 'quiet or blind — alarm decides after next cycle'})")
                    canary_zero_streak, msg = canary_state(
                        canary_zero_streak, n_canary)
                    if msg:
                        log(f"[copy_watcher] {msg}")
            if head < cursor:
                continue
            for lo, hi in block_chunks(cursor, head):
                # RETRY-DON'T-SKIP: a failed window is retried from the same
                # cursor next poll (Tenderly's balancer races its own head:
                # 'invalid block range params' for blocks another node just
                # announced). Silently advancing past a failure would DROP
                # the window's fills — lost samples. A window that fails
                # MAX_WINDOW_RETRIES in a row is skipped LOUDLY.
                events: list = []
                window_ok = True
                try:
                    events = await fill_logs(lo, hi, roster_topics)
                except Exception as e:
                    window_ok = False
                    log(f"[copy_watcher] get_logs error [{lo},{hi}]: {e!r}")
                if not window_ok:
                    fail_streak += 1
                    if fail_streak >= MAX_WINDOW_RETRIES:
                        log(f"[copy_watcher] window [{lo},{hi}] failed "
                            f"{fail_streak}x — SKIPPING (dropped window, "
                            f"lost samples possible)")
                        fail_streak = 0
                        cursor = hi + 1
                        continue
                    cursor = lo  # retry this window next poll
                    break
                fail_streak = 0
                cursor = hi + 1
                # decode raw V2 logs, then merge same-tx fills into ONE wager
                # (a split order's first fill alone under-reads conviction)
                decoded: list[dict] = []
                for ev in events:
                    sig = decode_fill_v2(dict(ev), roster_set)
                    if sig is None:
                        continue
                    sig["tx"] = _hex(ev.get("transactionHash", ""))
                    sig["_block"] = int(ev.get("blockNumber", 0))
                    decoded.append(sig)
                for sig in merge_same_tx(decoded):
                    now = time.time()
                    # direction is not in the V2 event — read the receipt
                    # (roster hits only, so this is rare and cheap)
                    try:
                        rcpt = await rpc_call(
                            bc.w3.eth.get_transaction_receipt(sig["tx"]))
                        side = side_from_receipt_logs(
                            [dict(lg) for lg in rcpt["logs"]],
                            sig["trader"], sig["token_id"])
                    except Exception as e:
                        log(f"[copy_watcher] receipt error {sig['tx'][:18]}…: "
                            f"{e!r}")
                        side = None
                    if side != "BUY":
                        if side is None:
                            log(f"[copy_watcher] SIDE UNKNOWN (skipped) "
                                f"{sig['trader'][:10]}… tok="
                                f"{sig['token_id'][:10]}… tx={sig['tx'][:18]}…")
                        elif side == "SELL":
                            # GO-precondition #4: record to the SEPARATE
                            # sell sink; a sink failure must never break
                            # BUY detection (fail-toward-missing-data,
                            # alarmed by the sink's own staleness later)
                            try:
                                with open(cfg.sell_sink, "a") as _sf:
                                    _sf.write(json.dumps(
                                        sell_record(sig, now)) + "\n")
                            except Exception as e:
                                log(f"[copy_watcher] sell-sink error: {e!r}")
                        continue  # graded estimand is first BUY - unchanged
                    sig["side"] = "BUY"
                    try:
                        blk = await rpc_call(bc.w3.eth.get_block(sig["_block"]))
                        block_ts = int(blk["timestamp"])
                    except Exception:
                        block_ts = int(now)
                        # marker (2026-08-25): fabricated timestamp - lag
                        # stats must be able to exclude it
                        sig["block_ts_est"] = True
                    sig.pop("_block", None)
                    sig["first_buy"] = dedup.is_first(
                        sig["trader"], sig["token_id"])
                    if bidreg is not None and sig["first_buy"]:
                        try:
                            if bidreg.register(sig["trader"], sig["token_id"],
                                               float(sig["whale_price"]),
                                               now, "chain",
                                               trigger_tx=sig.get("tx")):
                                log(f"[bidsim] POST bid={sig['whale_price']:.3f} "
                                    f"tok={sig['token_id'][:10]}... "
                                    f"open={bidreg.n_open}")
                        except Exception as e:
                            log(f"[bidsim] register error: {e!r}")
                    # conviction annotation RETIRED 2026-08-25 (operator
                    # "go with rec 9", ledger #30): its sole purpose - the
                    # pre-registered Option-D Spearman gate - was never
                    # built, no reader existed. sizing.py stays as a tested
                    # library for any future clean re-test.
                    bid, ask, quote_err = await quote_book(
                        session, sig["token_id"])
                    quote_ts = time.time()
                    sig["quote_error"] = quote_err
                    sig["one_sided_book"] = (bid is None)
                    sanity = quote_sanity_msg(bid, ask)
                    if sanity:
                        log(f"[copy_watcher] {sanity} "
                            f"tok={sig['token_id'][:10]}…")
                    book = await fetch_book(session, sig["token_id"])
                    verdict, fill = evaluate_gates(
                        sig["whale_price"], bid, ask,
                        cfg.max_chase, cfg.max_spread, cfg.max_fill)
                    rec = shadow_record(
                        sig, verdict, fill, bid, ask, block_ts, now,
                        tx=sig.pop("tx", ""), book=book,
                        quote_ts=quote_ts)
                    with open(cfg.shadow_path, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                    log(f"[copy_watcher] {verdict:<15} {sig['trader'][:10]}… "
                        f"tok={sig['token_id'][:10]}… whale={sig['whale_price']:.3f} "
                        f"ask={ask} lag={rec['detect_lag_s']}s "
                        f"first={sig['first_buy']}")
            # cursor is advanced per-window above (retry-don't-skip); no
            # blanket jump to head here — that was the dropped-window bug


# -- RTDS A/B consumer (2026-07-30) ------------------------------------------
# Polymarket's real-time data socket pushes every trade at match time WITH the
# trader identity (proxyWallet) and side - measured 2026-07-29/30: delivery lag
# p50 0.82s / p90 1.2s / max 1.4s (n=9,576) vs this file's chain poll at
# p50 1.30s / p90 11.6s / max 53.8s (n=12,980). This consumer runs BESIDE the
# chain poll and writes same-schema records to its OWN sink for A/B: the chain
# stream stays canonical, so nothing downstream changes until the A/B proves
# parity and the source swap is separately approved. Design rules:
#   * NEVER raises out: connect/parse/write failures log + backoff + reconnect.
#     The chain poll is the function guarantee; this task's death must not
#     take detection down with it.
#   * Timeout on EVERY network await (open_timeout, send/recv wait_for).
#   * Liveness: the firehose runs ~40-60 trades/s; RTDS_SILENT_ALARM_S with no
#     frames at all means the feed (not the market) is dead - log LOUDLY and
#     reconnect. A silently-dead subscription is this lane's worst failure mode.
#   * Separate FirstBuyDedup + TrailingMedians instances - the A/B stream must
#     not mutate the primary stream's estimand or conviction state.
# 60s -> 15s (2026-08-19 over-correction audit): the app-PING keepalive did
# NOT reduce outage frequency (18 alarms/2.9h post-fix vs ~6/h before;
# coverage gap 13.8% vs 11.8% - the PING root-cause theory is REFUTED by
# live A/B). The venue cycles these connections regardless (the reference
# recorder also reconnects ~4/h). The real lever is DETECTION TIME: at the
# measured ~40-60 frames/s, 15 silent seconds is unambiguous death
# (P(no frame | alive) ~ 0), and each outage shrinks from ~61s to ~16s.
RTDS_SILENT_ALARM_S = 15.0
RTDS_BACKOFF_S = (1.0, 5.0, 15.0, 60.0)  # reconnect schedule, capped at tail
# Keepalive fix (2026-08-19, operator-approved): RTDS requires an APPLICATION-
# level "PING" text frame - protocol-level websocket pings keep the transport
# alive while the subscription goes dead (measured: 83 silent alarms vs 2
# ConnectionClosedError in 13.9h; ~9.2% downtime; 98.9% of the A/B coverage
# gap fell inside those outages). The repo's reference consumer already
# documents this venue requirement (base_engine/data/rtds_websocket.py:22
# _PING_INTERVAL=5 "RTDS requires keep-alive pings", :75 ping_interval=None
# "we handle pings manually", :115 send("PING"), :170 PONG skip). Mirror it.
RTDS_APP_PING_S = 5.0


def parse_rtds_trades(msg: Any) -> list[dict]:
    """Trade rows out of one decoded RTDS frame; [] for control/status/other.
    Never raises on malformed frames - the stream carries occasional empty
    and non-trade messages (measured: statusCode frames, blank lines)."""
    if not isinstance(msg, dict) or msg.get("type") != "trades":
        return []
    pl = msg.get("payload")
    items = pl if isinstance(pl, list) else [pl]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        w = it.get("proxyWallet")
        tok = it.get("asset")
        if not w or not tok:
            continue
        try:
            price = float(it.get("price"))
            size = float(it.get("size"))
        except (TypeError, ValueError):
            continue
        ts = it.get("timestamp") or msg.get("timestamp")
        try:
            ts = int(ts)
            ts = ts // 1000 if ts > 2e10 else ts
        except (TypeError, ValueError):
            ts = None
        out.append({
            "trader": str(w).lower(),
            "token_id": str(tok),
            "side": str(it.get("side") or "").upper(),
            "price": price,
            "size": size,
            "trade_ts": ts,
            # lower-cased: the chain path's tx (HexBytes.hex()) is lowercase,
            # and this is the A/B join key — case mismatch = silent 0 joins
            "tx": _hex(it.get("transactionHash") or "").lower(),
        })
    return out


def rtds_sig(row: dict) -> dict:
    """RTDS row -> the sig shape the chain path feeds shadow_record. was_taker
    is unknowable from this feed (None, disclosed); whale_size_usd = price*size
    (RTDS size is shares). NOTE the semantic difference vs the chain path:
    the chain path merges same-tx clips into one wager before conviction -
    this stream records per-row. A/B latency joins are by tx, where the
    difference is invisible; conviction fields here are annotation only."""
    return {
        "trader": row["trader"],
        "token_id": row["token_id"],
        "whale_price": row["price"],
        "whale_size_usd": round(row["price"] * row["size"], 6),
        "was_taker": None,
        "side": "BUY",
        "source": "rtds",
    }


async def rtds_watch(cfg: WatcherConfig, log: Callable[[str], None] = print) -> None:
    """RTDS consumer loop. Runs forever; every failure path reconnects."""
    import aiohttp
    import websockets

    roster_set = set(load_roster(cfg.roster_path))
    # conviction median machinery RETIRED 2026-08-25 (rec 9 / ledger #30)
    dedup = FirstBuyDedup()
    _seeded = seed_firstbuy(dedup, cfg.rtds_sink)
    log(f"[rtds_watch] first-buy dedup seeded from sink: {_seeded} pairs")
    bidreg = bidsim_make(cfg)
    last_sweep = time.time()
    os.makedirs(os.path.dirname(cfg.rtds_sink) or ".", exist_ok=True)
    log(f"[rtds_watch] A/B consumer STARTING url={cfg.rtds_url} "
        f"sink={cfg.rtds_sink} roster={len(roster_set)} "
        f"silent_alarm={RTDS_SILENT_ALARM_S:.0f}s")
    attempt = 0
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with websockets.connect(
                        cfg.rtds_url, ping_interval=None, ping_timeout=None,
                        open_timeout=12) as ws:
                    await asyncio.wait_for(ws.send(json.dumps(
                        {"action": "subscribe", "subscriptions":
                            [{"topic": "activity", "type": "trades"}]})),
                        timeout=10)
                    log("[rtds_watch] connected + subscribed (activity/trades)")

                    async def _app_ping():
                        # venue keepalive; on send failure just stop - the
                        # recv loop's silent-alarm handles the dead socket
                        while True:
                            await asyncio.sleep(RTDS_APP_PING_S)
                            try:
                                await asyncio.wait_for(ws.send("PING"),
                                                       timeout=10)
                            except Exception as e:
                                # audit 2026-08-19: a silent death here hid
                                # whether PINGs were even being sent
                                log(f"[rtds_watch] app-ping stopped: "
                                    f"{type(e).__name__} (reconnect handles it)")
                                return
                    ping_task = asyncio.create_task(_app_ping())
                    attempt = 0
                    last_data = time.time()
                    try:
                        while True:
                           try:
                               raw = await asyncio.wait_for(
                                   ws.recv(), timeout=RTDS_SILENT_ALARM_S)
                           except asyncio.TimeoutError:
                               log(f"[rtds_watch] RTDS SILENT ALARM: no frames in "
                                   f"{RTDS_SILENT_ALARM_S:.0f}s on a ~50-trade/s "
                                   f"feed - RECONNECTING (chain poll unaffected)")
                               break
                           if raw in ("PONG", "pong"):
                               # keepalive reply, NOT data. It wakes recv()
                               # and would silently satisfy the 60s alarm -
                               # so data-liveness is tracked separately: a
                               # subscription answering PINGs while sending
                               # zero trades is still DEAD and must reconnect.
                               if time.time() - last_data > RTDS_SILENT_ALARM_S:
                                   log(f"[rtds_watch] RTDS DATA-SILENT ALARM: "
                                       f"PONGs alive but no data frames in "
                                       f"{RTDS_SILENT_ALARM_S:.0f}s - "
                                       f"RECONNECTING (chain poll unaffected)")
                                   break
                               continue
                           last_data = time.time()
                           try:
                               msg = json.loads(raw)
                           except (TypeError, ValueError):
                               continue
                           for row in parse_rtds_trades(msg):
                               # bidsim: EVERY print (any trader) can fill an open
                               # shadow bid; sweep expiries ~each minute
                               if bidreg is not None:
                                   try:
                                       nf = bidreg.on_print(
                                           row["token_id"], row["price"],
                                           time.time(),
                                           print_tx=row.get("tx"),
                                           print_trader=row.get("trader"),
                                           print_side=row.get("side"))
                                       if nf:
                                           log(f"[bidsim] FILL x{nf} tok="
                                               f"{row['token_id'][:10]}... print="
                                               f"{row['price']:.3f} open={bidreg.n_open}")
                                       if time.time() - last_sweep > 60:
                                           last_sweep = time.time()
                                           ne = bidreg.sweep_expired(last_sweep)
                                           if ne:
                                               log(f"[bidsim] EXPIRE x{ne} "
                                                   f"open={bidreg.n_open}")
                                   except Exception as e:
                                       log(f"[bidsim] print/sweep error: {e!r}")
                               if row["trader"] not in roster_set:
                                   continue
                               if row["side"] != "BUY":
                                   continue  # estimand is first BUY, as chain path
                               now = time.time()
                               sig = rtds_sig(row)
                               sig["first_buy"] = dedup.is_first(
                                   sig["trader"], sig["token_id"])
                               if bidreg is not None and sig["first_buy"]:
                                   try:
                                       if bidreg.register(sig["trader"],
                                                          sig["token_id"],
                                                          float(sig["whale_price"]),
                                                          now, "rtds",
                                                          # rtds_sig() does NOT
                                                          # carry tx - the row
                                                          # does (:932 uses it
                                                          # the same way). Using
                                                          # sig here made the
                                                          # guard inert for every
                                                          # RTDS post.
                                                          trigger_tx=row.get("tx")):
                                           log(f"[bidsim] POST bid="
                                               f"{sig['whale_price']:.3f} tok="
                                               f"{sig['token_id'][:10]}... "
                                               f"open={bidreg.n_open}")
                                   except Exception as e:
                                       log(f"[bidsim] register error: {e!r}")
                               # conviction annotation RETIRED 2026-08-25
                               # (see chain path note)
                               bid, ask, quote_err = await quote_book(
                                   session, sig["token_id"])
                               quote_ts = time.time()
                               sig["quote_error"] = quote_err
                               sig["one_sided_book"] = (bid is None)
                               sanity = quote_sanity_msg(bid, ask)
                               if sanity:
                                   log(f"[rtds_watch] {sanity} "
                                       f"tok={sig['token_id'][:10]}...")
                               book = await fetch_book(session, sig["token_id"])
                               verdict, fill = evaluate_gates(
                                   sig["whale_price"], bid, ask,
                                   cfg.max_chase, cfg.max_spread, cfg.max_fill)
                               rec = shadow_record(
                                   sig, verdict, fill, bid, ask,
                                   block_ts=int(row["trade_ts"] or now),
                                   now_ts=now, tx=row["tx"], book=book,
                                   quote_ts=quote_ts)
                               with open(cfg.rtds_sink, "a") as f:
                                   f.write(json.dumps(rec) + "\n")
                               log(f"[rtds_watch] {verdict:<15} "
                                   f"{sig['trader'][:10]}... "
                                   f"tok={sig['token_id'][:10]}... "
                                   f"whale={sig['whale_price']:.3f} ask={ask} "
                                   f"lag={rec['detect_lag_s']}s "
                                   f"first={sig['first_buy']}")
                    finally:
                        ping_task.cancel()
            except asyncio.CancelledError:
                raise  # shutdown is the only way out
            except Exception as e:
                log(f"[rtds_watch] connection error: {type(e).__name__}: "
                    f"{str(e)[:140]}")
            delay = RTDS_BACKOFF_S[min(attempt, len(RTDS_BACKOFF_S) - 1)]
            attempt += 1
            log(f"[rtds_watch] reconnecting in {delay:.0f}s (attempt {attempt})")
            await asyncio.sleep(delay)


# -- Shadow-bid simulator (2026-08-19, docs/BIDSIM_DESIGN.md) ----------------
# Band-only maker-execution measurement: on a roster first-buy BUY with
# whale_price in [BIDSIM_LO, BIDSIM_HI), register a shadow BID at the whale
# price; a FILL is any real print at price <= bid on that token (RTDS
# firehose, all traders); EXPIRE after 24h. Fill rule is QUEUE-OPTIMISTIC by
# design (registered) - it brackets the snapshot proxy's understatement.
# Enabled only when MIRROR3_BIDSIM=1; otherwise every hook below is a no-op.
BIDSIM_LO, BIDSIM_HI = 0.65, 0.85
BIDSIM_EXPIRE_S = 24 * 3600.0


class BidRegistry:
    """Open shadow bids keyed by (trader, token). Pure core - offline-testable.
    Emits events through the sink callback; never raises out of hooks."""

    def __init__(self, sink_write: Callable[[dict], None]) -> None:
        self._open: dict[tuple, dict] = {}
        self._done: set[tuple] = set()  # one bid per (trader, token), ever
        self._write = sink_write

    def register(self, trader: str, token_id: str, whale_price: float,
                 now_ts: float, source: str,
                 trigger_tx: Optional[str] = None) -> bool:
        """Register a bid at the whale price if in-band and not seen before.

        `trigger_tx` is the transaction of the whale fill that PROMPTED this
        bid. It is recorded so on_print can refuse to fill the bid from that
        same transaction's own tape rows (2026-08-21 correction, see
        docs/BIDSIM_DESIGN.md AMENDMENT 1)."""
        if not (BIDSIM_LO <= whale_price < BIDSIM_HI):
            return False
        key = (str(trader).lower(), str(token_id))
        if key in self._done:
            return False
        self._done.add(key)
        bid = {"type": "post", "trader": key[0], "token_id": key[1],
               "bid": float(whale_price), "post_ts": round(now_ts, 3),
               "source": source,
               "trigger_tx": (str(trigger_tx).lower() if trigger_tx else None)}
        self._open[key] = bid
        self._write(bid)
        return True

    def on_print(self, token_id: str, price: float, now_ts: float,
                 print_tx: Optional[str] = None,
                 print_trader: Optional[str] = None,
                 print_side: Optional[str] = None) -> int:
        """A real trade printed on token_id at `price`. Fill every open bid on
        that token with bid >= price, EXCEPT a bid whose own trigger
        transaction is this print (a resting order cannot be filled by the
        very order it was posted in reaction to - those makers were already
        ahead of us in queue).

        The print's identity (tx/trader/side) is recorded on the fill event so
        the remaining open question - which side of the tape legitimately
        fills a resting bid - is measurable from forward data rather than
        assumed. Returns fills emitted."""
        tx = str(print_tx).lower() if print_tx else None
        n = 0
        for key in [k for k, b in self._open.items()
                    if k[1] == str(token_id) and price <= b["bid"] + 1e-12
                    and not (tx and b.get("trigger_tx") == tx)]:
            b = self._open.pop(key)
            self._write({"type": "fill", "trader": b["trader"],
                         "token_id": b["token_id"], "bid": b["bid"],
                         "post_ts": b["post_ts"],
                         "fill_ts": round(now_ts, 3),
                         "fill_print_price": float(price),
                         "wait_s": round(now_ts - b["post_ts"], 1),
                         "trigger_tx": b.get("trigger_tx"),
                         "fill_tx": tx,
                         "fill_trader": (str(print_trader).lower()
                                         if print_trader else None),
                         "fill_side": (str(print_side).upper()
                                       if print_side else None)})
            n += 1
        return n

    def sweep_expired(self, now_ts: float) -> int:
        """Expire bids older than BIDSIM_EXPIRE_S. Returns expiries emitted."""
        n = 0
        for key in [k for k, b in self._open.items()
                    if now_ts - b["post_ts"] >= BIDSIM_EXPIRE_S]:
            b = self._open.pop(key)
            self._write({"type": "expire", "trader": b["trader"],
                         "token_id": b["token_id"], "bid": b["bid"],
                         "post_ts": b["post_ts"],
                         "expire_ts": round(now_ts, 3)})
            n += 1
        return n

    @property
    def n_open(self) -> int:
        return len(self._open)


def bidsim_rehydrate(reg: "BidRegistry", sink_path: str, now_ts: float) -> int:
    """Rebuild open bids from the sink after a restart: posts without a
    terminal event, not yet expired. Terminal keys also seed _done so a
    restart can never double-register. Returns bids reopened."""
    if not os.path.exists(sink_path):
        return 0
    posts: dict[tuple, dict] = {}
    terminal: set = set()
    with open(sink_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            key = (str(e.get("trader", "")), str(e.get("token_id", "")))
            if e.get("type") == "post":
                posts[key] = e
            elif e.get("type") in ("fill", "expire"):
                terminal.add(key)
    reopened = 0
    for key, e in posts.items():
        reg._done.add(key)
        if key in terminal:
            continue
        if now_ts - float(e.get("post_ts") or 0) >= BIDSIM_EXPIRE_S:
            reg._write({"type": "expire", "trader": key[0], "token_id": key[1],
                        "bid": e.get("bid"), "post_ts": e.get("post_ts"),
                        "expire_ts": round(now_ts, 3),
                        "note": "expired across restart"})
            continue
        reg._open[key] = {"type": "post", "trader": key[0], "token_id": key[1],
                          "bid": float(e.get("bid") or 0),
                          "post_ts": float(e.get("post_ts") or 0),
                          "source": e.get("source", "rehydrated"),
                          "trigger_tx": e.get("trigger_tx")}
        reopened += 1
    for key in terminal:
        reg._done.add(key)
    return reopened


_BIDSIM_SHARED: dict = {}


def bidsim_make(cfg: "WatcherConfig") -> Optional["BidRegistry"]:
    """SHARED registry wired to the sink (one instance per sink path — the
    chain watch and rtds_watch run on one event loop and must see the same
    open-bid set, or posts double and fills split), or None when disabled."""
    if not cfg.bidsim:
        return None
    if cfg.bidsim_path in _BIDSIM_SHARED:
        return _BIDSIM_SHARED[cfg.bidsim_path]
    os.makedirs(os.path.dirname(cfg.bidsim_path) or ".", exist_ok=True)

    def write(event: dict) -> None:
        with open(cfg.bidsim_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    reg = BidRegistry(write)
    bidsim_rehydrate(reg, cfg.bidsim_path, time.time())
    _BIDSIM_SHARED[cfg.bidsim_path] = reg
    return reg
