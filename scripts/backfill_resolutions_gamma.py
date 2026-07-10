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
umaEndDate, else endDate — first parseable (gamma rows); end_date_iso (CLOB
rows). Missing => the graders fall back to entry time and report the count.

SOURCES (2026-07-10 rework): 0x condition_ids are fetched ONE PER REQUEST
from CLOB /markets/{cid} — gamma's ?condition_ids= batch filter is silently
ignored (two full runs: 200s, zero matches; production resolution_backfill.py
uses the same CLOB endpoint for the same reason). Numeric ids use gamma
/markets/{id}.

SAFETY: READ-ONLY everywhere (DB SELECTs to skip already-labeled keys; API
GETs, throttled). The JSON cache is append/merge, resume-safe — re-running
skips keys already backfilled.

INVOCATION (on the VPS; per-key fetches — ~2.5h per 130k holes at --rps 15):
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
CLOB = "https://clob.polymarket.com"


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


def map_clob_market(m: dict) -> Optional[dict]:
    """CLOB /markets/{condition_id} row → the same blob shape as
    map_gamma_market. Ported from base_engine/data/resolution_backfill.py
    (production, 2026-05-26 chain-verification): resolution from token PRICES
    first ([~1,~0] payout), winner flag only as fallback — the winner flag
    alone has been observed missing/wrong. resolution='YES' means tokens[0]
    won (positional convention, paired with the stored token ids — the
    graders' labeler matches by token id, so wording-independent).
    resolved_at ≈ end_date_iso (settlement lags it by hours — immaterial on a
    30-day review grid; missing => graders' counted entry-time fallback).
    Open / non-binary / ambiguous-closed → None, never guessed."""
    if not isinstance(m, dict) or not m.get("closed"):
        return None
    tokens = m.get("tokens") or []
    if len(tokens) != 2:
        return None
    t0 = str(tokens[0].get("token_id") or "").strip()
    t1 = str(tokens[1].get("token_id") or "").strip()
    if not t0 or not t1:
        return None
    res = None
    try:
        p0 = float(tokens[0].get("price") or 0)
        p1 = float(tokens[1].get("price") or 0)
        if p0 >= 0.99 and p1 <= 0.01:
            res = "YES"
        elif p0 <= 0.01 and p1 >= 0.99:
            res = "NO"
    except (ValueError, TypeError):
        pass
    if res is None:  # legacy fallback: prices not populated on the row yet
        if tokens[0].get("winner"):
            res = "YES"
        elif tokens[1].get("winner"):
            res = "NO"
    if res is None:
        return None
    ra = fc.parse_ts(m.get("end_date_iso"))
    return {"resolution": res,
            "resolved_at": ra.isoformat() if ra else None,
            "yes_token_id": t0, "no_token_id": t1,
            "category": fc.bucket_category(m.get("question") or "")}


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
    todo = sorted(k for k in keys - labeled_db - set(existing)
                  if k.startswith("0x") or k.isdigit())  # slug keys: no per-id
    #  endpoint takes them (same exclusion as the old batch code); they stay
    #  in the report's unlabelable residue — add a slug-lookup pass if big.
    print(f"[2/4] DB-labeled={len(labeled_db):,}  already-backfilled={len(existing):,}"
          f"  to-fetch={len(todo):,}", file=sys.stderr)

    # 3. per-key fetches — CLOB /markets/{condition_id} for 0x keys, gamma
    # /markets/{id} for numeric ids. Gamma's /markets?condition_ids= filter
    # is silently IGNORED (2026-07-10: two full runs returned HTTP 200 with
    # the default listing — 0 labels, 0 errors, 170k keys; production
    # base_engine/data/resolution_backfill.py learned the same lesson and
    # uses the per-id CLOB endpoint). Never batch-query gamma by
    # condition_ids again.
    added = skipped = errors = 0
    chunk_n = max(1, args.batch)
    async with httpx.AsyncClient(timeout=25) as hc:
        async def fetch_one(key: str) -> None:
            nonlocal added, skipped, errors
            try:
                url = (f"{CLOB}/markets/{key}" if key.startswith("0x")
                       else f"{GAMMA}/markets/{key}")
                r = await hc.get(url)
                if r.status_code == 200:
                    row = r.json()
                    mapped = (map_clob_market(row) if key.startswith("0x")
                              else map_gamma_market(row))
                elif r.status_code == 404:
                    mapped = None  # market unknown to the API — skip, no error
                else:
                    errors += 1
                    return
            except Exception:
                errors += 1
                return
            if mapped:
                existing[key] = mapped
                added += 1
            else:
                skipped += 1

        chunks = [todo[i:i + chunk_n] for i in range(0, len(todo), chunk_n)]
        for ci, chunk in enumerate(chunks):
            await asyncio.gather(*(fetch_one(k) for k in chunk))
            if ci % 40 == 0:
                print(f"  …chunk {ci}/{len(chunks)}  labeled+{added:,}"
                      f"  skipped={skipped:,}  errors={errors}", file=sys.stderr)
                fc.write_json_atomic(out_path, existing)
            await asyncio.sleep(chunk_n / max(args.rps, 0.1))
    fc.write_json_atomic(out_path, existing)

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

    # CLOB mapper (per-condition-id endpoint — the 2026-07-10 rework)
    cm = {"closed": True, "question": "Will the Lakers win the NBA finals?",
          "end_date_iso": "2026-06-20T00:00:00Z",
          "tokens": [{"token_id": "C1", "outcome": "Lakers", "price": "1"},
                     {"token_id": "C2", "outcome": "Celtics", "price": "0"}]}
    r5 = map_clob_market(cm)
    ok5 = (r5 and r5["resolution"] == "YES" and r5["yes_token_id"] == "C1"
           and r5["resolved_at"] == "2026-06-20T00:00:00"
           and r5["category"] == "sports")
    print(f"  [clob] prices [1,0] -> YES/token[0]/end_date/category : {bool(ok5)}"); ok &= bool(ok5)

    cm2 = {**cm, "tokens": [{"token_id": "C1", "outcome": "A", "price": None},
                            {"token_id": "C2", "outcome": "B", "price": None,
                             "winner": True}]}
    r6 = map_clob_market(cm2)
    ok6 = r6 and r6["resolution"] == "NO"  # winner-flag fallback, token[1] won
    print(f"  [clob] no prices, winner flag on token[1] -> NO : {bool(ok6)}"); ok &= bool(ok6)

    ok7 = (map_clob_market({**cm, "closed": False}) is None
           and map_clob_market({**cm, "tokens": [
               {"token_id": "C1", "outcome": "A", "price": "0.6"},
               {"token_id": "C2", "outcome": "B", "price": "0.4"}]}) is None
           and map_clob_market({**cm, "tokens": cm["tokens"][:1]}) is None)
    print(f"  [clob] open/ambiguous/non-binary -> skipped, never guessed : {ok7}"); ok &= ok7

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
    ap.add_argument("--batch", type=int, default=25,
                    help="concurrent per-key requests per wave")
    ap.add_argument("--rps", type=float, default=15.0)
    ap.add_argument("--timeout", type=int, default=60, help="DB statement_timeout s")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
