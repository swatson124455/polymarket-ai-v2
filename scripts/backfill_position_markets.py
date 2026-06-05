#!/usr/bin/env python3
"""One-time TARGETED resolution backfill for markets with OPEN LIVE positions.

Context (2026-06-02): the end_date_iso population fix (commit abf5a34) stops NEW
NULL end-dates, but the markets where MirrorBot already holds open live positions
are still NULL-dated and resolved=false in the DB even though several have
resolved on-chain. The scheduled resolution_backfill won't reach them — its
discovery orders by end_date_iso NULLS LAST and prioritizes OLDEST-end markets,
so a recently-resolved market with an open position is starved behind the
backlog. That leaves MirrorBot trying to SELL into resolved markets (orderbook
gone) and tripping the circuit breaker.

This processes EXACTLY the open live position markets via the SAME tested helpers
that run_resolution_backfill uses internally — no hand-mapped YES/NO inference.

Usage:
  python scripts/backfill_position_markets.py --dry-run   # show, write nothing
  python scripts/backfill_position_markets.py             # apply
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")


async def main(dry_run: bool) -> int:
    from sqlalchemy import text
    from base_engine.data.database import Database
    from base_engine.data.resolution_backfill import (
        _fetch_market_by_condition_id,
        _clob_to_market_format,
    )
    from base_engine.data.resolution_observation import record_resolution_observation

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized")
        return 1

    async with db.get_session() as s:
        rows = await s.execute(text(
            "SELECT DISTINCT p.market_id FROM positions p "
            "WHERE p.status='open' AND p.is_paper=false "
            "  AND COALESCE(p.source_bot, p.bot_id)='MirrorBot' AND p.market_id IS NOT NULL"
        ))
        mids = [r[0] for r in rows.fetchall() if r[0]]
    print(f"{'DRY-RUN: ' if dry_run else ''}open live MirrorBot position markets: {len(mids)}")

    enddate_n = resolved_n = 0
    for mid in mids:
        if not (str(mid).startswith("0x") and len(str(mid)) == 66):
            print(f"  {str(mid)[:18]} skip (not a condition_id)")
            continue
        clob = await _fetch_market_by_condition_id(mid)
        if not clob:
            print(f"  {mid[:14]} no CLOB data")
            continue
        m = _clob_to_market_format(clob, mid)
        end_iso = m.get("end_date_iso")
        res = m.get("resolution")
        closed = bool(clob.get("closed"))
        toks = [(t.get("outcome"), t.get("price"), t.get("winner")) for t in (clob.get("tokens") or [])]
        end_dt = None
        if end_iso:
            try:
                end_dt = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, TypeError):
                end_dt = None

        plan = []
        if end_dt is not None:
            plan.append(f"set end_date={end_dt.date()}")
        if closed and res in ("YES", "NO"):
            plan.append(f"RESOLVE={res}")
        print(f"  {mid[:14]} closed={closed} res={res} tokens={toks} -> {', '.join(plan) or 'no-op'}")

        if dry_run:
            continue

        if end_dt is not None:
            async with db.get_session() as s:
                await s.execute(text(
                    "UPDATE markets SET end_date_iso=:ed "
                    "WHERE (id=:mid OR condition_id=:mid) AND end_date_iso IS NULL"
                ), {"ed": end_dt, "mid": mid})
                await s.commit()
            enddate_n += 1
        if closed and res in ("YES", "NO"):
            _resolved_at = record_resolution_observation(
                datetime.now(timezone.utc).replace(tzinfo=None),
                market_id=str(mid), scheduled_close=end_dt,
                source="targeted_position_backfill",
            )
            await db.save_market_resolution(mid, True, res, "clob_api", _resolved_at)
            try:
                await db.mark_market_resolved(mid, res)
            except Exception as e:
                print(f"    mark_market_resolved warn: {e}")
            resolved_n += 1

    await db.close()
    if dry_run:
        print("DRY-RUN complete (no writes).")
    else:
        print(f"DONE: end_date patched={enddate_n}, markets resolved={resolved_n}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Show planned changes without writing")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
