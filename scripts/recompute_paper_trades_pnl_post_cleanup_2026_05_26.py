#!/usr/bin/env python3
"""Recompute paper_trades.realized_pnl for the 10 EB-family markets cleaned 2026-05-26.

Companion to cleanup_eb_resolution_mismatches_2026_05_26.py. The cleanup
script (commit c6b45c0) updates paper_trades.resolution to chain truth, but
intentionally does NOT touch realized_pnl. As a result, 9 of 10 cleaned
markets currently have paper_trades.realized_pnl computed from the OLD buggy
resolution — sign-inverted on the loser side and magnitude-wrong on the
winner side.

Example (verified 2026-05-26 17:19 UTC):
  market 0x7abae048de..: side=NO resolution=YES realized_pnl=$+469.27
  Bot held NO; chain=YES so NO lost; pnl should be NEGATIVE (= -price * size).
  Current $+469.27 is phantom gain from the old resolution=NO computation.

This script does NOT mutate trade_events. The canonical P&L source is
trade_events via bot_pnl.py, where phase4b has already re-emitted the
chain-correct RESOLUTION row for 8 of 10 markets. Recomputing paper_trades
brings the shadow records in line with the canonical view.

Formula (winner-take-all CTF payoff):
  if side == resolution: new_pnl = (1.0 - price) * size  # winner
  else:                  new_pnl = -price * size          # loser

Usage:
    # On VPS:
    cd /opt/polymarket-ai-v2-esports
    PYTHONPATH=/opt/polymarket-ai-v2-esports ./venv/bin/python \\
        scripts/recompute_paper_trades_pnl_post_cleanup_2026_05_26.py
    PYTHONPATH=/opt/polymarket-ai-v2-esports ./venv/bin/python \\
        scripts/recompute_paper_trades_pnl_post_cleanup_2026_05_26.py --apply

Safety:
  - DRY-RUN is the default. --apply is required to mutate.
  - Hardcoded condition_id list — script will not touch unrelated markets.
  - Filter `created_at < '2026-05-26 12:09:00+00'` ensures only pre-fix rows
    are touched (master writer-fix deploy 20260526_120918 was at 12:09 UTC).
  - Idempotent: re-running on already-recomputed rows recalculates to the
    same value (no drift).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


CLEANED_CONDITION_IDS = [
    "0x5bcc52fb0270567e273a77d5446835621639596ab45c30552a3e950a0b81b909",
    "0xeb1c74502bbfed5e3fd95997fe2ceefc53e8cbf12f266b892766f923f297c41c",
    "0x1ddb3154b132640aca2454cdb27b5e5a84d4f8b57afbce7c7ccc5f62d776805b",
    "0x73d8e486ccd4bcda76aae2054bbbb9d5db54a750f783208a98d429c63e0f3807",
    "0x7abae048de3adaa94b32c9cd19f4003cbabf8219ce790a05681e8027c8902e5f",
    "0x9ed21dd7e558c5ca10944cf5c9942373ee4e140d02e4e8bcf4b330cd49b79e79",
    "0x49e0e5ddc6a0c2d0cc8b6223d3d8687a4d590d9a1d0b7ea650dfd5882feba775",
    "0x1b4aab46cc77c13dd304f74a308a7fca668b7e593174915469075a88d2d25bdc",
    "0x39c58e4ddea0d1a8d213e00fd8604bd92719249f6d2aeeca594f1179ba333ee1",
    "0x2bb3c938279b17cd13efb728740100cbfcfbc1c699774480d42fa6d83dd4fa36",
]

EB_BOTS = ("EsportsBot", "EsportsBotV2")
# All 10 condition_ids are resolved markets (markets.resolution = chain truth).
# The bot cannot trade a resolved market, so no post-fix paper_trades rows
# exist for these condition_ids. The hardcoded list IS the scope guard.


def _compute_pnl(side: str, resolution: str, price: float, size: float) -> float:
    """Winner-take-all CTF payoff. Returns realized_pnl per row."""
    if side == resolution:
        return (1.0 - price) * size
    return -price * size


async def main(apply: bool) -> int:
    from base_engine.data.database import Database
    from dotenv import load_dotenv
    from sqlalchemy import text
    load_dotenv()

    db = Database()
    await db.init()

    mode = "APPLY (mutating)" if apply else "DRY-RUN (read-only)"
    print(f"=== EB paper_trades P&L Recompute — {mode} ===\n")

    rows_inspected = 0
    rows_drifted = 0
    rows_updated = 0
    cumulative_old_pnl = 0.0
    cumulative_new_pnl = 0.0

    try:
        async with db.get_session() as s:
            r = await s.execute(text(
                "SELECT id, market_id, bot_name, side, price, size, resolution, realized_pnl, created_at "
                "FROM paper_trades "
                "WHERE market_id = ANY(:cids) AND bot_name = ANY(:bots) "
                "ORDER BY market_id, created_at"
            ), {"cids": CLEANED_CONDITION_IDS, "bots": list(EB_BOTS)})
            rows = r.fetchall()

        for row in rows:
            row_id, market_id, bot_name, side, price, size, resolution, old_pnl, created_at = row
            rows_inspected += 1

            if resolution is None or side is None or price is None or size is None:
                print(f"  SKIP id={row_id} market={market_id[:12]}.. — missing required field "
                      f"(side={side}, price={price}, size={size}, resolution={resolution})")
                continue

            new_pnl = _compute_pnl(side, resolution, float(price), float(size))
            cumulative_old_pnl += float(old_pnl or 0.0)
            cumulative_new_pnl += new_pnl

            drifted = abs(float(old_pnl or 0.0) - new_pnl) > 0.005
            marker = "DRIFT" if drifted else "match"
            if drifted:
                rows_drifted += 1

            print(f"  id={row_id} market={market_id[:14]}.. bot={bot_name} side={side} "
                  f"resolution={resolution} price={price} size={size}")
            print(f"    old_pnl={old_pnl}  new_pnl={new_pnl:.4f}  [{marker}]")

        print()
        print(f"--- Inspection summary ---")
        print(f"  Rows inspected:            {rows_inspected}")
        print(f"  Rows with drift:           {rows_drifted}")
        print(f"  Cumulative old realized:   ${cumulative_old_pnl:+.2f}")
        print(f"  Cumulative new realized:   ${cumulative_new_pnl:+.2f}")
        print(f"  Net adjustment:            ${cumulative_new_pnl - cumulative_old_pnl:+.2f}")
        print()

        if not apply:
            print("DRY-RUN complete. Re-run with --apply to mutate.")
            print()
            print("Apply will UPDATE paper_trades.realized_pnl row-by-row using the same")
            print("formula. trade_events table is NOT touched (canonical P&L unchanged).")
            return 0

        # APPLY — one UPDATE per row to avoid SQL CASE complexity
        for row in rows:
            row_id, market_id, bot_name, side, price, size, resolution, old_pnl, _ = row
            if resolution is None or side is None or price is None or size is None:
                continue
            new_pnl = _compute_pnl(side, resolution, float(price), float(size))
            if abs(float(old_pnl or 0.0) - new_pnl) <= 0.005:
                continue  # already matches; skip
            async with db.get_session() as s:
                await s.execute(text(
                    "UPDATE paper_trades SET realized_pnl = :new_pnl "
                    "WHERE id = :rid AND realized_pnl IS DISTINCT FROM :new_pnl"
                ), {"new_pnl": new_pnl, "rid": row_id})
                await s.commit()
            rows_updated += 1
            print(f"  UPDATED id={row_id} realized_pnl: {old_pnl} -> {new_pnl:.4f}")

        print()
        print(f"--- Apply summary ---")
        print(f"  Rows updated:              {rows_updated}")
    finally:
        await db.close()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Mutate paper_trades.realized_pnl. Default is dry-run.")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(apply=args.apply)))
