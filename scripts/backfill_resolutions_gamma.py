#!/usr/bin/env python3
"""Backfill market resolutions from the gamma API into a local JSON cache.

WHY (2026-07-10 tinker, before the next long run): the copyable/walk-forward
graders can only label bets whose market is in OUR DB — measured coverage was
24%, i.e. every trader was being judged on a quarter of their record. The
gamma API has outcomes for effectively every market that ever existed. This
script finds the unlabeled market keys in the history cache, pulls their
resolutions, and writes them to <cache>/gamma_resolutions.json — which
find_copyable_traders.py and walkforward_copy_traders.py merge UNDER the DB
map (DB wins on conflict; this only fills holes).

LABEL SEMANTICS (general-binary, not just Yes/No wording): a market is labeled
only when it is closed AND definitive — one outcome priced >= 0.99 and the
other <= 0.01. We store resolution='YES' meaning "token[0] won" together with
yes_token_id=clobTokenIds[0] / no_token_id=clobTokenIds[1], so the graders'
token-matching labeler works for team-name binaries too. Ambiguous, split, or
open markets are skipped and counted, never guessed.

resolved_at (knowledge time for the walk-forward roster): closedTime, else
umaEndDate, else endDate — first parseable. Missing => the graders fall back
to entry time and report the count.

SAFETY: READ-ONLY everywhere (DB SELECTs to skip already-labeled keys; gamma
GETs, throttled). The JSON cache is append/merge, resume-safe — re-running
skips keys already backfilled.

INVOCATION (on the VPS, once; ~15-40 min depending on hole count):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc \
      venv/bin/python /tmp/mbpc/scripts/backfill_resolutions_gamma.py \
      --cache /tmp/copyable_cache | tee /tmp/gamma_backfill.log
    ... --self-test        # offline mapping check, no DB/network
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_copyable_traders as fc  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com"


# ── Pure mapping (offline-testable) ──────────────────────────────────────────
def _jlist(v: Any) -> list:
    """gamma encodes list fields as JSON strings about half the time."""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except (ValueError, TypeError):
            return []
    return []


def map_gamma_market(m: dict) -> Optional[dict]:
    """gamma market row → {resolution, resolved_at, yes_token_id,
    no_token_id, category} or None (open / non-binary / not definitive).
    resolution='YES' means token[0] won — paired with the stored token ids so
    the graders' token-matching labeler is wording-independent."""
    if not isinstance(m, dict) or not m.get("closed"):
        return None
    prices = [float(x) for x in _jlist(m.get("outcomePrices")) if str(x).strip() != ""]
    tokens = [str(x) for x in _jlist(m.get("clobTokenIds"))]
    if len(prices) != 2 or len(tokens) != 2:
        return None
    hi, lo = max(prices), min(prices)
    if not (hi >= 0.99 and lo <= 0.01):
        return None  # split / unresolved payout — never guess
    winner = prices.index(hi)
    resolved_at = None
    for k in ("closedTime", "umaEndDate", "endDate"):
        resolved_at = fc.parse_ts(m.get(k))
        if resolved_at is not None:
            break
    return {"resolution": "YES" if winner == 0 else "NO",
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
            "yes_token_id": tokens[0], "no_token_id": tokens[1],
            "category": m.get("category") or ""}


def keys_of(m: dict) -> list[str]:
    """Every key this market can be looked up by in our histories."""
    out = []
    cid = (m.get("conditionId") or "").strip()
    if cid:
        out.append(cid)
    mid = m.get("id")
    if mid is not None:
        out.append(str(mid))
    return out


# ── Network run ──────────────────────────────────────────────────────────────
async def run(args) -> int:
    import httpx
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database

    # 1. all market keys referenced by the cached histories
    keys: set[str] = set()
    for f in os.listdir(args.cache):
        if not (f.startswith("0x") and f.endswith(".json")):
            continue
        with open(os.path.join(args.cache, f)) as fh:
            blob = json.load(fh)
        trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
        for t in trades:
            k = (t.get("marketId") or "").strip()
            if k:
                keys.add(k)
    print(f"[1/4] {len(keys):,} distinct market keys in cached histories", file=sys.stderr)

    # 2. subtract keys the DB already labels
    db = Database()
    await db.init()
    try:
        db_map = await fc.load_markets(db, sorted(keys), args.timeout)
    finally:
        await db.close()
    labeled_db = {k for k in keys if (db_map.get(k) or {}).get("resolution")}
    out_path = os.path.join(args.cache, "gamma_resolutions.json")
    existing: dict = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    todo = sorted(keys - labeled_db - set(existing))
    print(f"[2/4] DB-labeled={len(labeled_db):,}  already-backfilled={len(existing):,}"
          f"  to-fetch={len(todo):,}", file=sys.stderr)

    # 3. batched gamma fetches (condition_ids for 0x keys, id for numeric)
    cids = [k for k in todo if k.startswith("0x")]
    nids = [k for k in todo if k.isdigit()]
    added = skipped = errors = 0
    async with httpx.AsyncClient(timeout=25) as hc:
        async def fetch(params: list[tuple]) -> list:
            nonlocal errors
            try:
                r = await hc.get(f"{GAMMA}/markets", params=params + [("limit", "100")])
                if r.status_code != 200:
                    errors += 1
                    return []
                out = r.json()
                return out if isinstance(out, list) else []
            except Exception:
                errors += 1
                return []

        batches = [("condition_ids", cids[i:i + args.batch]) for i in range(0, len(cids), args.batch)]
        batches += [("id", nids[i:i + args.batch]) for i in range(0, len(nids), args.batch)]
        for bi, (pname, batch) in enumerate(batches):
            rows = await fetch([(pname, k) for k in batch])
            got = set()
            for m in rows:
                mapped = map_gamma_market(m)
                for k in keys_of(m):
                    got.add(k)
                    if mapped and k in keys and k not in existing:
                        existing[k] = mapped
                        added += 1
            skipped += sum(1 for k in batch if k not in got or k not in existing)
            if bi % 40 == 0:
                print(f"  …batch {bi}/{len(batches)}  labeled+{added:,}", file=sys.stderr)
                with open(out_path, "w") as f:
                    json.dump(existing, f)
            await asyncio.sleep(1.0 / args.rps)
    with open(out_path, "w") as f:
        json.dump(existing, f)

    # 4. report
    total_after = len(labeled_db) + len(existing)
    print("\n" + "=" * 78)
    print("  GAMMA RESOLUTION BACKFILL")
    print(f"  market keys in histories : {len(keys):,}")
    print(f"  labeled by DB            : {len(labeled_db):,}")
    print(f"  backfilled (cache total) : {len(existing):,}  (+{added:,} this run)")
    print(f"  unlabelable/open/split   : {max(0, len(keys) - total_after):,}"
          f"   fetch errors: {errors}")
    print(f"  projected label coverage : {min(1.0, total_after / max(1, len(keys))):.0%}"
          f"  (was ~24% DB-only)")
    print(f"  cache file: {out_path}")
    print("  Both graders pick this up automatically via --gamma-cache (default path).")
    print("=" * 78 + "\n")
    return 0


# ── Self-test (no DB, no network) ────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — gamma mapping (no DB, no network)\n")
    ok = True
    m = {"closed": True, "conditionId": "0xabc", "id": 123,
         "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]',
         "clobTokenIds": '["T1","T2"]', "closedTime": "2026-01-05T00:00:00Z",
         "category": "Sports"}
    r = map_gamma_market(m)
    ok1 = (r and r["resolution"] == "YES" and r["yes_token_id"] == "T1"
           and r["resolved_at"] == "2026-01-05T00:00:00" and r["category"] == "Sports")
    print(f"  [map] Yes-won market -> YES/token[0]/resolved_at : {bool(ok1)}"); ok &= bool(ok1)

    m2 = {**m, "outcomePrices": '["0","1"]', "outcomes": '["Lakers","Celtics"]'}
    r2 = map_gamma_market(m2)
    ok2 = r2 and r2["resolution"] == "NO"  # token[1] won; wording-independent
    print(f"  [map] team-name binary, second token won -> NO : {bool(ok2)}"); ok &= bool(ok2)

    ok3 = (map_gamma_market({**m, "closed": False}) is None
           and map_gamma_market({**m, "outcomePrices": '["0.6","0.4"]'}) is None
           and map_gamma_market({**m, "clobTokenIds": '["T1"]'}) is None)
    print(f"  [map] open/split/non-binary -> skipped, never guessed : {ok3}"); ok &= ok3

    ok4 = keys_of(m) == ["0xabc", "123"] and _jlist('["a","b"]') == ["a", "b"] \
        and _jlist(["a"]) == ["a"] and _jlist("junk") == []
    print(f"  [keys/jlist] both lookup keys; string-encoded lists : {ok4}"); ok &= ok4

    # merge hook: gamma fills holes, DB wins on conflict
    markets = {"0xdb": {"resolution": "NO"}}
    gamma_blob = {"0xdb": {"resolution": "YES"},
                  "0xnew": {"resolution": "YES", "yes_token_id": "T1",
                            "no_token_id": "T2", "category": "Sports",
                            "resolved_at": "2026-01-05T00:00:00"}}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(gamma_blob, f)
        path = f.name
    n = fc.merge_gamma_cache(markets, ["0xdb", "0xnew", "0xmissing"], path)
    os.unlink(path)
    ok5 = n == 1 and markets["0xdb"]["resolution"] == "NO" \
        and markets["0xnew"]["resolution"] == "YES"
    print(f"  [merge] fills holes only, DB wins : {ok5}"); ok &= ok5

    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill resolutions from gamma into a local cache")
    ap.add_argument("--cache", default="/tmp/copyable_cache")
    ap.add_argument("--batch", type=int, default=25, help="market keys per gamma request")
    ap.add_argument("--rps", type=float, default=5.0)
    ap.add_argument("--timeout", type=int, default=60, help="DB statement_timeout s")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
