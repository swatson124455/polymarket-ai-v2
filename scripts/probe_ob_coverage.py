#!/usr/bin/env python3
"""Probe: WHY did the fill gate report 0% orderbook coverage for the
chain-audit CLEAN roster? Two hypotheses, one read-only query session:

  A. KEY MISMATCH — orderbook_snapshots.token_id stores a different shape
     (hex, market id, ...) than the cache's decimal CLOB tokenId, so the
     join silently misses (the order_gateway market-index landmine class).
  B. GENUINE GAP — snapshots only cover the bots' own tracked markets
     (~top-200 by volume) and the roster trades elsewhere; nothing to join.

Prints: sample token_id values from the table (shape check by eye), table
time range, then for the CLEAN roster's cached BUY tokens: how many appear
in orderbook_snapshots AT ALL (any time). A(match>0 but fill-gate cov=0)
would need time-window analysis; A(shape differs) is instant; B shows as
zero matches with matching shapes.

READ-ONLY. Small ANY() queries under statement_timeout.
INVOCATION (VPS):
    cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/tmp/mbpc2 \
      venv/bin/python /tmp/mbpc2/scripts/probe_ob_coverage.py \
      --from-audit /tmp/chain_audit.json --cache /tmp/copyable_cache
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from sqlalchemy import text
    from base_engine.data.database import Database

    with open(args.from_audit) as f:
        clean = json.load(f).get("clean", [])
    print(f"CLEAN roster: {len(clean)} traders")

    # collect each clean trader's BUY tokenIds from the cache
    tokens: set[str] = set()
    per_trader: dict[str, set] = {}
    for a in clean:
        cp = os.path.join(args.cache, f"{a}.json")
        if not os.path.exists(cp):
            continue
        with open(cp) as f:
            blob = json.load(f)
        trades = blob.get("trades", blob) if isinstance(blob, (dict, list)) else []
        ts = {str(t.get("tokenId")) for t in trades
              if str(t.get("side", "")).upper() == "BUY"
              and str(t.get("tokenId", "")).isdigit()}
        per_trader[a] = ts
        tokens |= ts
    print(f"distinct BUY tokenIds across roster caches: {len(tokens):,}")

    db = Database()
    await db.init()
    try:
        async with db.get_session() as s:
            await s.execute(text("SET LOCAL statement_timeout = '60s'"))
            rows = (await s.execute(text(
                "SELECT token_id, snapshot_time FROM orderbook_snapshots "
                "ORDER BY snapshot_time DESC LIMIT 5"))).fetchall()
            print("\nsample orderbook_snapshots.token_id (newest 5):")
            for r in rows:
                print(f"  {str(r[0])[:70]}  @ {r[1]}")
            # catalog ESTIMATE — a real count(*)/min/max seq-scans 37.7M rows
            # and times out (the mirror_rejected_signals lesson, re-learned
            # 2026-07-11 when v1 of this probe did exactly that)
            est = (await s.execute(text(
                "SELECT reltuples::bigint FROM pg_class "
                "WHERE relname = 'orderbook_snapshots'"))).scalar()
            print(f"table rows (catalog estimate): {int(est or 0):,}")

        # membership: which roster tokens exist in the table AT ALL
        # (token_id lookups are index-backed — the fills gate's point
        # queries return fast; a timed-out chunk is reported, not fatal)
        tok_list = sorted(tokens)
        found: set[str] = set()
        chunks_failed = 0
        B = 500
        for i in range(0, len(tok_list), B):
            chunk = tok_list[i:i + B]
            try:
                async with db.get_session() as s:
                    await s.execute(text("SET LOCAL statement_timeout = '60s'"))
                    rows = (await s.execute(text(
                        "SELECT DISTINCT token_id FROM orderbook_snapshots "
                        "WHERE token_id = ANY(:toks)"), {"toks": chunk})).fetchall()
                found |= {str(r[0]) for r in rows}
            except Exception as e:
                chunks_failed += 1
                print(f"  chunk {i // B} FAILED ({type(e).__name__}) — "
                      f"skipped, membership is a lower bound")
        if chunks_failed:
            print(f"NOTE: {chunks_failed} chunk(s) failed — 'present' counts "
                  f"are lower bounds")
        print(f"\nroster tokens present in orderbook_snapshots (ANY time): "
              f"{len(found):,}/{len(tokens):,}")
        print("\nper trader (tokens present / tokens):")
        for a in clean:
            ts = per_trader.get(a, set())
            if not ts:
                print(f"  {a[:14]}  no cache")
                continue
            print(f"  {a[:14]}  {len(ts & found):>6,}/{len(ts):,}")
        print("\nREAD: shapes match + ~zero present  -> GENUINE coverage gap "
              "(forward shadow needed).")
        print("      shapes differ                  -> KEY MISMATCH (fix the "
              "join, rerun the gate).")
        print("      many present but gate cov=0    -> TIME-WINDOW bug in the "
              "gate (investigate book_at).")
    finally:
        await db.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-audit", default="/tmp/chain_audit.json",
                    dest="from_audit")
    ap.add_argument("--cache", default="/tmp/copyable_cache")
    raise SystemExit(asyncio.run(run(ap.parse_args())))
