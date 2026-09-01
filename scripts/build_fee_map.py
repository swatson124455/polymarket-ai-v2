#!/usr/bin/env python3
"""Build the per-market fee map for the shadow readout (token -> taker bps).

WHY (2026-07-30, operator-approved "ding market by market"): fees on Polymarket
are PER-MARKET. Measured on our shadow universe: 145/148 resolvable markets
carry taker_base_fee=1000, 3 carry 0 (CLOB per-market reads); on fee-bearing
markets the realized fee is material (~2.8% of notional p50, n=1,826 live RTDS
trades). The readout's flat 2% haircut therefore over-charges the measured
zero-fee markets and is roughly right elsewhere. This job maps every shadow
token to its market's `taker_base_fee` so `analyze_shadow.analyze(fee_map=...)`
can EXEMPT measured-zero-fee tokens; every fee-bearing or unmapped token keeps
the flat conservative charge (a calibrated per-market RATE is a separate,
gated step).

Token -> condition_id via the gamma/CLOB resolution cache AND the `markets`
table (union — the cache alone covered only 148 cids on 2026-07-30);
condition_id -> taker_base_fee via CLOB /markets/{cid}. Both legs of each
market get the market's fee. Atomic write, timestamped backup, and every stage
asserts NON-EMPTY input (empty-set false-pass class): 0 tokens or 0 fetched is
a FAILURE, never "nothing to do".

    DATABASE_URL=... PYTHONPATH=<repo> venv/bin/python scripts/build_fee_map.py [--write]
    ... --self-test   # offline
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone

SHADOW = "/opt/pa2-shared/mirror3_shadow.jsonl"
CACHE = "/opt/pa2-shared/mb_copyable_data/copyable_cache/gamma_resolutions.json"
OUT = "/opt/pa2-shared/mb_copyable_data/copyable_cache/fee_map.json"
H = {"Content-Type": "application/json", "User-Agent": "curl/8"}


# Official per-category taker rates (docs.polymarket.com "Trading Fees",
# fetched 2026-08-19; formula fee = C * rate * p * (1-p); VALIDATED against
# 3,070 live charged fees: crypto implied p50 0.0700, sports 0.0500).
# Unknown/missing category -> 0.07 = the HIGHEST published rate, conservative
# for any pass verdict (disclosed in output).
CATEGORY_RATES = {
    "crypto": 0.07,
    "sports": 0.05, "esports": 0.05, "economics": 0.05, "culture": 0.05,
    "weather": 0.05, "other": 0.05,
    "finance": 0.04, "politics": 0.04, "mentions": 0.04, "tech": 0.04,
    "geopolitics": 0.0,
}
UNKNOWN_RATE = 0.07


class FeeMapError(RuntimeError):
    """A guard tripped — fatal by design (see module docstring)."""


def shadow_tokens(paths: list[str]) -> set[str]:
    toks: set[str] = set()
    for path in paths:
        if not os.path.exists(path):
            continue
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
        raise FeeMapError(f"0 shadow tokens from {paths} — refusing to emit "
                          f"an empty map as success")
    return toks


def cids_from_cache(cache: dict, toks: set[str]) -> dict[str, str]:
    """token -> condition_id for tokens the resolution cache knows."""
    out: dict[str, str] = {}
    for cid, m in cache.items():
        if not isinstance(m, dict):
            continue
        for k in ("yes_token_id", "no_token_id"):
            t = str(m.get(k))
            if t in toks:
                out[t] = cid
    return out


def legs_of(cache: dict, cid: str) -> list[str]:
    m = cache.get(cid)
    if not isinstance(m, dict):
        return []
    return [str(m.get(k)) for k in ("yes_token_id", "no_token_id")
            if m.get(k)]


def fetch_fee(cid: str) -> int | None:
    try:
        req = urllib.request.Request(
            f"https://clob.polymarket.com/markets/{cid}", headers=H)
        j = json.load(urllib.request.urlopen(req, timeout=12))
        v = j.get("taker_base_fee")
        return int(v) if v is not None else None
    except Exception:
        return None


async def db_token_cids(toks: list[str]) -> dict[str, tuple[str, str, str]]:
    """token -> (condition_id, yes_token, no_token) from `markets` (present
    even for unresolved markets — the cache misses most open ones)."""
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    try:
        async with db.get_session() as s:
            await s.execute(text("SET LOCAL statement_timeout='120s'"))
            rows = (await s.execute(text(
                "SELECT condition_id, yes_token_id, no_token_id, category "
                "FROM markets "
                "WHERE yes_token_id = ANY(:t) OR no_token_id = ANY(:t)"),
                {"t": toks})).fetchall()
    finally:
        await db.close()
    out = {}
    for r in rows:
        m = r._mapping
        for k in ("yes_token_id", "no_token_id"):
            t = str(m[k])
            out[t] = (str(m["condition_id"]), str(m["yes_token_id"]),
                      str(m["no_token_id"]), str(m.get("category") or ""))
    return out


async def run(args) -> int:
    toks = shadow_tokens([args.shadow, args.shadow_rtds])
    with open(args.cache) as f:
        cache = json.load(f)
    tok2cid = cids_from_cache(cache, toks)
    missing = sorted(toks - set(tok2cid))
    db_map = await db_token_cids(missing) if missing else {}
    # db_map includes BOTH legs of each found market, so count coverage on the
    # actual token set, not by adding lengths (that printed unmappable=-294)
    unmapped = toks - set(tok2cid) - set(db_map)
    print(f"shadow tokens={len(toks)}  cid-via-cache={len(tok2cid)}  "
          f"cid-via-DB={len(set(db_map) & toks)}  unmappable={len(unmapped)}")
    # cid -> its two legs (cache legs where known; DB legs for DB finds)
    cid_legs: dict[str, set[str]] = {}
    for t, cid in tok2cid.items():
        cid_legs.setdefault(cid, set()).update(legs_of(cache, cid) or [t])
    cid_cat: dict[str, str] = {}
    for t, (cid, yt, nt, cat) in db_map.items():
        cid_legs.setdefault(cid, set()).update({yt, nt})
        if cat:
            cid_cat[cid] = cat.lower()
    # category from the resolution cache fills remaining holes
    for t, cid in tok2cid.items():
        if cid not in cid_cat:
            c = cache.get(cid)
            if isinstance(c, dict) and c.get("category"):
                cid_cat[cid] = str(c["category"]).lower()
    if not cid_legs:
        raise FeeMapError("0 condition_ids mapped — nothing verified")
    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(10)

    async def one(c):
        async with sem:
            return c, await loop.run_in_executor(None, fetch_fee, c)
    res = await asyncio.gather(*(one(c) for c in sorted(cid_legs)))
    fees = {c: f for c, f in res if f is not None}
    if not fees:
        raise FeeMapError(f"0 of {len(cid_legs)} CLOB fee fetches succeeded — "
                          f"a zero-row fetch is a failure, not a map")
    fee_map: dict[str, int] = {}
    rate_map: dict[str, float] = {}
    unknown_cat = 0
    for cid, f in fees.items():
        cat = cid_cat.get(cid, "")
        if f == 0:
            rate = 0.0  # venue-verified fee-free market
        elif cat in CATEGORY_RATES:
            rate = CATEGORY_RATES[cat]
        else:
            rate = UNKNOWN_RATE
            unknown_cat += 1
        for t in cid_legs[cid]:
            fee_map[t] = f
            rate_map[t] = rate
    zeros = sum(1 for v in fee_map.values() if v == 0)
    print(f"fetched fees for {len(fees)}/{len(cid_legs)} markets "
          f"(unreachable {len(cid_legs) - len(fees)}); map covers "
          f"{len(fee_map)} tokens, zero-fee={zeros}; rate map: "
          f"{len(rate_map)} tokens, {unknown_cat} markets at the "
          f"conservative unknown-category rate {UNKNOWN_RATE}")
    if not args.write:
        print("\nDRY RUN — map NOT written. Re-run with --write to apply.")
        return 0
    if os.path.exists(args.out):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        bak = f"{args.out}.bak-{stamp}"
        if not os.path.exists(bak):
            shutil.copy2(args.out, bak)
        # merge under existing entries (a token's market fee is stable; keep
        # old mappings for tokens no longer in the shadow window)
        with open(args.out) as f:
            old = json.load(f)
        if isinstance(old, dict):
            merged = dict(old)
            merged.update(fee_map)
            fee_map = merged
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fee_map, f)
    os.replace(tmp, args.out)
    print(f"WROTE {args.out}: {len(fee_map)} tokens")
    rate_out = args.out.replace("fee_map.json", "fee_rate_map.json")
    if os.path.exists(rate_out):
        with open(rate_out) as f:
            old_rates = json.load(f)
        if isinstance(old_rates, dict):
            merged_r = dict(old_rates)
            merged_r.update(rate_map)
            rate_map = merged_r
    tmp2 = rate_out + ".tmp"
    with open(tmp2, "w") as f:
        json.dump(rate_map, f)
    os.replace(tmp2, rate_out)
    print(f"WROTE {rate_out}: {len(rate_map)} tokens")
    return 0


def _self_test() -> int:
    print("SELF-TEST — build_fee_map (offline)\n")
    ok = True
    cache = {"c1": {"yes_token_id": "y1", "no_token_id": "n1"},
             "c2": {"yes_token_id": "y2", "no_token_id": "n2"},
             "cX": "junk"}
    m = cids_from_cache(cache, {"y1", "n2", "zz"})
    ok1 = m == {"y1": "c1", "n2": "c2"}
    print(f"  [map] token->cid via cache, junk skipped : {ok1}"); ok &= ok1
    ok2 = (legs_of(cache, "c1") == ["y1", "n1"] and legs_of(cache, "cX") == []
           and legs_of(cache, "nope") == [])
    print(f"  [map] legs_of both legs, junk/absent -> [] : {ok2}"); ok &= ok2
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.jsonl")
        open(p, "w").write('{"token_id":"t1"}\nbad\n{"token_id":"t1"}\n')
        ok3 = shadow_tokens([p, os.path.join(d, "absent.jsonl")]) == {"t1"}
        print(f"  [read] dedup + absent-file tolerated : {ok3}"); ok &= ok3
        open(p, "w").write("\n")
        try:
            shadow_tokens([p])
            ok4 = False
        except FeeMapError:
            ok4 = True
        print(f"  [guard] zero tokens raises, never empty-map success : {ok4}")
        ok &= ok4
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="token -> taker_base_fee bps map "
                                             "for the shadow readout")
    ap.add_argument("--shadow", default=SHADOW)
    ap.add_argument("--shadow-rtds", dest="shadow_rtds",
                    default="/opt/pa2-shared/mirror3_shadow_rtds.jsonl")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    try:
        raise SystemExit(asyncio.run(run(a)))
    except FeeMapError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        raise SystemExit(2)
