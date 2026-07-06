#!/usr/bin/env python3
"""
WB Snapshot Scorer — Phase-2 Method 2 (clean) for WB_REBUILD_PLAN.md.

Grades a wb_signal_probe.py --snapshot file against ACTUAL market resolutions.
The snapshot captured, at a known time, the model's P(YES) and the REAL live
market price (verified live Gamma `outcomePrices`) for each bucket. Once those
markets resolve, this scores Brier(model) vs Brier(market) on the same buckets.

This is the un-contaminated test: market prices are the real live ones (not the
bug-era logged prices that made Method 1 untrustworthy), so the market's own
Brier here is the validation — it should land BELOW the base-rate baseline.

Resolution source: markets table (authoritative). market_id in the snapshot is
the Gamma market id, stored as markets.id.

READ-ONLY.

Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_snapshot_score.py SNAPSHOT.jsonl
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _brier(rows):
    n = len(rows)
    if not n:
        return (0, 0.0, 0.0, 0.0, 0.0, 0.0)
    bm = sum((m - a) ** 2 for m, _k, a in rows) / n
    bk = sum((k - a) ** 2 for _m, k, a in rows) / n
    mm = sum(m for m, _k, _a in rows) / n
    mk = sum(k for _m, k, _a in rows) / n
    base = sum(a for _m, _k, a in rows) / n
    return (n, bm, bk, mm, mk, base)


def _line(label, rows):
    n, bm, bk, mm, mk, base = _brier(rows)
    if not n:
        print(f"  {label:<22} n=0")
        return
    verdict = "MODEL wins" if bm < bk else ("MARKET wins" if bk < bm else "tie")
    print(f"  {label:<22} n={n:<5} BS_model={bm:.4f}  BS_market={bk:.4f}  "
          f"Δ={bk-bm:+.4f}  [{verdict}]")


async def main():
    if len(sys.argv) < 2:
        print("usage: wb_snapshot_score.py SNAPSHOT.jsonl")
        return
    path = sys.argv[1]
    recs = []
    with open(path) as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                recs.append(json.loads(ln))
    if not recs:
        print("empty snapshot")
        return
    ids = sorted({str(r["market_id"]) for r in recs})

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    res_map = {}
    try:
        async with db.get_session() as session:
            result = await session.execute(text("""
                SELECT id, resolution, resolved
                FROM markets
                WHERE id = ANY(:ids) AND resolution IN ('YES','NO')
            """), {"ids": ids})
            for mid, resolution, resolved in result.fetchall():
                res_map[str(mid)] = resolution
    finally:
        try:
            await db.close()
        except Exception:
            pass

    scored, unresolved = [], 0
    by_lead = {}
    for r in recs:
        res = res_map.get(str(r["market_id"]))
        if res is None:
            unresolved += 1
            continue
        actual = 1.0 if res == "YES" else 0.0
        rec = (float(r["model_prob"]), float(r["market_price"]), actual)
        scored.append(rec)
        lh = r.get("lead_h")
        bucket = ("lead:<24h" if lh is not None and lh < 24
                  else "lead:24-48h" if lh is not None and lh < 48
                  else "lead:48h+")
        by_lead.setdefault(bucket, []).append(rec)

    print("=" * 72)
    print(f"WB SNAPSHOT SCORE — {path}")
    print(f"  snapshot ts: {recs[0].get('snap_ts')}")
    print("=" * 72)
    print(f"  buckets in snapshot: {len(recs)}   resolved+scored: {len(scored)}   "
          f"still unresolved: {unresolved}")
    if not scored:
        print("  nothing resolved yet — re-run later.")
        return
    n, bm, bk, mm, mk, base = _brier(scored)
    print(f"  base rate P(YES)={base:.3f}   mean model P={mm:.3f}   mean market P={mk:.3f}")
    print(f"  SANITY: always-base-rate Brier={base*(1-base):.4f}; "
          f"a real market should beat it. mean market P vs base = {abs(mk-base):.3f}")
    print()
    _line("OVERALL", scored)
    for b in ("lead:<24h", "lead:24-48h", "lead:48h+"):
        if b in by_lead:
            _line(b, by_lead[b])
    print()
    if bm < bk:
        print(f"  => MODEL beats market by {bk-bm:+.4f} on CLEAN live prices. Real edge signal.")
    else:
        print(f"  => MARKET beats/ties model ({bk-bm:+.4f}) on CLEAN live prices. "
              f"No edge — overconfidence confirmed.")


if __name__ == "__main__":
    asyncio.run(main())
