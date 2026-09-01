#!/usr/bin/env python3
"""Tx-exact re-adjudication of DISCREPANT chain-audit traders.

WHY (2026-07-12): the 2026-07-10 chain audit (scripts/audit_roster_chain.py)
excluded 9/29 traders as DISCREPANT. Its matcher volume-blends EVERY
OrderFilled event for (trader, token, direction) inside a ±window around the
API fill's timestamp — so a trader who made two REAL trades on the same token
at different prices inside the window gets a blended chain price that matches
neither API row: a false price_mismatch. King's notes flagged exactly this
("some may be ±30min window-blend artifacts — a tx-hash-exact matcher would
settle it"). This script is that matcher.

HOW: the trade cache does NOT retain transactionHash (normalize_activity
drops it — verified find_copyable_traders.py), so the API row cannot name its
tx. Instead the discrimination happens on the CHAIN side: window events are
grouped by transactionHash, and the API row (price p, size s) is matched
against each candidate = every single event AND every per-tx aggregate:
  verified_txexact   some candidate matches SIZE (±tol_size rel) AND PRICE
                     (±tol_price) — the exact trade exists on chain
  mismatch_txexact   some candidate matches SIZE but not PRICE — a
                     size-matched tx at a materially different price; this is
                     a REAL discrepancy, no blend excuse left
  not_found          no candidate matches the row's size (includes the old
                     not_founds; RPC gaps and sub-window latency happen)
Blending across txs never occurs, so the artifact class is eliminated.

PRE-REGISTERED ADJUDICATION (fixed before any run; chain still wins):
  VINDICATED         0 mismatch_txexact AND not_found <= max-notfound (20%)
                     AND rpc_error <= 50%, over >= 20 sampled BUY fills
  STILL_DISCREPANT   >= 1 mismatch_txexact
  THIN / ERROR       as in the original audit (not enough chain evidence)
Only VINDICATED traders may be PROPOSED for the roster, as a SECOND COHORT
with its own shadow start date — operator decision required; nothing here
auto-adds anybody to anything.

DUAL-ERA MATCHING (2026-07-14): Polymarket moved trading to the V2
exchanges; a V1-only matcher finds NOTHING for post-migration fills, so
its not_found column was structurally inflated (caught when the grey
re-review's not_found rates barely moved at a 4x-wider window — wrong
PLACE, not wrong time). Window events now come from BOTH eras:
V1 OrderFilled + the V2 unnamed fill event (topic0 FILL_TOPIC_V2, layout
receipt-validated in mirror_v3/copy_watcher). The V2 event does not carry
direction, so V2 candidates are emitted BUY-shaped and any size-matched
winner is RECEIPT-CONFIRMED before it may verify or damn a claim;
unconfirmable candidates are dropped, never trusted. THIN (operator rule
2026-07-14) is an evidence gap, never an accusation — the response is a
deeper search, not a threshold change.

SAFETY: READ-ONLY. RPC GETs only. No DB writes, no orders, no bot code.

INVOCATION (VPS, from a /tmp clone — never the deployed tree):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbre \
      venv/bin/python /tmp/mbre/scripts/readjudicate_discrepant.py \
      --audit-json /opt/pa2-shared/mb_copyable_data/chain_audit.json \
      --cache /opt/pa2-shared/mb_copyable_data/copyable_cache \
      --rpc-url https://polygon.gateway.tenderly.co \
      | tee /tmp/readjudicate.log
    ... --self-test   # offline matching-logic check, no network
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import audit_roster_chain as ac  # noqa: E402  (audited helpers, reused as-is)
import find_copyable_traders as fc  # noqa: E402
from mirror_v3.copy_watcher import (  # noqa: E402  (receipt-validated V2 kit)
    EXCHANGE_V2, FILL_TOPIC_V2, NEGRISK_EXCHANGE_V2, _hex, _words, addr_topic,
    side_from_receipt_logs)

USDC_ASSET_ID = 0
SCALE = 1e6  # USDC and CTF outcome tokens are both 6-decimals


# ── Pure, offline-testable core ──────────────────────────────────────────────
def _leg(e: dict, addr: str, token: int, buy: bool) -> Optional[tuple[float, float]]:
    """(usdc, tokens) of this event if it is addr trading `token` in the
    given direction, else None. Same asset semantics as the audited
    match_fill — kept identical on purpose."""
    mk = str(e.get("maker", "")).lower()
    tk = str(e.get("taker", "")).lower()
    m_id, t_id = int(e.get("makerAssetId", -1)), int(e.get("takerAssetId", -1))
    m_amt = float(e.get("makerAmountFilled", 0))
    t_amt = float(e.get("takerAmountFilled", 0))
    if mk == addr and ((m_id == USDC_ASSET_ID and t_id == token) if buy
                       else (m_id == token and t_id == USDC_ASSET_ID)):
        usdc, tok = (m_amt, t_amt) if buy else (t_amt, m_amt)
    elif tk == addr and ((m_id == token and t_id == USDC_ASSET_ID) if buy
                         else (m_id == USDC_ASSET_ID and t_id == token)):
        usdc, tok = (t_amt, m_amt) if buy else (m_amt, t_amt)
    else:
        return None
    if tok <= 0:
        return None
    return usdc / SCALE, tok / SCALE


def match_fill_txexact(events: list[dict], addr: str, token: int, side: str,
                       api_price: float, api_size: float, tol_price: float,
                       tol_size: float) -> dict:
    """Match one API fill (price, size in shares) against window events that
    each carry a `_tx` hash. Candidates: every single event + every per-tx
    aggregate. Never blends across txs.

    Returns {status: verified_txexact|mismatch_txexact|not_found,
             chain_price, chain_tokens, tx}."""
    a = addr.lower()
    buy = str(side).upper() != "SELL"
    per_tx: dict[str, list[tuple[float, float]]] = {}
    candidates: list[tuple[float, float, str]] = []  # (usdc, tok, tx)
    for e in events:
        leg = _leg(e, a, token, buy)
        if leg is None:
            continue
        tx = str(e.get("_tx", ""))
        candidates.append((leg[0], leg[1], tx))
        per_tx.setdefault(tx, []).append(leg)
    for tx, legs in per_tx.items():
        if len(legs) > 1:  # aggregate only when it differs from the singles
            candidates.append((sum(u for u, _ in legs),
                               sum(t for _, t in legs), tx))
    size_matched = []
    for usdc, tok, tx in candidates:
        if api_size > 0 and abs(tok - api_size) <= tol_size * api_size:
            size_matched.append((usdc / tok, tok, tx))
    if not size_matched:
        return {"status": "not_found", "chain_price": None,
                "chain_tokens": 0.0, "tx": None}
    best = min(size_matched, key=lambda c: abs(c[0] - float(api_price)))
    price, tok, tx = best
    if abs(price - float(api_price)) <= tol_price:
        return {"status": "verified_txexact", "chain_price": price,
                "chain_tokens": tok, "tx": tx}
    return {"status": "mismatch_txexact", "chain_price": price,
            "chain_tokens": tok, "tx": tx}


def v2_log_to_pseudo_event(lg: dict, addr: str) -> Optional[dict]:
    """Raw V2 fill log -> V1-args-shaped BUY candidate for match_fill_txexact.

    The V2 event carries (token, usdc, tokens) but NOT direction; candidates
    are emitted BUY-shaped and marked `_v2` so the run loop receipt-confirms
    any size-matched winner before it can verify or damn a claim."""
    topics = lg.get("topics") or []
    if len(topics) < 3 or _hex(topics[0]).lower() != FILL_TOPIC_V2:
        return None
    if _hex(topics[2]).lower()[-40:] != addr.lower().replace("0x", ""):
        return None
    w = _words(lg.get("data", "0x"))
    if len(w) < 4 or w[2] <= 0 or w[3] <= 0:
        return None
    txh = lg.get("transactionHash", "")
    return {"maker": addr.lower(), "taker": "",
            "makerAssetId": USDC_ASSET_ID, "takerAssetId": w[1],
            "makerAmountFilled": float(w[2]), "takerAmountFilled": float(w[3]),
            "_tx": txh.hex() if hasattr(txh, "hex") else str(txh), "_v2": True}


def adjudicate(counts: dict, n: int, max_notfound: float) -> str:
    """Pre-registered rule from the module docstring. Chain still wins."""
    if n == 0 or counts.get("rpc_error", 0) > n / 2:
        return "ERROR"
    if counts.get("mismatch_txexact", 0) > 0:
        return "STILL_DISCREPANT"
    if counts.get("not_found", 0) > max_notfound * n:
        return "THIN"
    return "VINDICATED"


def discrepant_from_audit(audit: dict, include_thin: bool = False) -> list[str]:
    verdicts = {"DISCREPANT"} | ({"THIN"} if include_thin else set())
    return sorted(a for a, r in (audit.get("results") or {}).items()
                  if r.get("verdict") in verdicts)


# ── Network run ──────────────────────────────────────────────────────────────
async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    if os.path.exists("/opt/pa2-shared/.env"):
        load_dotenv("/opt/pa2-shared/.env")
    from base_engine.data.blockchain_client import (
        BlockchainClient, EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT,
        ORDER_FILLED_EVENT_ABI)
    from web3 import Web3

    rpc_url, rpc_err = ac.resolve_rpc_url(args.rpc_url, args.rpc_env, os.environ)
    if rpc_err:
        print(rpc_err, file=sys.stderr)
        return 2

    with open(args.audit_json) as f:
        audit = json.load(f)
    roster = discrepant_from_audit(audit, args.include_thin)
    if args.traders:
        roster = [a.strip().lower() for a in args.traders.split(",") if a.strip()]
    if not roster:
        print("no DISCREPANT traders in the audit json — nothing to re-adjudicate")
        return 2
    print(f"re-adjudicating {len(roster)} traders "
          f"(tx-exact, DUAL-ERA V1+V2, window ±{args.window_s}s, "
          f"tol_price={args.tol_price}, tol_size={args.tol_size})",
          file=sys.stderr)

    bc = BlockchainClient(rpc_url=rpc_url)
    await bc.ensure_client()
    contracts = [bc.w3.eth.contract(
        address=Web3.to_checksum_address(c), abi=[ORDER_FILLED_EVENT_ABI])
        for c in dict.fromkeys((EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT))]

    first_err: list[str] = []

    def _note_err(e: Exception) -> None:
        if not first_err:
            first_err.append(repr(e))
            print(f"  FIRST ERROR (later ones counted silently): {e!r}",
                  file=sys.stderr)

    async def events_around(addr_cs: str, b0: int, b1: int) -> tuple[list[dict], int, int]:
        """Like the audit's, but every event keeps its tx hash as `_tx`."""
        out, errs, n_q = [], 0, 0
        for c in contracts:
            for field in ("maker", "taker"):
                for lo, hi in ac.block_chunks(b0, b1):
                    n_q += 1
                    try:
                        evs = await ac.get_logs_compat(
                            c.events.OrderFilled, lo, hi, {field: addr_cs})
                        for e in evs:
                            d = dict(e["args"])
                            txh = e.get("transactionHash", "")
                            d["_tx"] = txh.hex() if hasattr(txh, "hex") else str(txh)
                            out.append(d)
                    except Exception as e:
                        errs += 1
                        _note_err(e)
                    await asyncio.sleep(1.0 / max(args.rps, 0.1))
        return out, errs, n_q

    v2_addrs = [Web3.to_checksum_address(a)
                for a in (EXCHANGE_V2, NEGRISK_EXCHANGE_V2)]

    async def events_around_v2(addr: str, b0: int, b1: int
                               ) -> tuple[list[dict], int, int]:
        """V2-era fills by this order owner (server-side topic filter),
        BUY-shaped pseudo-events; ONE getLogs covers both V2 exchanges."""
        out, errs, n_q = [], 0, 0
        for lo, hi in ac.block_chunks(b0, b1):
            n_q += 1
            try:
                logs = await bc.w3.eth.get_logs({
                    "fromBlock": lo, "toBlock": hi, "address": v2_addrs,
                    "topics": [FILL_TOPIC_V2, None, addr_topic(addr)]})
                for lg in logs:
                    pe = v2_log_to_pseudo_event(dict(lg), addr)
                    if pe is not None:
                        out.append(pe)
            except Exception as e:
                errs += 1
                _note_err(e)
            await asyncio.sleep(1.0 / max(args.rps, 0.1))
        return out, errs, n_q

    v2_sides: dict[tuple[str, str], str] = {}  # (tx, token) -> receipt side

    latest_blk = await bc.w3.eth.get_block("latest")
    latest_num, latest_ts = int(latest_blk["number"]), int(latest_blk["timestamp"])
    anchors: list[tuple[int, int]] = []

    async def _get_block_ts(b: int) -> int:
        await asyncio.sleep(1.0 / max(args.rps, 0.1))
        return int((await bc.w3.eth.get_block(b))["timestamp"])

    results: dict[str, dict] = {}
    total: dict[str, int] = {}
    samples_done = samples_rpc_err = 0
    for ai, addr in enumerate(roster):
        path = os.path.join(args.cache, f"{addr}.json")
        if not os.path.exists(path):
            results[addr] = {"verdict": "ERROR", "detail": "no cached history"}
            continue
        with open(path) as f:
            blob = json.load(f)
        trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
        picks = ac.sample_fills(trades, args.per_trader, args.seed + ai)
        addr_cs = Web3.to_checksum_address(addr)
        counts: dict[str, int] = {}
        mismatches = []
        for t in picks:
            ts = fc.parse_ts(t["timestamp"])
            if ts is None:
                continue
            epoch = int(ts.timestamp())
            try:
                center = await ac.locate_block_by_ts(
                    epoch, _get_block_ts, latest_num, latest_ts, anchors,
                    tol_s=args.window_s / 3)
                if center is None:
                    raise RuntimeError(f"block locate did not converge ts={epoch}")
                span = max(args.pad_blocks, int(args.window_s / 2.1)) + 300
                events, errs, n_q = await events_around(
                    addr_cs, center - span, center + span)
                ev2, errs2, n_q2 = await events_around_v2(
                    addr, center - span, center + span)
                events, errs, n_q = events + ev2, errs + errs2, n_q + n_q2
                samples_done += 1
                if errs == n_q:
                    counts["rpc_error"] = counts.get("rpc_error", 0) + 1
                    samples_rpc_err += 1
                    if (args.fail_fast > 0 and samples_done >= args.fail_fast
                            and samples_rpc_err == samples_done):
                        print(f"\nFAIL-FAST: first {samples_done} samples ALL "
                              f"rpc_error — endpoint not serving these queries;"
                              f" first error: "
                              f"{first_err[0] if first_err else '?'}",
                              file=sys.stderr)
                        return 3
                    continue
            except Exception as e:
                _note_err(e)
                counts["rpc_error"] = counts.get("rpc_error", 0) + 1
                samples_done += 1
                samples_rpc_err += 1
                if (args.fail_fast > 0 and samples_done >= args.fail_fast
                        and samples_rpc_err == samples_done):
                    print(f"\nFAIL-FAST: first {samples_done} samples ALL failed"
                          f" — first error: "
                          f"{first_err[0] if first_err else '?'}", file=sys.stderr)
                    return 3
                continue
            # V2 candidates are direction-ambiguous: receipt-confirm any
            # size-matched V2 winner; a SELL/unconfirmable one is dropped
            # from the pool and matching re-runs without it.
            v2_txs = {e["_tx"] for e in events if e.get("_v2")}
            pool = events
            while True:
                m = match_fill_txexact(
                    pool, addr, int(t["tokenId"]), t.get("side", "BUY"),
                    float(t.get("price", 0)), float(t.get("size", 0)),
                    args.tol_price, args.tol_size)
                if m["status"] == "not_found" or m["tx"] not in v2_txs:
                    break
                ck = (m["tx"], str(t["tokenId"]))
                side = v2_sides.get(ck)
                if side is None:
                    try:
                        await asyncio.sleep(1.0 / max(args.rps, 0.1))
                        rcpt = await bc.w3.eth.get_transaction_receipt(m["tx"])
                        side = side_from_receipt_logs(
                            [dict(lg) for lg in rcpt["logs"]], addr,
                            str(t["tokenId"])) or "UNCONFIRMED"
                    except Exception as e:
                        _note_err(e)
                        side = "UNCONFIRMED"
                    v2_sides[ck] = side
                want = "SELL" if str(t.get("side", "BUY")).upper() == "SELL" \
                    else "BUY"
                if side == want:
                    break
                pool = [e for e in pool
                        if not (e.get("_v2") and e.get("_tx") == m["tx"])]
            counts[m["status"]] = counts.get(m["status"], 0) + 1
            if m["status"] == "mismatch_txexact":
                mismatches.append({
                    "ts": str(t["timestamp"]), "token": t["tokenId"],
                    "api_price": t.get("price"), "api_size": t.get("size"),
                    "chain_price": round(m["chain_price"], 4), "tx": m["tx"]})
            await asyncio.sleep(1.0 / max(args.rps, 0.1))
        n = sum(counts.values())
        v = adjudicate(counts, n, args.max_notfound)
        results[addr] = {"verdict": v, "n": n, "counts": counts,
                         "mismatches": mismatches,
                         "audit_verdict": (audit.get("results", {})
                                           .get(addr, {}).get("verdict"))}
        for k, x in counts.items():
            total[k] = total.get(k, 0) + x
        print(f"  [{ai + 1}/{len(roster)}] {addr[:14]}…  {v}  {counts}",
              file=sys.stderr)

    n_all = sum(total.values())
    vindicated = sorted(a for a, r in results.items()
                        if r["verdict"] == "VINDICATED")
    print("\n" + "=" * 78)
    print("  TX-EXACT RE-ADJUDICATION — window-blend artifact class removed")
    print(f"  traders={len(roster)}  sampled fills={n_all}  "
          f"verified={total.get('verified_txexact', 0)}  "
          f"mismatch={total.get('mismatch_txexact', 0)}  "
          f"not-found={total.get('not_found', 0)}  "
          f"rpc-err={total.get('rpc_error', 0)}")
    if first_err:
        print(f"  first error of the run: {first_err[0][:300]}")
    for addr, r in results.items():
        print(f"    {addr[:14]}…  {r['verdict']:<17} "
              f"{r.get('counts', r.get('detail'))}")
        for mm in r.get("mismatches", [])[:3]:
            print(f"      MISMATCH {mm}")
    print(f"  VINDICATED: {len(vindicated)}/{len(roster)}")
    print("  READ: VINDICATED = every size-matched tx also price-matches; the")
    print("  audit's mismatch was the window blend. PROPOSED for a SECOND")
    print("  shadow cohort (own start date) — operator decision required.")
    print("  STILL_DISCREPANT = a size-matched tx at a different price exists;")
    print("  chain wins, stays excluded. THIN/ERROR = still not enough chain")
    print("  evidence; wider --window-s / different RPC before any conclusion.")
    print("=" * 78)
    fc.write_json_atomic(args.out, fc.json_safe(
        {"results": results, "totals": total, "vindicated": vindicated,
         "first_error": first_err[0] if first_err else None,
         "params": {"window_s": args.window_s, "tol_price": args.tol_price,
                    "tol_size": args.tol_size, "per_trader": args.per_trader,
                    "seed": args.seed}}))
    print(f"full results -> {args.out}")
    return 0


# ── Self-test (no network) ───────────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — tx-exact matching core (no network)\n")
    ok = True
    A = "0xAbCd000000000000000000000000000000000001"

    def ev(tx, m_amt, t_amt, maker=A, taker="0x9", m_id=0, t_id=777):
        return {"maker": maker, "taker": taker, "makerAssetId": m_id,
                "takerAssetId": t_id, "makerAmountFilled": m_amt,
                "takerAmountFilled": t_amt, "_tx": tx}

    # the artifact class itself: two REAL trades in one window, 100 tok @0.60
    # and 100 tok @0.80 — the OLD matcher blends to 0.70 and calls BOTH rows
    # mismatched; tx-exact verifies each row against its own tx
    window = [ev("0xt1", 60_000_000, 100_000_000),
              ev("0xt2", 80_000_000, 100_000_000)]
    old = ac.match_fill(window, A, 777, "BUY", 0.60, 0.02)
    m1 = match_fill_txexact(window, A, 777, "BUY", 0.60, 100.0, 0.02, 0.05)
    m2 = match_fill_txexact(window, A, 777, "BUY", 0.80, 100.0, 0.02, 0.05)
    ok1 = (old["status"] == "price_mismatch"
           and m1["status"] == "verified_txexact" and m1["tx"] == "0xt1"
           and m2["status"] == "verified_txexact" and m2["tx"] == "0xt2")
    print(f"  [blend artifact] old mismatches, tx-exact verifies both rows : {ok1}")
    ok &= ok1

    # a REAL discrepancy survives: size-matched tx at a different price
    m = match_fill_txexact([ev("0xt1", 90_000_000, 100_000_000)],
                           A, 777, "BUY", 0.60, 100.0, 0.02, 0.05)
    ok2 = m["status"] == "mismatch_txexact" and abs(m["chain_price"] - 0.90) < 1e-9
    print(f"  [real mismatch] size-matched tx at 0.90 vs api 0.60 stays damning : {ok2}")
    ok &= ok2

    # multi-fill tx: two partial fills in ONE tx aggregate to the API row
    split = [ev("0xt1", 30_000_000, 50_000_000), ev("0xt1", 30_000_000, 50_000_000)]
    m = match_fill_txexact(split, A, 777, "BUY", 0.60, 100.0, 0.02, 0.05)
    ok3 = (m["status"] == "verified_txexact"
           and abs(m["chain_tokens"] - 100.0) < 1e-9)
    print(f"  [same-tx split] partial fills aggregate within their tx : {ok3}")
    ok &= ok3

    # single fill of a multi-fill tx still matches as a single event
    m = match_fill_txexact(split, A, 777, "BUY", 0.60, 50.0, 0.02, 0.05)
    ok4 = m["status"] == "verified_txexact" and abs(m["chain_tokens"] - 50.0) < 1e-9
    print(f"  [single leg] one fill of a split tx matches at its own size : {ok4}")
    ok &= ok4

    # no size match anywhere -> not_found (never a blended mismatch)
    m = match_fill_txexact(window, A, 777, "BUY", 0.60, 37.0, 0.02, 0.05)
    ok5 = m["status"] == "not_found"
    print(f"  [no size match] wrong size -> not_found, never blended : {ok5}")
    ok &= ok5

    # taker-side reversed asset semantics
    tk = {"maker": "0x9", "taker": A, "makerAssetId": 777, "takerAssetId": 0,
          "makerAmountFilled": 50_000_000, "takerAmountFilled": 33_000_000,
          "_tx": "0xt9"}
    m = match_fill_txexact([tk], A, 777, "BUY", 0.66, 50.0, 0.02, 0.05)
    ok6 = m["status"] == "verified_txexact"
    print(f"  [taker BUY] reversed asset semantics verified : {ok6}")
    ok &= ok6

    ok7 = (adjudicate({"verified_txexact": 20}, 20, 0.2) == "VINDICATED"
           and adjudicate({"verified_txexact": 19, "mismatch_txexact": 1}, 20, 0.2)
           == "STILL_DISCREPANT"
           and adjudicate({"verified_txexact": 14, "not_found": 6}, 20, 0.2) == "THIN"
           and adjudicate({"rpc_error": 15, "verified_txexact": 5}, 20, 0.2) == "ERROR"
           and adjudicate({}, 0, 0.2) == "ERROR")
    print(f"  [adjudicate] vindicated/still/thin/error : {ok7}")
    ok &= ok7

    audit = {"results": {"0xa": {"verdict": "DISCREPANT"},
                         "0xb": {"verdict": "CLEAN"},
                         "0xc": {"verdict": "THIN"}}}
    ok8 = (discrepant_from_audit(audit) == ["0xa"]
           and discrepant_from_audit(audit, include_thin=True) == ["0xa", "0xc"])
    print(f"  [roster] DISCREPANT (and optional THIN) from audit json : {ok8}")
    ok &= ok8

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Tx-exact re-adjudication of DISCREPANT audit traders")
    ap.add_argument("--audit-json", default="/tmp/chain_audit.json",
                    dest="audit_json",
                    help="audit_roster_chain.py output; roster = its DISCREPANT")
    ap.add_argument("--traders", default="",
                    help="explicit comma-separated addresses (overrides audit json)")
    ap.add_argument("--include-thin", action="store_true", dest="include_thin",
                    help="also re-adjudicate THIN traders (pair with a wider --window-s)")
    ap.add_argument("--cache", default="/tmp/copyable_cache")
    ap.add_argument("--per-trader", type=int, default=20, dest="per_trader")
    ap.add_argument("--window-s", type=int, default=900, dest="window_s")
    ap.add_argument("--pad-blocks", type=int, default=600, dest="pad_blocks")
    ap.add_argument("--tol-price", type=float, default=0.02, dest="tol_price")
    ap.add_argument("--tol-size", type=float, default=0.05, dest="tol_size",
                    help="relative size tolerance for a candidate to count as "
                         "the API row's trade")
    ap.add_argument("--max-notfound", type=float, default=0.2, dest="max_notfound")
    ap.add_argument("--rps", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="/tmp/readjudicate.json")
    ap.add_argument("--rpc-url", default="", dest="rpc_url")
    ap.add_argument("--rpc-env", default="", dest="rpc_env")
    ap.add_argument("--fail-fast", type=int, default=12, dest="fail_fast")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
