#!/usr/bin/env python3
"""Layer 3: clean up 9 EB-family RESOLUTION rows mismatched against chain.

Discovery: 2026-05-26 chain-verification pass (this session) found 9 of 361
EB-family (EsportsBot + EsportsBotV2) trade_events.RESOLUTION rows where the
recorded outcome disagreed with Polymarket's authoritative CLOB outcome_prices.
Distribution: 3 PHANTOM_GAIN + 5 PHANTOM_LOSS + 2 PHANTOM_ZERO (bidirectional
bug class). Companion writer fix: commit 9043aea (resolution_backfill.py
outcome_prices priority).

This script is SAFE TO HOLD until the writer fix is live in production. If you
run it before the writer fix is active, the next ingestion cycle may
re-introduce the same bad rows. Recommended order:
  1. Writer fix live (master deploy OR EB splinter ingestion service running
     with eb/main code).
  2. Then run this script with --apply.

Usage:
    # On VPS:
    cd /opt/polymarket-ai-v2-esports
    PYTHONPATH=/opt/polymarket-ai-v2-esports ./venv/bin/python \\
        scripts/cleanup_eb_resolution_mismatches_2026_05_26.py            # dry-run (default)
    PYTHONPATH=/opt/polymarket-ai-v2-esports ./venv/bin/python \\
        scripts/cleanup_eb_resolution_mismatches_2026_05_26.py --apply    # mutate

Mechanism for each mismatched market:
  - Re-fetch outcome from CLOB API to confirm the chain-side truth (defensive
    — handles the unlikely case where chain itself disagrees with the
    snapshot taken at discovery time).
  - UPDATE markets SET resolution = '<chain>' WHERE condition_id = '<mid>'
    AND resolution != '<chain>' (idempotent).
  - UPDATE paper_trades SET resolution = '<chain>' WHERE market_id = '<mid>'
    AND bot_name IN (EB family) AND resolution != '<chain>' (idempotent).
    Note: realized_pnl on these rows reflects the OLD buggy resolution and is
    NOT re-computed here. Canonical P&L reads from trade_events via
    bot_pnl.py, so the paper_trades.realized_pnl drift is recordkeeping only.
  - DELETE FROM trade_events WHERE event_type = 'RESOLUTION'
    AND market_id = '<mid>' AND bot_name IN (EB family).
  - Audit trail: prints before+after counts, the chain-truth, the deleted
    trade_event row's realized_pnl per row (canonical, matches bot_pnl.py
    per-event display).
  - phase4b on the next ingestion cycle (with the fixed code) will re-emit
    the RESOLUTION row using outcome_prices priority, producing the
    chain-correct realized_pnl this time.

This script does NOT compute aggregate P&L, win rates, or any derived
financial figures. After applying, run bot_pnl.py for the corrected
canonical totals.
"""
import argparse
import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Discovered 2026-05-26. Each tuple: (condition_id, expected_chain_resolution, note).
# expected_chain_resolution is what the chain says YES/NO is (the side that
# resolved to $1.00). The bot's recorded side opposes this for losers and
# matches it for winners — we don't encode side here, we just fix
# markets.resolution to the chain-truth and let phase4b re-emit.
MISMATCHED_MARKETS = [
    # PHANTOM_GAIN — bot recorded a win, chain says loss
    ("0x5bcc52fb0270567e273a77d5446835621639596ab45c30552a3e950a0b81b909", "YES",
     "CS: GenOne vs megoshort - Map 2 Winner. Bot held NO@1.0. Chain: outcome_prices=[1,0] (YES/GenOne won)."),
    ("0xeb1c74502bbfed5e3fd95997fe2ceefc53e8cbf12f266b892766f923f297c41c", "NO",
     "LoL: Verdant vs UOL Sexy Edition - Game 1 Winner. Bot held YES@1.0. Chain: outcome_prices=[0,1] (NO/UOL won)."),
    ("0x1ddb3154b132640aca2454cdb27b5e5a84d4f8b57afbce7c7ccc5f62d776805b", "YES",
     "CS: UNO MILLE vs Procyon - BetBoom Storm Playoffs. Bot held NO@1.0. Chain: outcome_prices=[1,0]."),
    # PHANTOM_LOSS — bot recorded a loss, chain says win
    ("0x73d8e486ccd4bcda76aae2054bbbb9d5db54a750f783208a98d429c63e0f3807", "YES",
     "LoL: G2 NORD vs Witchcraft - EMEA Masters Group D. Bot held YES@0.0. Chain: outcome_prices=[1,0] (G2 NORD won)."),
    ("0x7abae048de3adaa94b32c9cd19f4003cbabf8219ce790a05681e8027c8902e5f", "YES",
     "LoL: G2 NORD vs Team Orange Gaming - Prime League. Bot held NO@1.0. Chain: outcome_prices=[1,0]."),
    ("0x9ed21dd7e558c5ca10944cf5c9942373ee4e140d02e4e8bcf4b330cd49b79e79", "YES",
     "Valorant: Nongshim RedForce vs Paper Rex - VCT Masters Santiago. Bot held YES@0.0. Chain: outcome_prices=[1,0]."),
    ("0x49e0e5ddc6a0c2d0cc8b6223d3d8687a4d590d9a1d0b7ea650dfd5882feba775", "YES",
     "Valorant: Nongshim RedForce vs G2 Esports - VCT Masters Santiago. Bot held YES@0.0. Chain: outcome_prices=[1,0]."),
    ("0x1b4aab46cc77c13dd304f74a308a7fca668b7e593174915469075a88d2d25bdc", "YES",
     "Valorant: Nongshim RedForce vs NRG - VCT Masters Santiago. Bot held YES@0.0. Chain: outcome_prices=[1,0]."),
    # PHANTOM_ZERO — label mismatch with zero realized_pnl on the row
    ("0x39c58e4ddea0d1a8d213e00fd8604bd92719249f6d2aeeca594f1179ba333ee1", "YES",
     "LoL: Nongshim Red Force vs DN SOOPers - Game 2 Winner. SELL-row label mismatch. Chain: outcome_prices=[1,0]."),
    ("0x2bb3c938279b17cd13efb728740100cbfcfbc1c699774480d42fa6d83dd4fa36", "YES",
     "LoL: Nongshim Red Force vs DN SOOPers - Game 1 Winner. Bot held NO@1.0. Chain: outcome_prices=[1,0]."),
]


EB_BOTS = ("EsportsBot", "EsportsBotV2")


async def _verify_chain(condition_id: str) -> dict:
    """Re-fetch CLOB market data. Returns dict with outcome_prices + resolution."""
    import httpx
    url = f"https://clob.polymarket.com/markets/{condition_id}"
    async with httpx.AsyncClient(timeout=15.0) as h:
        r = await h.get(url)
        if r.status_code == 200:
            return r.json()
    return {}


def _derive_chain_resolution(clob_json: dict) -> str | None:
    """Mirror the writer-fix priority: token prices first, fallback to winner flag."""
    tokens = clob_json.get("tokens") or []
    if len(tokens) >= 2:
        try:
            p0 = float(tokens[0].get("price") or 0)
            p1 = float(tokens[1].get("price") or 0)
            if p0 >= 0.99 and p1 <= 0.01:
                return "YES"
            if p0 <= 0.01 and p1 >= 0.99:
                return "NO"
        except (ValueError, TypeError):
            pass
    return None


async def main(apply: bool) -> int:
    from base_engine.data.database import Database
    from dotenv import load_dotenv
    from sqlalchemy import text
    load_dotenv()

    db = Database()
    await db.init()

    mode = "APPLY (mutating)" if apply else "DRY-RUN (read-only)"
    print(f"=== EB Resolution Mismatch Cleanup — {mode} ===\n")

    confirmed_count = 0
    chain_disagreement_count = 0
    deleted_count = 0
    updated_count = 0
    paper_updated_count = 0

    try:
        for condition_id, expected_chain, note in MISMATCHED_MARKETS:
            print(f"market: {condition_id[:12]}..")
            print(f"  expected chain: {expected_chain}")
            print(f"  note: {note}")

            # Re-verify against live CLOB
            try:
                clob = await _verify_chain(condition_id)
            except Exception as e:
                print(f"  ERROR: CLOB fetch failed: {e}")
                continue
            chain_now = _derive_chain_resolution(clob)
            print(f"  live chain resolution: {chain_now}")
            if chain_now is None:
                print(f"  SKIP: chain has no clean outcome_prices yet")
                continue
            if chain_now != expected_chain:
                print(f"  WARN: live chain ({chain_now}) disagrees with discovery-snapshot ({expected_chain}). Skipping for safety.")
                chain_disagreement_count += 1
                continue
            confirmed_count += 1

            # Look at current DB state
            async with db.get_session() as s:
                r = await s.execute(text(
                    "SELECT resolution FROM markets WHERE condition_id = :cid"
                ), {"cid": condition_id})
                row = r.fetchone()
                current_db_res = row[0] if row else None
            print(f"  current markets.resolution: {current_db_res}")

            # Count existing trade_events.RESOLUTION rows for EB family
            async with db.get_session() as s:
                r = await s.execute(text(
                    "SELECT bot_name, side, price, realized_pnl "
                    "FROM trade_events WHERE event_type = 'RESOLUTION' "
                    "AND market_id = :cid AND bot_name = ANY(:bots)"
                ), {"cid": condition_id, "bots": list(EB_BOTS)})
                existing = r.fetchall()
            print(f"  existing trade_events.RESOLUTION rows: {len(existing)}")
            for ex in existing:
                print(f"    {ex[0]} side={ex[1]} price={ex[2]} realized_pnl={ex[3]} (canonical, matches bot_pnl.py per-event display)")

            # Count paper_trades rows needing resolution UPDATE (EB family)
            async with db.get_session() as s:
                r = await s.execute(text(
                    "SELECT bot_name, side, resolution, realized_pnl "
                    "FROM paper_trades WHERE market_id = :cid "
                    "AND bot_name = ANY(:bots)"
                ), {"cid": condition_id, "bots": list(EB_BOTS)})
                paper_rows = r.fetchall()
            paper_rows_needing_update = [p for p in paper_rows if p[2] != chain_now]
            print(f"  paper_trades rows (EB family): {len(paper_rows)} total, {len(paper_rows_needing_update)} need resolution UPDATE")
            for p in paper_rows_needing_update:
                print(f"    {p[0]} side={p[1]} resolution={p[2]} (stale; realized_pnl={p[3]} preserved)")

            if not apply:
                print(f"  DRY-RUN: would UPDATE markets.resolution to '{chain_now}' if != current, UPDATE {len(paper_rows_needing_update)} paper_trades row(s), and DELETE {len(existing)} trade_events row(s)\n")
                continue

            # APPLY mutations — one transaction per market
            async with db.get_session() as s:
                if current_db_res != chain_now:
                    r = await s.execute(text(
                        "UPDATE markets SET resolution = :new WHERE condition_id = :cid AND resolution IS DISTINCT FROM :new"
                    ), {"new": chain_now, "cid": condition_id})
                    updated_count += 1
                    print(f"  UPDATED markets.resolution -> {chain_now}")
                if paper_rows_needing_update:
                    r2 = await s.execute(text(
                        "UPDATE paper_trades SET resolution = :new "
                        "WHERE market_id = :cid AND bot_name = ANY(:bots) "
                        "AND resolution IS DISTINCT FROM :new"
                    ), {"new": chain_now, "cid": condition_id, "bots": list(EB_BOTS)})
                    paper_updated_count += r2.rowcount or 0
                    print(f"  UPDATED {r2.rowcount} paper_trades.resolution row(s) -> {chain_now}")
                if existing:
                    await s.execute(text(
                        "DELETE FROM trade_events WHERE event_type = 'RESOLUTION' "
                        "AND market_id = :cid AND bot_name = ANY(:bots)"
                    ), {"cid": condition_id, "bots": list(EB_BOTS)})
                    deleted_count += len(existing)
                    print(f"  DELETED {len(existing)} trade_events.RESOLUTION row(s)")
                await s.commit()
            print()
    finally:
        await db.close()

    print("=== Summary ===")
    print(f"  Markets attempted:               {len(MISMATCHED_MARKETS)}")
    print(f"  Chain-confirmed mismatches:      {confirmed_count}")
    print(f"  Chain disagreement (skipped):    {chain_disagreement_count}")
    if apply:
        print(f"  markets.resolution updates:      {updated_count}")
        print(f"  paper_trades.resolution updates: {paper_updated_count}")
        print(f"  trade_events.RESOLUTION deletes: {deleted_count}")
        print()
        print("Next steps:")
        print("  1. Verify the writer fix (commit 9043aea) is running in production.")
        print("  2. Run resolution_backfill (it runs every INGESTION_SCHEDULER_INTERVAL_MINUTES).")
        print("  3. After phase4b re-emits, run bot_pnl.py EsportsBotV2 720 to see the corrected canonical numbers.")
    else:
        print()
        print("DRY-RUN complete. Re-run with --apply to mutate.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Mutate the DB. Default is dry-run.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
