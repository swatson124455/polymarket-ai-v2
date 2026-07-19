#!/usr/bin/env python3
"""Persistent chain-fill cache + multi-address lifetime sweep (2026-07-19).

WHY (operator-approved speedups 1+2, "verify no function is lost"):
the per-trader lifetime sweep (~55M blocks) dominates chain_deep_dive's cost
(~1.5-2h/trader) and is thrown away after every run. This module adds:
  1. a PER-ADDRESS fill cache under <cache>/chain_fills/ — swept fills +
     receipt-confirmed V2 sides persist across runs; a re-dive re-sweeps only
     the incremental block gap;
  2. populate_multi(): ONE sweep of the range for N addresses at once (the
     V1 maker/taker filters and the V2 owner topic all accept OR-lists),
     demuxed through the SAME reconstruct functions chain_deep_dive uses.

FUNCTION-PRESERVATION RULES (the design contract):
  - The gate/verdict logic is untouched. Only fill ACQUISITION changes, and
    only behind chain_deep_dive's --fill-cache-dir flag (default OFF = the
    byte-identical old path).
  - The sweep layer stays DIRECTION-PENDING: cached/served V2 fills carry
    side=None exactly like sweep_lifetime's output. Cached sides live in a
    separate per-address map keyed "tx|token_id" (side is per (tx, token),
    NOT per tx) and are consumed ONLY by classify_v2_directions via its
    known_sides parameter — as successful resolutions, because they ARE
    receipt results, just fetched in an earlier run. A re-dive can thereby
    resolve MORE txs than a fresh capped run — strictly more evidence,
    never less.
  - Cache is written ONLY from sweeps with leaf_fail == 0 (a lossy sweep is
    never persisted); served only when it fully covers the requested range.
    Anything else falls back to the exact existing per-address sweep.
  - Served fills are filtered to the requested [from_b, to_b] — a narrower
    re-dive never sees extra fills.
  - Multi-sweep demux mirrors the per-address queries STRUCTURALLY: V1 runs
    the same 4 query streams (2 contracts x maker/taker) with an address
    LIST, and each event is reconstructed once per matching roster address
    per stream — reproducing even the both-sides-roster double-count
    exactly as N separate sweeps would.
  - Reorg safety: populate caps to_b at head - SETTLE_BLOCKS.

INVOCATION (populate the cache for a roster in one pass, VPS):
  python scripts/chain_fill_cache.py --traders-file /tmp/deepen_wave.txt \
      --cache /opt/pa2-shared/mb_copyable_data/copyable_cache \
      --rpc-url https://polygon.gateway.tenderly.co --rps 8
Then run chain_deep_dive.py with --fill-cache-dir <cache>/chain_fills.
  --self-test  runs the offline demux/round-trip checks.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CACHE_VER = 1
SETTLE_BLOCKS = 30  # only cache ranges this far behind head (reorg margin)


def _dd():
    import chain_deep_dive as dd  # noqa: PLC0415  (late: avoids import cycle)
    return dd


def side_key(f: dict) -> str:
    return f"{f.get('tx', '')}|{f.get('token_id', '')}"


def cache_path(cache_dir: str, addr: str) -> str:
    # distinct suffix: can NEVER collide with the API history cache's
    # <addr>.json even if --fill-cache-dir is mispointed (review F3)
    return os.path.join(cache_dir, f"{addr.lower()}.fills.json")


def load(cache_dir: str, addr: str) -> Optional[dict]:
    p = cache_path(cache_dir, addr)
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p))
    except (ValueError, OSError):
        return None
    # structural validation (review F5): a malformed blob must read as
    # "no cache" -> full-sweep fallback, never a crash mid-dive
    if (d.get("ver") != CACHE_VER
            or not isinstance(d.get("from_b"), int)
            or not isinstance(d.get("to_b"), int)
            or not isinstance(d.get("v1_fills"), list)
            or not isinstance(d.get("v2_fills"), list)):
        return None
    return d


def store(cache_dir: str, addr: str, blob: dict) -> None:
    import find_copyable_traders as fc  # noqa: PLC0415
    os.makedirs(cache_dir, exist_ok=True)
    blob["ver"] = CACHE_VER
    fc.write_json_atomic(cache_path(cache_dir, addr), fc.json_safe(blob))


def serve(cached: Optional[dict], from_b: int, to_b: int) -> Optional[dict]:
    """Sweep-shaped result from cache iff it FULLY covers [from_b, to_b];
    fills filtered to the requested range; V2 sides forced back to
    direction-pending (classify owns side resolution). Else None."""
    if cached is None or cached.get("from_b") is None:
        return None
    if cached["from_b"] > from_b or cached["to_b"] < to_b:
        return None
    inr = lambda f: from_b <= f.get("block", 0) <= to_b  # noqa: E731
    v2 = []
    for f in cached["v2_fills"]:
        if inr(f):
            g = dict(f)
            g["side"] = None
            v2.append(g)
    return {"v1_fills": [dict(f) for f in cached["v1_fills"] if inr(f)],
            "v2_fills": v2,
            "leaf_ok": cached.get("leaf_ok", 0), "leaf_fail": 0,
            "rpc_err_frac": 0.0}


def _fill_key(f: dict) -> str:
    return json.dumps({k: v for k, v in f.items() if k != "side"},
                      sort_keys=True)


def merge_ranges(cached: Optional[dict], new: dict, from_b: int, to_b: int
                 ) -> dict:
    """Extend a cache blob with a CLEAN sweep of an adjacent/overlapping
    range; dedup on the reconstructed record (side excluded).

    RAISES on a non-adjacent range (review F1): merging a disjoint sweep
    would mint a blob whose [from_b, to_b] claims coverage over a HOLE that
    was never swept — served later as clean, zero-error, missing fills →
    false not_found. Callers must catch and skip caching instead."""
    if cached is None:
        return {"from_b": from_b, "to_b": to_b,
                "v1_fills": new["v1_fills"], "v2_fills": new["v2_fills"],
                "leaf_ok": new.get("leaf_ok", 0), "sides": {}}
    if from_b > cached["to_b"] + 1 or to_b < cached["from_b"] - 1:
        raise ValueError(
            f"non-adjacent merge: cache [{cached['from_b']}, {cached['to_b']}]"
            f" vs new [{from_b}, {to_b}] — refusing to mint a coverage hole")
    out = dict(cached)
    for fld in ("v1_fills", "v2_fills"):
        seen = {_fill_key(f) for f in cached[fld]}
        out[fld] = cached[fld] + [f for f in new[fld]
                                  if _fill_key(f) not in seen]
    out["from_b"] = min(cached["from_b"], from_b)
    out["to_b"] = max(cached["to_b"], to_b)
    out["leaf_ok"] = cached.get("leaf_ok", 0) + new.get("leaf_ok", 0)
    return out


def _trim(blob: dict, store_to: int) -> Optional[dict]:
    """Copy of a blob with coverage capped at store_to (reorg margin, review
    F2): fills above store_to are dropped from the PERSISTED copy so the
    unsettled tail is re-swept fresh next run (self-healing, like the
    uncached path). None if nothing settled remains."""
    if store_to < blob["from_b"]:
        return None
    out = dict(blob)
    out["to_b"] = min(blob["to_b"], store_to)
    for fld in ("v1_fills", "v2_fills"):
        out[fld] = [f for f in blob[fld] if f.get("block", 0) <= store_to]
    return out


async def sweep_with_cache(bc, addr: str, from_b: int, to_b: int, cfg,
                           note_err, cache_dir: str) -> dict:
    """Drop-in for chain_deep_dive.sweep_lifetime with a cache in front.
    Full cache hit -> served (it WAS a clean sweep). Partial coverage ->
    sweep ONLY the gap with the EXISTING sweep_lifetime, merge, persist
    (only if clean, only up to to_b - SETTLE_BLOCKS). No/unusable cache ->
    full existing sweep (exact old path), persisted when clean."""
    dd = _dd()
    addr = addr.lower()
    store_to = to_b - SETTLE_BLOCKS
    cached = load(cache_dir, addr)
    hit = serve(cached, from_b, to_b)
    if hit is not None:
        return hit
    if cached is not None and cached["from_b"] <= from_b \
            and cached["to_b"] < to_b:
        gap_lo = cached["to_b"] + 1
        gap = await dd.sweep_lifetime(bc, addr, gap_lo, to_b, cfg, note_err)
        if gap["leaf_fail"] == 0:
            merged = merge_ranges(cached, gap, gap_lo, to_b)
            trimmed = _trim(merged, store_to)
            if trimmed is not None:
                store(cache_dir, addr, trimmed)
            return serve(merged, from_b, to_b)
        # lossy gap sweep: cached clean base + gap results; errors reported
        # over the WHOLE range's leaves (review F4) so the completeness
        # gate sees the same denominator semantics as an uncached sweep
        base = serve(cached, from_b, cached["to_b"])
        tot = base["leaf_ok"] + gap["leaf_ok"] + gap["leaf_fail"]
        return {"v1_fills": base["v1_fills"] + gap["v1_fills"],
                "v2_fills": base["v2_fills"] + gap["v2_fills"],
                "leaf_ok": base["leaf_ok"] + gap["leaf_ok"],
                "leaf_fail": gap["leaf_fail"],
                "rpc_err_frac": gap["leaf_fail"] / tot if tot else 1.0}
    res = await dd.sweep_lifetime(bc, addr, from_b, to_b, cfg, note_err)
    if res["leaf_fail"] == 0:
        blob = merge_ranges(None, res, from_b, to_b)
        blob["sides"] = (cached or {}).get("sides", {})
        trimmed = _trim(blob, store_to)
        if trimmed is not None:
            store(cache_dir, addr, trimmed)
    return res


def known_sides(cache_dir: str, addr: str) -> Optional[dict]:
    """The persisted receipt-confirmed side map ('tx|token_id' -> BUY/SELL)
    for classify_v2_directions(known_sides=...). None when absent/empty."""
    cached = load(cache_dir, addr.lower())
    sides = (cached or {}).get("sides") or {}
    return sides or None


def persist_sides(cache_dir: str, addr: str, v2_fills: list[dict]) -> int:
    """After classify_v2_directions: persist newly receipt-confirmed sides.
    Only BUY/SELL values (never None — a gap is not knowledge)."""
    cached = load(cache_dir, addr.lower())
    if cached is None:
        return 0
    sides = cached.setdefault("sides", {})
    n = 0
    for f in v2_fills:
        s = f.get("side")
        if s in ("BUY", "SELL") and sides.get(side_key(f)) != s:
            sides[side_key(f)] = s
            n += 1
    if n:
        store(cache_dir, addr.lower(), cached)
    return n


async def populate_multi(bc, addrs: list[str], from_b: int, to_b: int, cfg,
                         note_err, cache_dir: str,
                         log: Callable = print) -> dict:
    """ONE sweep of [from_b, to_b] for ALL addrs; demux; write per-addr caches
    (only if the whole sweep is clean — a lossy multi-sweep persists NOTHING
    and every trader falls back to its own sweep). Mirrors sweep_lifetime's
    query structure exactly: 4 V1 streams (2 contracts x maker/taker, address
    LIST filter) + 1 V2 stream (owner-topic LIST)."""
    dd = _dd()
    from base_engine.data.blockchain_client import (  # noqa: PLC0415
        EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT, ORDER_FILLED_EVENT_ABI)
    from web3 import Web3  # noqa: PLC0415
    import audit_roster_chain as ac  # noqa: PLC0415

    addrs = [a.lower() for a in addrs]
    aset = set(addrs)
    cs_list = [Web3.to_checksum_address(a) for a in addrs]
    v1_contracts = [bc.w3.eth.contract(
        address=Web3.to_checksum_address(c), abi=[ORDER_FILLED_EVENT_ABI])
        for c in dict.fromkeys((EXCHANGE_CONTRACT, NEGRISK_EXCHANGE_CONTRACT))]
    v2_addrs = [Web3.to_checksum_address(a)
                for a in (dd.EXCHANGE_V2, dd.NEGRISK_EXCHANGE_V2)]

    counters = {"logs": [], "leaf_ok": 0, "leaf_fail": 0}
    per = {a: {"v1_fills": [], "v2_fills": []} for a in addrs}

    for c in v1_contracts:
        for field in ("maker", "taker"):
            async def _fetch(x, y, _c=c, _f=field):
                evs = await ac.get_logs_compat(_c.events.OrderFilled, x, y,
                                               {_f: cs_list})
                out = []
                for e in evs:
                    d = dict(e["args"])
                    txh = e.get("transactionHash", "")
                    d["_tx"] = txh.hex() if hasattr(txh, "hex") else str(txh)
                    d["_block"] = int(e.get("blockNumber", 0))
                    out.append(d)
                return out
            base = len(counters["logs"])
            await dd._sweep_leaf(_fetch, from_b, to_b, cfg.chunk_blocks,
                                 cfg.min_chunk, cfg.rps, note_err, counters)
            for d in counters["logs"][base:]:
                # demux by the FILTERED field only — the same event in the
                # other role arrives via that role's own stream, exactly as
                # N separate per-address sweeps would deliver it
                owner = str(d.get(field, "")).lower()
                if owner in aset:
                    f = dd.v1_reconstruct_fill(d, owner)
                    if f is not None:
                        per[owner]["v1_fills"].append(f)
            counters["logs"] = counters["logs"][:base]
            log(f"  populate: V1 {field} stream done "
                f"(contract {str(c.address)[:10]}…)")

    async def _fetch_v2(x, y):
        return await bc.w3.eth.get_logs({
            "fromBlock": x, "toBlock": y, "address": v2_addrs,
            "topics": [dd.FILL_TOPIC_V2, None,
                       [dd.addr_topic(a) for a in addrs]]})
    base = len(counters["logs"])
    await dd._sweep_leaf(_fetch_v2, from_b, to_b, cfg.chunk_blocks,
                         cfg.min_chunk, cfg.rps, note_err, counters)
    for lg_ in counters["logs"][base:]:
        lg_ = dict(lg_)
        topics = lg_.get("topics") or []
        owner = dd._topic_addr(topics[2]) if len(topics) > 2 else ""
        if owner in aset:
            f = dd.v2_reconstruct_fill(lg_, owner)
            if f is not None:
                per[owner]["v2_fills"].append(f)
    log("  populate: V2 stream done")

    clean = counters["leaf_fail"] == 0
    skipped_disjoint = []
    if clean:
        for a in addrs:
            prior = load(cache_dir, a)
            try:
                blob = merge_ranges(prior,
                                    {**per[a], "leaf_ok": counters["leaf_ok"]},
                                    from_b, to_b)
            except ValueError:
                # non-adjacent to the prior blob (review F1): caching would
                # mint a coverage hole — skip; this addr keeps its old cache
                # and the dive's gap/fallback logic handles the rest
                skipped_disjoint.append(a)
                continue
            blob["sides"] = (prior or {}).get("sides", {})
            store(cache_dir, a, blob)
    if skipped_disjoint:
        log(f"  populate: SKIPPED {len(skipped_disjoint)} addrs — swept range "
            f"not adjacent to their existing cache (no hole minted): "
            f"{[a[:10] for a in skipped_disjoint]}")
    tot = counters["leaf_ok"] + counters["leaf_fail"]
    return {"clean": clean,
            "cached": (len(addrs) - len(skipped_disjoint)) if clean else 0,
            "leaf_ok": counters["leaf_ok"], "leaf_fail": counters["leaf_fail"],
            "rpc_err_frac": counters["leaf_fail"] / tot if tot else 1.0,
            "fills": {a: len(per[a]["v1_fills"]) + len(per[a]["v2_fills"])
                      for a in addrs}}


# ------------------------------- self-test --------------------------------

def self_test() -> int:
    import tempfile
    dd = _dd()
    with tempfile.TemporaryDirectory() as td:
        blob = {"from_b": 100, "to_b": 200, "leaf_ok": 4, "sides": {},
                "v1_fills": [{"tx": "0xa", "block": 150, "side": "BUY",
                              "token_id": "1", "usd": 5.0, "tokens": 10.0,
                              "era": "v1"}],
                "v2_fills": [{"tx": "0xb", "block": 190, "side": None,
                              "token_id": "2", "usd": 3.0, "tokens": 6.0,
                              "era": "v2"}]}
        store(td, "0xAB", blob)
        c = load(td, "0xab")
        assert c and c["ver"] == CACHE_VER
        assert serve(c, 90, 200) is None           # not fully covered
        assert serve(c, 100, 201) is None
        full = serve(c, 100, 200)
        assert len(full["v1_fills"]) == 1 and full["rpc_err_frac"] == 0.0
        narrow = serve(c, 160, 200)
        assert len(narrow["v1_fills"]) == 0 and len(narrow["v2_fills"]) == 1
        # V2 sides are ALWAYS served direction-pending
        c["v2_fills"][0]["side"] = "BUY"
        assert serve(c, 100, 200)["v2_fills"][0]["side"] is None

        # side persistence: composite (tx|token) keys, BUY/SELL only
        n = persist_sides(td, "0xab",
                          [{"tx": "0xb", "token_id": "2", "side": "BUY"},
                           {"tx": "0xb", "token_id": "9", "side": "SELL"},
                           {"tx": "0xc", "token_id": "2", "side": None}])
        assert n == 2
        ks = known_sides(td, "0xab")
        assert ks == {"0xb|2": "BUY", "0xb|9": "SELL"}

        # merge: dedup on identical record (side excluded), range extension
        newer = {"v1_fills": [dict(blob["v1_fills"][0]),
                              {"tx": "0xd", "block": 250, "side": "SELL",
                               "token_id": "3", "usd": 1.0, "tokens": 2.0,
                               "era": "v1"}],
                 "v2_fills": [], "leaf_ok": 2}
        c3 = load(td, "0xab")
        m = merge_ranges(c3, newer, 201, 300)
        assert m["from_b"] == 100 and m["to_b"] == 300
        assert len(m["v1_fills"]) == 2  # 0xa dedup'd, 0xd added
        assert m["sides"] == ks         # sides survive a merge

        # review F1: a NON-ADJACENT merge must refuse (never mint a hole)
        try:
            merge_ranges(c3, newer, 500, 600)
            raise AssertionError("disjoint merge did not raise")
        except ValueError:
            pass

        # review F2: _trim caps persisted coverage + drops unsettled fills
        t = _trim({"from_b": 100, "to_b": 300, "sides": {},
                   "v1_fills": [{"block": 150}], "v2_fills": [{"block": 290}],
                   "leaf_ok": 1}, 250)
        assert t["to_b"] == 250 and len(t["v2_fills"]) == 0 \
            and len(t["v1_fills"]) == 1
        assert _trim({"from_b": 100, "to_b": 300, "v1_fills": [],
                      "v2_fills": [], "sides": {}}, 50) is None

        # review F5: malformed blob loads as None (fallback), never crashes
        store(td, "0xbad", {"from_b": None, "to_b": 5,
                            "v1_fills": [], "v2_fills": []})
        assert load(td, "0xbad") is None

        # V1 demux parity: one event with maker AND taker in roster must
        # produce BOTH per-addr fills exactly like two per-addr sweeps
        ev = {"maker": "0xM", "taker": "0xT", "makerAssetId": 0,
              "takerAssetId": 7, "makerAmountFilled": 4_000_000,
              "takerAmountFilled": 8_000_000, "_tx": "0xe", "_block": 5}
        fm = dd.v1_reconstruct_fill(dict(ev), "0xm")
        ft = dd.v1_reconstruct_fill(dict(ev), "0xt")
        assert fm is not None and ft is not None and fm["side"] != ft["side"]
        assert fm["tx"] == "0xe" and fm["block"] == 5
    print("self-test PASS")
    return 0


async def run_populate(args) -> int:
    from dotenv import load_dotenv  # noqa: PLC0415
    load_dotenv()
    if os.path.exists("/opt/pa2-shared/.env"):
        load_dotenv("/opt/pa2-shared/.env")
    from base_engine.data.blockchain_client import BlockchainClient  # noqa: PLC0415
    import audit_roster_chain as ac  # noqa: PLC0415
    dd = _dd()

    rpc_url, err = ac.resolve_rpc_url(args.rpc_url, "", os.environ)
    if err:
        print(err, file=sys.stderr)
        return 2
    with open(args.traders_file) as f:
        addrs = [a.strip().lower() for a in f if a.strip().startswith("0x")]
    if not addrs:
        print("no addresses", file=sys.stderr)
        return 2
    bc = BlockchainClient(rpc_url=rpc_url)
    await bc.ensure_client()
    head = await dd.rpc_call(bc.w3.eth.get_block_number())
    to_b = args.to_block or (head - SETTLE_BLOCKS)
    cfg = argparse.Namespace(chunk_blocks=args.chunk_blocks,
                             min_chunk=args.min_chunk, rps=args.rps)
    errs: list = []
    cache_dir = os.path.join(args.cache, "chain_fills")
    print(f"populate_multi: {len(addrs)} addrs, blocks "
          f"[{args.from_block}, {to_b}], rps={args.rps}")
    res = await populate_multi(bc, addrs, args.from_block, to_b, cfg,
                               lambda e: errs.append(e), cache_dir)
    print(json.dumps({k: v for k, v in res.items() if k != "fills"}))
    for a, nf in res["fills"].items():
        print(f"  {a[:12]}… fills={nf}")
    if not res["clean"]:
        print("SWEEP NOT CLEAN — nothing cached; traders will fall back to "
              "their own sweeps", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--traders-file", default="")
    ap.add_argument("--cache", default="/opt/pa2-shared/mb_copyable_data/"
                                       "copyable_cache")
    ap.add_argument("--rpc-url", default="")
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--from-block", type=int, default=37_000_000,
                    help="2023-01-01 floor (matches chain_deep_dive)")
    ap.add_argument("--to-block", type=int, default=0)
    ap.add_argument("--chunk-blocks", type=int, default=25_000,
                    help="smaller default than single-addr (N-addr responses)")
    ap.add_argument("--min-chunk", type=int, default=900)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return asyncio.run(run_populate(args))


if __name__ == "__main__":
    raise SystemExit(main())
