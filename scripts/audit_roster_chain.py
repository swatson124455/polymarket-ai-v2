#!/usr/bin/env python3
"""Per-fill OrderFilled chain audit for a copy-trader roster.

WHY (binding rule, docs/MB_COPYTRADER_CONTEXT.md §7.5): chain wins over
API/DB on any mismatch, and NO money decision may name a trader until their
API-reported fills are spot-verified against on-chain OrderFilled events.
This is that audit. It samples fills from each rostered trader's cached
history (the same cache the graders ran on) and checks each against Polygon:
did this address actually trade this token, at this price, at this time?

WHAT IT CHECKS per sampled fill (both CTF exchanges — the main Exchange AND
the NegRisk Exchange; neg-risk fills only appear on the latter):
  events in a block window around the fill's timestamp where the trader is
  maker or taker (both indexed → server-side filtered) and the fill's token
  is one of the asset ids, direction-consistent with BUY/SELL:
    maker+BUY : makerAssetId==0(USDC), takerAssetId==token
    taker+BUY : makerAssetId==token,   takerAssetId==0
    (SELL mirrored)
  price = usdc_amount / token_amount across matching events
  (volume-weighted), must sit within --tol-price of the API price.

VERDICTS per trader (strict — the audit is a tripwire, not a gate):
  CLEAN      every sampled fill chain-verified
  DISCREPANT >=1 fill found on chain at a materially different price, or
             direction-inconsistent — CHAIN WINS, trader's API record is
             suspect, exclude from any money decision until explained
  THIN       >=--max-notfound of samples not found on chain at all (RPC gaps
             and sub-window latency happen; a high rate is the tripwire)
  ERROR      RPC failures dominated; audit says nothing about this trader

GLOBAL SANITY: if EVERY sample across ALL traders is not-found while RPC
errors are low, the address model is wrong (proxy-wallet vs signer) — the
script says AUDIT-INCONCLUSIVE loudly instead of failing every trader.

SAFETY: READ-ONLY. RPC GETs only. No DB writes, no orders, no bot code.

INVOCATION (VPS, after a walk-forward PASS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc2 \
      venv/bin/python /tmp/mbpc2/scripts/audit_roster_chain.py \
      --from-json /tmp/walkforward3.json --cache /tmp/copyable_cache \
      --rpc-url https://polygon-rpc.com \
      | tee /tmp/chain_audit.log
    ... --self-test   # offline matching-logic check, no network

RPC NOTE (2026-07-10): the endpoint MUST serve eth_getLogs.
polygon-bor-rpc.publicnode.com (the VPS POLYGON_RPC) 403s getLogs while
answering block lookups — probe with a raw eth_getLogs curl before a long
run, or rely on --fail-fast (aborts in ~1 min if every sample errors).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_copyable_traders as fc  # noqa: E402

USDC_ASSET_ID = 0
SCALE = 1e6  # USDC and CTF outcome tokens are both 6-decimals


# ── Pure, offline-testable core ──────────────────────────────────────────────
def match_fill(events: list[dict], addr: str, token: int, side: str,
               api_price: float, tol_price: float) -> dict:
    """Match one API fill against decoded OrderFilled events from its window.

    events: [{maker, taker, makerAssetId, takerAssetId,
              makerAmountFilled, takerAmountFilled}] (already addr-filtered
    by the indexed-topic query; re-checked here anyway).
    Returns {status: verified|price_mismatch|not_found,
             chain_price, chain_tokens} — aggregation across events handles
    one API row filled by several maker orders."""
    a = addr.lower()
    buy = str(side).upper() != "SELL"
    tok_total = usdc_total = 0.0
    for e in events:
        mk = str(e.get("maker", "")).lower()
        tk = str(e.get("taker", "")).lower()
        m_id, t_id = int(e.get("makerAssetId", -1)), int(e.get("takerAssetId", -1))
        m_amt, t_amt = float(e.get("makerAmountFilled", 0)), float(e.get("takerAmountFilled", 0))
        if mk == a and ((m_id == USDC_ASSET_ID and t_id == token) if buy
                        else (m_id == token and t_id == USDC_ASSET_ID)):
            usdc, tok = (m_amt, t_amt) if buy else (t_amt, m_amt)
        elif tk == a and ((m_id == token and t_id == USDC_ASSET_ID) if buy
                          else (m_id == USDC_ASSET_ID and t_id == token)):
            usdc, tok = (t_amt, m_amt) if buy else (m_amt, t_amt)
        else:
            continue
        if tok <= 0:
            continue
        tok_total += tok / SCALE
        usdc_total += usdc / SCALE
    if tok_total <= 0:
        return {"status": "not_found", "chain_price": None, "chain_tokens": 0.0}
    chain_price = usdc_total / tok_total
    if abs(chain_price - float(api_price)) <= tol_price:
        return {"status": "verified", "chain_price": chain_price,
                "chain_tokens": tok_total}
    return {"status": "price_mismatch", "chain_price": chain_price,
            "chain_tokens": tok_total}


def trader_verdict(counts: dict, n: int, max_notfound: float) -> str:
    if n == 0:
        return "ERROR"
    if counts.get("rpc_error", 0) > n / 2:
        return "ERROR"
    if counts.get("price_mismatch", 0) > 0:
        return "DISCREPANT"
    if counts.get("not_found", 0) > max_notfound * n:
        return "THIN"
    return "CLEAN"


def sample_fills(trades: list[dict], k: int, seed: int) -> list[dict]:
    """BUY fills only (the graders score first BUYs), numeric token ids,
    spread across the record: sort by time, stride-sample."""
    rows = [t for t in trades
            if str(t.get("side", "")).upper() == "BUY"
            and str(t.get("tokenId", "")).isdigit()
            and t.get("timestamp") is not None
            and float(t.get("size", 0)) > 0]
    rows.sort(key=lambda t: str(t["timestamp"]))
    if len(rows) <= k:
        return rows
    rng = random.Random(seed)
    stride = len(rows) / k
    return [rows[min(len(rows) - 1, int(i * stride) + rng.randrange(max(1, int(stride))))]
            for i in range(k)]


GETLOGS_CHUNK = 900  # public Polygon RPCs commonly cap eth_getLogs at ~1k blocks


def block_chunks(b0: int, b1: int, chunk: int = GETLOGS_CHUNK) -> list[tuple[int, int]]:
    """Split [b0, b1] into inclusive sub-ranges of at most `chunk` blocks."""
    spans, lo = [], b0
    while lo <= b1:
        hi = min(lo + chunk - 1, b1)
        spans.append((lo, hi))
        lo = hi + 1
    return spans


async def get_logs_compat(event, b0: int, b1: int, filters: dict):
    """web3 v7 renamed get_logs kwargs to from_block/to_block; v6- used
    fromBlock/toBlock. The camelCase call TypeError'd on EVERY fill under
    web3 7.5 (2026-07-10 run: 580/580 'rpc_error', zero verdicts) — the
    snake_case path is primary, camelCase is the fallback for old libs.
    Module-level so tests can bind it against the INSTALLED web3 signature."""
    try:
        return await event.get_logs(argument_filters=filters,
                                    from_block=b0, to_block=b1)
    except TypeError:
        return await event.get_logs(argument_filters=filters,
                                    fromBlock=b0, toBlock=b1)


def resolve_rpc_url(rpc_url_arg: str, rpc_env_arg: str,
                    environ: dict) -> tuple[Optional[str], Optional[str]]:
    """(url, error). Explicit --rpc-url wins; --rpc-env reads a named env var
    (so paid-endpoint URLs with embedded API keys never appear on a command
    line or in a pasted log); neither -> None = BlockchainClient defaults.
    2026-07-10: the default endpoint (publicnode) 403s eth_getLogs — the
    audit needs a logs-serving endpoint without touching shared modules."""
    if rpc_url_arg:
        return rpc_url_arg, None
    if rpc_env_arg:
        v = environ.get(rpc_env_arg, "")
        if not v:
            return None, f"--rpc-env {rpc_env_arg} is not set in the environment"
        return v, None
    return None, None


# ── Network run ──────────────────────────────────────────────────────────────
async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    if os.path.exists("/opt/pa2-shared/.env"):      # shared VPS env (no override)
        load_dotenv("/opt/pa2-shared/.env")
    from base_engine.data.blockchain_client import (
        BlockchainClient, EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT,
        ORDER_FILLED_EVENT_ABI)

    rpc_url, rpc_err = resolve_rpc_url(args.rpc_url, args.rpc_env, os.environ)
    if rpc_err:
        print(rpc_err, file=sys.stderr)
        return 2

    # roster
    if args.traders:
        roster = [a.strip().lower() for a in args.traders.split(",") if a.strip()]
    else:
        with open(args.from_json) as f:
            wf = json.load(f)
        roster = [a.lower() for a in wf.get("ever_rostered_primary", [])]
    if not roster:
        print("no roster addresses (check --from-json / --traders)")
        return 2

    bc = BlockchainClient(rpc_url=rpc_url)
    await bc.ensure_client()
    from web3 import Web3
    contracts = []
    for caddr in dict.fromkeys((EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT)):
        contracts.append(bc.w3.eth.contract(
            address=Web3.to_checksum_address(caddr), abi=[ORDER_FILLED_EVENT_ABI]))

    first_err: list[str] = []  # first real exception of the run, verbatim

    def _note_err(e: Exception) -> None:
        if not first_err:
            first_err.append(repr(e))
            print(f"  FIRST ERROR (all later ones counted silently): {e!r}",
                  file=sys.stderr)

    async def events_around(addr_cs: str, b0: int, b1: int) -> tuple[list[dict], int, int]:
        """OrderFilled events on both exchanges where addr is maker or taker.
        Ranges chunked to <=GETLOGS_CHUNK blocks (public-RPC getLogs caps).
        Returns (events, n_errors, n_queries)."""
        out, errs, n_q = [], 0, 0
        for c in contracts:
            for field in ("maker", "taker"):
                for lo, hi in block_chunks(b0, b1):
                    n_q += 1
                    try:
                        evs = await get_logs_compat(
                            c.events.OrderFilled, lo, hi, {field: addr_cs})
                        out.extend(dict(e["args"]) for e in evs)
                    except Exception as e:
                        errs += 1
                        _note_err(e)
        return out, errs, n_q

    results: dict[str, dict] = {}
    prev_ts = prev_block = None
    total = {"verified": 0, "price_mismatch": 0, "not_found": 0, "rpc_error": 0}
    samples_done = samples_rpc_err = 0  # fail-fast: don't grind a dead endpoint
    for ai, addr in enumerate(roster):
        path = os.path.join(args.cache, f"{addr}.json")
        if not os.path.exists(path):
            results[addr] = {"verdict": "ERROR", "detail": "no cached history"}
            continue
        with open(path) as f:
            blob = json.load(f)
        trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
        picks = sample_fills(trades, args.per_trader, args.seed + ai)
        addr_cs = Web3.to_checksum_address(addr)
        counts: dict[str, int] = {}
        mismatches = []
        for t in picks:
            ts = fc.parse_ts(t["timestamp"])
            if ts is None:
                continue
            epoch = int(ts.timestamp())
            try:
                # block for this ts: binary-search once, then reuse by offset
                # for nearby fills (Polygon ~2.1s/block), generously padded
                if prev_ts is not None and abs(epoch - prev_ts) < 6 * 3600:
                    center = prev_block + int((epoch - prev_ts) / 2.1)
                else:
                    center = await bc.get_block_number_from_timestamp(epoch)
                prev_ts, prev_block = epoch, center
                span = max(args.pad_blocks, int(args.window_s / 2.1)) + 300
                events, errs, n_q = await events_around(addr_cs, center - span, center + span)
                samples_done += 1
                if errs == n_q:  # every query failed
                    counts["rpc_error"] = counts.get("rpc_error", 0) + 1
                    samples_rpc_err += 1
                    if (args.fail_fast > 0 and samples_done >= args.fail_fast
                            and samples_rpc_err == samples_done):
                        print(f"\nFAIL-FAST: first {samples_done} samples ALL rpc_error"
                              f" — the endpoint is not serving these queries; aborting"
                              f" instead of wasting the run.", file=sys.stderr)
                        print(f"first error: {first_err[0] if first_err else '?'}",
                              file=sys.stderr)
                        print("retry with --rpc-url <logs-serving endpoint> or"
                              " --rpc-env ALCHEMY_HTTP (etc.); --fail-fast 0 disables.",
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
                    print(f"\nFAIL-FAST: first {samples_done} samples ALL failed —"
                          f" aborting; first error: "
                          f"{first_err[0] if first_err else '?'}", file=sys.stderr)
                    return 3
                continue
            m = match_fill(events, addr, int(t["tokenId"]), t.get("side", "BUY"),
                           float(t.get("price", 0)), args.tol_price)
            counts[m["status"]] = counts.get(m["status"], 0) + 1
            if m["status"] == "price_mismatch":
                mismatches.append({"ts": str(t["timestamp"]), "token": t["tokenId"],
                                   "api_price": t.get("price"),
                                   "chain_price": round(m["chain_price"], 4)})
            await asyncio.sleep(1.0 / max(args.rps, 0.1))
        n = sum(counts.values())
        v = trader_verdict(counts, n, args.max_notfound)
        results[addr] = {"verdict": v, "n": n, "counts": counts,
                         "mismatches": mismatches}
        for k, x in counts.items():
            total[k] = total.get(k, 0) + x
        print(f"  [{ai + 1}/{len(roster)}] {addr[:14]}…  {v}  "
              f"{counts}", file=sys.stderr)

    # global sanity: address-model mismatch reads as everyone-not-found
    n_all = sum(total.values())
    inconclusive = (n_all > 0 and total.get("verified", 0) == 0
                    and total.get("price_mismatch", 0) == 0
                    and total.get("rpc_error", 0) < n_all / 4)

    print("\n" + "=" * 78)
    print("  PER-FILL ORDERFILLED CHAIN AUDIT — chain wins on any mismatch")
    print(f"  roster={len(roster)}  sampled fills={n_all}  "
          f"verified={total.get('verified', 0)}  mismatch={total.get('price_mismatch', 0)}"
          f"  not-found={total.get('not_found', 0)}  rpc-err={total.get('rpc_error', 0)}")
    if first_err:
        print(f"  first error of the run: {first_err[0][:300]}")
    if inconclusive:
        print("  >>> AUDIT-INCONCLUSIVE: zero chain matches anywhere with low RPC")
        print("  >>> errors — the address model (proxy vs signer) is wrong for this")
        print("  >>> query, NOT evidence against the traders. Fix the audit first.")
    for addr, r in results.items():
        print(f"    {addr[:14]}…  {r['verdict']:<11} {r.get('counts', r.get('detail'))}")
        for mm in r.get("mismatches", [])[:3]:
            print(f"      MISMATCH {mm}")
    clean = sorted(a for a, r in results.items() if r["verdict"] == "CLEAN")
    print(f"  CLEAN traders: {len(clean)}/{len(roster)}")
    print("  READ: CLEAN = API record spot-verified on chain — eligible for the")
    print("  fill-quality gate. DISCREPANT = chain wins, exclude and investigate.")
    print("  THIN/ERROR = audit could not see enough chain evidence; re-run with")
    print("  a wider --window-s / different RPC before drawing any conclusion.")
    print("=" * 78)
    fc.write_json_atomic(args.out, fc.json_safe(
        {"results": results, "totals": total,
         "inconclusive": inconclusive, "clean": clean,
         "first_error": first_err[0] if first_err else None}))
    print(f"full results -> {args.out}")
    return 0


# ── Self-test (no network) ───────────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — chain-audit matching core (no network)\n")
    ok = True
    A = "0xAbCd000000000000000000000000000000000001"
    ev_maker_buy = {"maker": A, "taker": "0x9", "makerAssetId": 0,
                    "takerAssetId": 777, "makerAmountFilled": 60_000_000,
                    "takerAmountFilled": 100_000_000}   # $60 for 100 tok @0.60
    m = match_fill([ev_maker_buy], A, 777, "BUY", 0.60, 0.02)
    ok1 = m["status"] == "verified" and abs(m["chain_price"] - 0.60) < 1e-9
    print(f"  [maker BUY] verified at 0.60 : {ok1}"); ok &= ok1

    ev_taker_buy = {"maker": "0x9", "taker": A, "makerAssetId": 777,
                    "takerAssetId": 0, "makerAmountFilled": 50_000_000,
                    "takerAmountFilled": 33_000_000}    # 50 tok for $33 @0.66
    m = match_fill([ev_taker_buy], A, 777, "BUY", 0.66, 0.02)
    ok2 = m["status"] == "verified"
    print(f"  [taker BUY] reversed asset semantics verified : {ok2}"); ok &= ok2

    m = match_fill([ev_maker_buy], A, 777, "BUY", 0.90, 0.02)
    ok3 = m["status"] == "price_mismatch" and m["chain_price"] is not None
    print(f"  [mismatch] api 0.90 vs chain 0.60 -> price_mismatch : {ok3}"); ok &= ok3

    ok4 = (match_fill([ev_maker_buy], A, 888, "BUY", 0.6, 0.02)["status"] == "not_found"
           and match_fill([ev_maker_buy], "0xDEAD", 777, "BUY", 0.6, 0.02)["status"] == "not_found"
           and match_fill([ev_maker_buy], A, 777, "SELL", 0.6, 0.02)["status"] == "not_found")
    print(f"  [no-match] wrong token/addr/direction -> not_found : {ok4}"); ok &= ok4

    half = dict(ev_maker_buy, makerAmountFilled=30_000_000, takerAmountFilled=50_000_000)
    m = match_fill([half, half], A, 777, "BUY", 0.60, 0.02)
    ok5 = m["status"] == "verified" and abs(m["chain_tokens"] - 100.0) < 1e-9
    print(f"  [aggregate] two partial fills sum to the API row : {ok5}"); ok &= ok5

    ok6 = (trader_verdict({"verified": 20}, 20, 0.2) == "CLEAN"
           and trader_verdict({"verified": 19, "price_mismatch": 1}, 20, 0.2) == "DISCREPANT"
           and trader_verdict({"verified": 14, "not_found": 6}, 20, 0.2) == "THIN"
           and trader_verdict({"rpc_error": 15, "verified": 5}, 20, 0.2) == "ERROR")
    print(f"  [verdicts] clean/discrepant/thin/error : {ok6}"); ok &= ok6

    trades = [{"side": "BUY", "tokenId": str(i), "timestamp": i * 1000,
               "size": 5, "price": 0.5} for i in range(100)]
    trades += [{"side": "SELL", "tokenId": "999", "timestamp": 1, "size": 5,
                "price": 0.5}]
    s = sample_fills(trades, 10, 7)
    ok7 = (len(s) == 10 and all(t["side"] == "BUY" for t in s)
           and s == sorted(s, key=lambda t: str(t["timestamp"])))
    print(f"  [sample] 10 BUYs, time-spread, SELLs excluded : {ok7}"); ok &= ok7

    # get_logs_compat must work against BOTH web3 calling conventions
    # (the 2026-07-10 audit run failed 580/580 on exactly this)
    class _V7Event:  # web3 >= 7: snake_case kwargs
        async def get_logs(self, argument_filters=None, from_block=None,
                           to_block=None, block_hash=None):
            return [("v7", from_block, to_block, argument_filters)]

    class _V6Event:  # web3 < 7: camelCase kwargs
        async def get_logs(self, argument_filters=None, fromBlock=None,
                           toBlock=None, block_hash=None):
            return [("v6", fromBlock, toBlock, argument_filters)]

    r7 = asyncio.run(get_logs_compat(_V7Event(), 10, 20, {"maker": "0xA"}))
    r6 = asyncio.run(get_logs_compat(_V6Event(), 10, 20, {"maker": "0xA"}))
    ok8 = (r7 == [("v7", 10, 20, {"maker": "0xA"})]
           and r6 == [("v6", 10, 20, {"maker": "0xA"})])
    print(f"  [get_logs] compat binds on web3 v7 AND v6 signatures : {ok8}"); ok &= ok8

    ok9 = (block_chunks(0, 1800) == [(0, 899), (900, 1799), (1800, 1800)]
           and block_chunks(5, 5) == [(5, 5)]
           and all(hi - lo + 1 <= GETLOGS_CHUNK for lo, hi in block_chunks(0, 10_000)))
    print(f"  [chunks] getLogs ranges capped at {GETLOGS_CHUNK} blocks : {ok9}"); ok &= ok9

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Per-fill OrderFilled chain audit for a roster")
    ap.add_argument("--from-json", default="/tmp/walkforward3.json", dest="from_json",
                    help="walkforward output; roster = ever_rostered_primary")
    ap.add_argument("--traders", default="", help="explicit comma-separated addresses (overrides --from-json)")
    ap.add_argument("--cache", default="/tmp/copyable_cache")
    ap.add_argument("--per-trader", type=int, default=20, dest="per_trader")
    ap.add_argument("--window-s", type=int, default=900, dest="window_s",
                    help="half-window around each fill's timestamp")
    ap.add_argument("--pad-blocks", type=int, default=600, dest="pad_blocks")
    ap.add_argument("--tol-price", type=float, default=0.02, dest="tol_price")
    ap.add_argument("--max-notfound", type=float, default=0.2, dest="max_notfound",
                    help="not-found fraction above which a trader is THIN")
    ap.add_argument("--rps", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="/tmp/chain_audit.json")
    ap.add_argument("--rpc-url", default="", dest="rpc_url",
                    help="explicit Polygon RPC endpoint (must serve eth_getLogs; "
                         "the publicnode default 403s it, 2026-07-10)")
    ap.add_argument("--rpc-env", default="", dest="rpc_env",
                    help="name of an env var holding the RPC URL (keeps keyed "
                         "URLs out of command lines/logs), e.g. ALCHEMY_HTTP")
    ap.add_argument("--fail-fast", type=int, default=12, dest="fail_fast",
                    help="abort if the first N samples ALL rpc_error (0=off)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
