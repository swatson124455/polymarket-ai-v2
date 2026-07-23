#!/usr/bin/env python3
"""Shadow-targeted CLOB resolution supplement.

WHY (2026-07-22 label-integrity finding, 2026-07-23 follow-on): the shared
resolution backfill only ever queues markets THE BOT TRADED, so `markets.
resolved` is structurally incomplete for shadow-copied markets. The CLOB
supplement built on 2026-07-22 fixed the ADMIT deep-dive's evidence, but it was
keyed on the traders' OWN markets — measured 2026-07-23 it covers 21 of 405
shadow tokens. 300 shadow tokens are unlabelled by DB+cache, and the missing
slice is systematically NEGATIVE, which is exactly what flattered every edge
this lane has reported.

This job labels the SHADOW token set: for every shadow token still unlabelled
after DB+cache, look up its condition_id (from `markets` — present even when
unresolved) and refresh the resolution straight from CLOB, the trustworthy
source (resolution derived from token prices reflecting UMA settlement;
verified 196/196, 0 mismatches, on 2026-07-22).

It reuses `resolution_backfill._fetch_market_by_condition_id` +
`_clob_to_market_format` VERBATIM — the production-proven path — so the
resolution derivation can never drift from the system's own.

Output: merged into the same `gamma_resolutions.json` the readout reads, so
`shadow_readout.py` picks the new labels up with NO code change.

SAFETY
  * dry-run by default; `--write` required to touch the cache
  * timestamped backup before any write; atomic replace
  * NEVER overwrites an existing YES/NO entry — a disagreement is reported as a
    CONFLICT, loudly, and left for a human
  * every stage asserts its input is NON-EMPTY (empty-set false-pass class,
    2026-07-22): 0 shadow tokens, or 0 fetched when targets > 0, is a FAILURE,
    never a quiet success

    DATABASE_URL=... PYTHONPATH=<repo> venv/bin/python scripts/shadow_label_supplement.py [--write]
    ... --self-test    # offline: merge/conflict/guard logic, no DB, no network
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone

CACHE = "/opt/pa2-shared/mb_copyable_data/copyable_cache/gamma_resolutions.json"
SHADOW = "/opt/pa2-shared/mirror3_shadow.jsonl"


class LabelJobError(RuntimeError):
    """A guard tripped. Fatal by design — see the empty-set note above."""


def shadow_tokens(path: str) -> list[str]:
    toks: set[str] = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("token_id"):
                toks.add(str(r["token_id"]))
    if not toks:
        raise LabelJobError(f"0 shadow tokens read from {path} — refusing to "
                            f"report 'nothing to do' on an empty input")
    return sorted(toks)


def cache_labels(cache: dict) -> dict[str, str]:
    """token_id -> 'YES'/'NO' for definitively-resolved markets in the cache."""
    out: dict[str, str] = {}
    for m in cache.values():
        if not isinstance(m, dict) or m.get("resolution") not in ("YES", "NO"):
            continue
        out[str(m.get("yes_token_id"))] = m["resolution"]
        out[str(m.get("no_token_id"))] = m["resolution"]
    return out


def merge_entry(cache: dict, cid: str, entry: dict) -> str:
    """Merge ONE CLOB-derived entry. Returns 'new' | 'kept' | 'CONFLICT'.

    An existing YES/NO entry is authoritative and never overwritten: the two
    sources disagreeing is a finding for a human, not something to silently
    resolve in favour of whichever ran last."""
    old = cache.get(cid)
    if isinstance(old, dict) and old.get("resolution") in ("YES", "NO"):
        if old["resolution"] != entry["resolution"]:
            return "CONFLICT"
        return "kept"
    cache[cid] = entry
    return "new"


def entry_from_clob(clob: dict, cid: str) -> dict | None:
    """CLOB payload -> cache entry, or None if not definitively resolved.
    Derivation is the production path's, never re-implemented here."""
    from base_engine.data.resolution_backfill import _clob_to_market_format
    m = _clob_to_market_format(clob, cid)
    if not (m.get("resolved") and m.get("resolution") in ("YES", "NO")):
        return None
    if not (m.get("yes_token_id") and m.get("no_token_id")):
        return None
    return {"resolution": m["resolution"],
            "resolved_at": (m.get("end_date_iso") or
                            datetime.now(timezone.utc).isoformat()),
            "yes_token_id": str(m["yes_token_id"]),
            "no_token_id": str(m["no_token_id"]),
            "category": m.get("category") or "other",
            "source": "clob_shadow_supplement"}


async def _targets(tokens: list[str], labelled: set[str]) -> dict[str, str]:
    """condition_id -> a representative unlabelled shadow token."""
    from base_engine.data.database import Database
    from sqlalchemy import text
    unl = [t for t in tokens if t not in labelled]
    if not unl:
        return {}
    db = Database()
    await db.init()
    try:
        async with db.get_session() as s:
            await s.execute(text("SET LOCAL statement_timeout='120s'"))
            rows = (await s.execute(text(
                "SELECT condition_id, yes_token_id, no_token_id FROM markets "
                "WHERE yes_token_id = ANY(:t) OR no_token_id = ANY(:t)"),
                {"t": unl})).fetchall()
    finally:
        await db.close()
    out: dict[str, str] = {}
    want = set(unl)
    for r in rows:
        m = r._mapping
        for k in ("yes_token_id", "no_token_id"):
            if str(m[k]) in want:
                out[str(m["condition_id"])] = str(m[k])
    return out


async def _fetch_all(cids: list[str], concurrency: int, timeout_s: float):
    from base_engine.data.resolution_backfill import _fetch_market_by_condition_id
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, dict | None] = {}
    # a bare except that swallows the reason is how a library-signature bug got
    # laundered into "rpc_error" for 580/580 samples (2026-07-10 landmine) —
    # keep the failure REASONS and print them
    errs: dict[str, int] = {}

    async def one(cid: str):
        async with sem:
            try:
                # the helper carries its own 15s httpx timeout; this is the
                # belt-and-braces await bound (NO unbounded network await)
                out[cid] = await asyncio.wait_for(
                    _fetch_market_by_condition_id(cid), timeout=timeout_s)
            except asyncio.TimeoutError:
                out[cid] = None
                errs["timeout"] = errs.get("timeout", 0) + 1
            except Exception as e:
                out[cid] = None
                k = type(e).__name__
                errs[k] = errs.get(k, 0) + 1
    await asyncio.gather(*(one(c) for c in cids))
    if errs:
        print("fetch failures by reason:",
              ", ".join(f"{k}={v}" for k, v in sorted(errs.items())))
    return out


async def run(args) -> int:
    with open(args.cache) as f:
        cache = json.load(f)
    if not isinstance(cache, dict) or not cache:
        raise LabelJobError(f"cache empty/not an object: {args.cache}")
    tokens = shadow_tokens(args.shadow)
    labelled = set(cache_labels(cache))
    targets = await _targets(tokens, labelled)
    n_unl = len([t for t in tokens if t not in labelled])
    print(f"shadow tokens={len(tokens)}  unlabelled-by-cache={n_unl}  "
          f"condition_ids resolvable={len(targets)}")
    if not targets:
        print("0 targets — every unlabelled shadow token lacks a condition_id "
              "in `markets`. NOT a success: nothing was verified.")
        return 3
    cids = sorted(targets)[:args.limit] if args.limit else sorted(targets)
    if args.limit and len(targets) > args.limit:
        print(f"NOTE: capped at --limit {args.limit} of {len(targets)} "
              f"targets — {len(targets) - args.limit} NOT attempted")
    fetched = await _fetch_all(cids, args.concurrency, args.timeout)
    ok = {c: v for c, v in fetched.items() if v}
    if not ok:
        raise LabelJobError(f"0 of {len(cids)} CLOB fetches returned a payload "
                            f"— a zero-row fetch is a failure, not 'no news'")
    new = kept = conflict = still_open = 0
    conflicts: list[str] = []
    for cid, clob in ok.items():
        e = entry_from_clob(clob, cid)
        if e is None:
            still_open += 1
            continue
        r = merge_entry(cache, cid, e)
        if r == "new":
            new += 1
        elif r == "kept":
            kept += 1
        else:
            conflict += 1
            conflicts.append(cid)
    print(f"fetched ok={len(ok)}/{len(cids)}  unreachable={len(cids) - len(ok)}  "
          f"newly-labelled={new}  already-known={kept}  still-open={still_open}  "
          f"CONFLICT={conflict}")
    for c in conflicts:
        print(f"  CONFLICT (cache kept, NOT overwritten): {c}")
    if not args.write:
        print("\nDRY RUN — cache NOT modified. Re-run with --write to apply.")
        return 0
    if new == 0:
        print("\nnothing new to write — cache untouched")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    bak = f"{args.cache}.pre-shadow-supplement-{stamp}"
    if not os.path.exists(bak):
        shutil.copy2(args.cache, bak)
    tmp = args.cache + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, args.cache)
    with open(args.cache) as f:
        back = json.load(f)
    if len(back) < len(cache):
        raise LabelJobError("post-write readback SHRANK the cache — restore "
                            f"from {bak} immediately")
    print(f"\nWROTE {args.cache}: {len(back)} keys (+{new}); backup {bak}")
    return 0


def _self_test() -> int:
    print("SELF-TEST — shadow_label_supplement (offline)\n")
    ok = True
    _e = lambda r: {"resolution": r, "yes_token_id": "y", "no_token_id": "n",
                    "resolved_at": "x", "category": "other"}
    c: dict = {}
    ok1 = merge_entry(c, "c1", _e("YES")) == "new" and c["c1"]["resolution"] == "YES"
    print(f"  [merge] new entry added : {ok1}"); ok &= ok1
    ok2 = merge_entry(c, "c1", _e("YES")) == "kept"
    print(f"  [merge] agreeing entry -> kept, idempotent : {ok2}"); ok &= ok2
    ok3 = (merge_entry(c, "c1", _e("NO")) == "CONFLICT"
           and c["c1"]["resolution"] == "YES")
    print(f"  [merge] disagreement -> CONFLICT, cache authoritative : {ok3}")
    ok &= ok3
    lab = cache_labels({"c1": _e("YES"), "c2": {"resolution": None}})
    ok4 = lab == {"y": "YES", "n": "YES"}
    print(f"  [labels] both legs from resolved only : {ok4}"); ok &= ok4
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.jsonl")
        open(p, "w").write('{"token_id": "t1"}\nnot json\n{"x": 1}\n')
        ok5 = shadow_tokens(p) == ["t1"]
        print(f"  [read] malformed lines skipped, tokens deduped : {ok5}")
        ok &= ok5
        open(p, "w").write("\n\n")
        try:
            shadow_tokens(p)
            ok6 = False
        except LabelJobError:
            ok6 = True
        print(f"  [guard] empty shadow log raises, never 'nothing to do' : {ok6}")
        ok &= ok6
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Label the shadow token set from "
                                             "CLOB into the readout's cache")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--shadow", default=SHADOW)
    ap.add_argument("--write", action="store_true",
                    help="apply the merge (default: dry run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap targets attempted (0 = all); a cap is DISCLOSED "
                         "in the output, never silent")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=25.0,
                    help="hard per-request await bound (seconds)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    try:
        raise SystemExit(asyncio.run(run(args)))
    except LabelJobError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        raise SystemExit(2)
