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

DETECTION: polls OrderFilled on both CTF exchanges (main + NegRisk) with
the roster addresses as indexed-topic filters (maker OR taker), from a
block cursor. Poll floor ~POLL_S + block time => ~3-4s detection lag,
measured and recorded per signal (detect_lag_s). WSS subscription is a
later upgrade; polling works on any HTTP RPC that serves eth_getLogs.

GATES (per signal, all recorded, none fatal to the run):
  NO_BOOK          could not quote a book (CLOB /price timeout/empty)
  SPREAD_TOO_WIDE  ask - bid > max_spread
  PRICE_RAN_AWAY   ask > whale_price + max_chase (the edge already left)
  OK               shadow-filled at the current ask (our copy price)

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
GETLOGS_CHUNK = 900  # public-RPC eth_getLogs range cap (audit-proven)
MAX_WINDOW_RETRIES = 5  # same-window failures before a LOUD skip


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


def evaluate_gates(whale_price: float, best_bid: Optional[float],
                   best_ask: Optional[float], max_chase: float,
                   max_spread: float) -> tuple[str, Optional[float]]:
    """(verdict, shadow_fill_price). OK => we'd cross at the ask; every
    other verdict is a skip whose cost is zero by construction."""
    if best_ask is None or best_ask <= 0:
        return "NO_BOOK", None
    if best_bid is not None and (best_ask - best_bid) > max_spread:
        return "SPREAD_TOO_WIDE", None
    if best_ask > whale_price + max_chase:
        return "PRICE_RAN_AWAY", None
    return "OK", best_ask


def block_chunks(b0: int, b1: int, chunk: int = GETLOGS_CHUNK) -> list[tuple[int, int]]:
    spans, lo = [], b0
    while lo <= b1:
        hi = min(lo + chunk - 1, b1)
        spans.append((lo, hi))
        lo = hi + 1
    return spans


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
    poll_s: float = 2.0

    @classmethod
    def from_env(cls, env) -> "WatcherConfig":
        missing = [k for k in ("MIRROR3_ROSTER_PATH", "MIRROR3_RPC_URL")
                   if not str(env.get(k, "")).strip()]
        if missing:
            raise ValueError(
                f"copy watcher enabled but required env missing: {missing} "
                f"(v3 rule: explicit presence, no code defaults for wiring)")
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
            poll_s=float(env.get("MIRROR3_POLL_S", "2")),
        )


def shadow_record(sig: dict, verdict: str, fill: Optional[float],
                  best_bid: Optional[float], best_ask: Optional[float],
                  block_ts: int, now_ts: float, tx: str) -> dict:
    return {
        **sig,
        "verdict": verdict,
        "shadow_fill": fill,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "block_ts": block_ts,
        "detect_ts": round(now_ts, 3),
        "detect_lag_s": round(now_ts - block_ts, 3),
        "tx": tx,
    }


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
                     timeout_s: float = 2.0) -> tuple[Optional[float], Optional[float]]:
    """(best_bid, best_ask) from the public CLOB /price endpoint."""
    bid = ask = None
    for side, out in (("SELL", "bid"), ("BUY", "ask")):
        try:
            async with session.get(
                    CLOB_PRICE_URL,
                    params={"token_id": token_id, "side": side},
                    timeout=timeout_s) as r:
                px = float((await r.json()).get("price", 0)) or None
        except Exception:
            px = None
        if out == "bid":
            bid = px
        else:
            ask = px
    return bid, ask


async def watch(cfg: WatcherConfig, log: Callable[[str], None] = print) -> None:
    """Poll loop. Raises on wiring errors (fail-loud; run.py exits so
    systemd restarts); per-signal errors are recorded, never fatal."""
    import aiohttp
    from base_engine.data.blockchain_client import (
        BlockchainClient, EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT,
        ORDER_FILLED_EVENT_ABI)
    from web3 import Web3

    from mirror_v3.sizing import (
        TrailingMedians, conviction_multiplier, merge_same_tx,
        seed_medians_from_cache)

    roster = load_roster(cfg.roster_path)
    roster_set = set(roster)
    roster_cs = [Web3.to_checksum_address(a) for a in roster]
    if cfg.median_cache and os.path.isdir(cfg.median_cache):
        medians = seed_medians_from_cache(cfg.median_cache, roster)
        seeded = sum(1 for a in roster if medians.stats(a)[1] > 0)
        log(f"[copy_watcher] conviction medians seeded for {seeded}/{len(roster)} "
            f"traders from {cfg.median_cache}")
    else:
        medians = TrailingMedians()
        log(f"[copy_watcher] NO median cache ({cfg.median_cache!r}) — all "
            f"traders cold-start at 1.0x until {20} observed wagers")
    log(f"[copy_watcher] roster={len(roster)} rpc={cfg.rpc_url} "
        f"gates: chase<={cfg.max_chase:.02f} spread<={cfg.max_spread:.02f} "
        f"poll={cfg.poll_s}s sink={cfg.shadow_path}")

    bc = BlockchainClient(rpc_url=cfg.rpc_url)
    await bc.ensure_client()
    contracts = [bc.w3.eth.contract(
        address=Web3.to_checksum_address(c), abi=[ORDER_FILLED_EVENT_ABI])
        for c in dict.fromkeys((EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT))]

    os.makedirs(os.path.dirname(cfg.shadow_path) or ".", exist_ok=True)
    dedup = FirstBuyDedup()
    cursor = int(await bc.w3.eth.get_block_number()) + 1  # forward-only
    fail_streak = 0  # consecutive failures of the SAME window (stall guard)

    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(cfg.poll_s)
            try:
                head = int(await bc.w3.eth.get_block_number())
            except Exception as e:
                log(f"[copy_watcher] head fetch error: {e!r}")
                continue
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
                for c in contracts:
                    for field in ("maker", "taker"):
                        try:
                            evs = await get_logs_compat(
                                c.events.OrderFilled, lo, hi,
                                {field: roster_cs})
                            events.extend(evs)
                        except Exception as e:
                            window_ok = False
                            log(f"[copy_watcher] get_logs error "
                                f"[{lo},{hi}] {field}: {e!r}")
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
                # decode, then merge same-tx fills into ONE wager (a split
                # order's first fill alone under-reads conviction)
                decoded: list[dict] = []
                for ev in events:
                    sig = decode_fill(dict(ev["args"]), roster_set)
                    if sig is None:
                        continue
                    txh = ev.get("transactionHash", "")
                    sig["tx"] = txh.hex() if hasattr(txh, "hex") else str(txh)
                    sig["_block"] = int(ev.get("blockNumber", 0))
                    decoded.append(sig)
                for sig in merge_same_tx(decoded):
                    now = time.time()
                    try:
                        blk = await bc.w3.eth.get_block(sig["_block"])
                        block_ts = int(blk["timestamp"])
                    except Exception:
                        block_ts = int(now)
                    sig.pop("_block", None)
                    sig["first_buy"] = dedup.is_first(
                        sig["trader"], sig["token_id"])
                    # conviction annotation (A+D rule): r vs the trader's
                    # median BEFORE this wager, then observe it
                    tmed, n_obs = medians.stats(sig["trader"])
                    mult, r = conviction_multiplier(
                        sig["whale_size_usd"], tmed, n_obs)
                    medians.observe(sig["trader"], sig["whale_size_usd"])
                    sig["trailing_median_usd"] = tmed
                    sig["conviction_r"] = round(r, 4) if r is not None else None
                    sig["size_multiplier"] = mult
                    bid, ask = await quote_book(session, sig["token_id"])
                    verdict, fill = evaluate_gates(
                        sig["whale_price"], bid, ask,
                        cfg.max_chase, cfg.max_spread)
                    rec = shadow_record(
                        sig, verdict, fill, bid, ask, block_ts, now,
                        tx=sig.pop("tx", ""))
                    with open(cfg.shadow_path, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                    log(f"[copy_watcher] {verdict:<15} {sig['trader'][:10]}… "
                        f"tok={sig['token_id'][:10]}… whale={sig['whale_price']:.3f} "
                        f"ask={ask} lag={rec['detect_lag_s']}s "
                        f"first={sig['first_buy']} mult={mult}x")
            # cursor is advanced per-window above (retry-don't-skip); no
            # blanket jump to head here — that was the dropped-window bug
