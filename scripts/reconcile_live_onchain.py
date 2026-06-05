#!/usr/bin/env python3
"""READ-ONLY on-chain reconciliation of EVERY live MirrorBot position.

Writes nothing. For each distinct live (market, side) position it cross-checks:
  - CLOB resolution (closed? which side won?)
  - on-chain CTF ERC1155 balance (what the deposit wallet actually holds)
  - cost-basis recoverability (does a live ENTRY trade_event exist?)
  - whether it was exited via a live EXIT trade_event

Purpose: establish on-chain ground truth independent of the (known-broken)
trade_events live ledger, and quantify exactly how much true P&L is recoverable
vs. lost (cost basis cleared on close + missing ENTRY events).
"""
import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")


async def main() -> int:
    from sqlalchemy import text
    from base_engine.data.database import Database
    from base_engine.data.resolution_backfill import _fetch_market_by_condition_id, _clob_to_market_format
    from base_engine.execution.clob_adapter import check_ctf_balance

    db = Database()
    await db.init()
    async with db.get_session() as s:
        rows = await s.execute(text("""
            SELECT DISTINCT ON (p.market_id, p.side)
                   p.market_id, p.side, p.token_id, p.status,
                   en.cost AS entry_cost_te,
                   (en.cost IS NOT NULL) AS has_live_entry,
                   (ex.n IS NOT NULL) AS has_live_exit
            FROM positions p
            LEFT JOIN (
                SELECT market_id, side, SUM(CAST(size AS float)*CAST(price AS float)) cost
                FROM trade_events
                WHERE bot_name='MirrorBot' AND event_type='ENTRY' AND execution_mode='live'
                GROUP BY market_id, side
            ) en ON en.market_id=p.market_id AND en.side=p.side
            LEFT JOIN (
                SELECT market_id, side, COUNT(*) n
                FROM trade_events
                WHERE bot_name='MirrorBot' AND event_type='EXIT' AND execution_mode='live'
                GROUP BY market_id, side
            ) ex ON ex.market_id=p.market_id AND ex.side=p.side
            WHERE COALESCE(p.source_bot,p.bot_id)='MirrorBot' AND p.is_paper=false
            ORDER BY p.market_id, p.side, p.opened_at DESC
        """))
        positions = rows.fetchall()

    print(f"distinct live MB positions (market x side): {len(positions)}")
    print("market           side stat   resolved winner outcome  onchain_bal cost_basis")
    n_exit = n_win = n_loss = n_open = n_unk = 0
    n_have_cost = n_no_cost = 0
    redeemable = 0.0
    for market_id, side, token_id, status, cost_te, has_entry, has_exit in positions:
        clob = await _fetch_market_by_condition_id(market_id)
        resolved = bool(clob and clob.get("closed"))
        res = _clob_to_market_format(clob, market_id).get("resolution") if clob else None
        try:
            bal = await check_ctf_balance(token_id) if token_id else None
        except Exception:
            bal = None
        if has_entry:
            n_have_cost += 1
        else:
            n_no_cost += 1
        if has_exit:
            outcome = "EXITED"; n_exit += 1
        elif resolved and res in ("YES", "NO"):
            if side == res:
                outcome = "WIN"; n_win += 1; redeemable += (bal or 0.0)
            else:
                outcome = "LOSS"; n_loss += 1
        elif not resolved:
            outcome = "OPEN"; n_open += 1
        else:
            outcome = "?"; n_unk += 1
        cb = f"${cost_te:.3f}" if has_entry else "MISSING"
        bs = f"{bal:.3f}" if bal is not None else "n/a"
        print(f"{market_id[:16]} {str(side):3} {str(status):6} {str(resolved):5} {str(res):4} {outcome:7} {bs:>11} {cb}")

    print(f"\n=== SUMMARY ({len(positions)} live positions) ===")
    print(f"  outcomes:    EXITED={n_exit}  WIN={n_win}  LOSS={n_loss}  OPEN={n_open}  unclassified={n_unk}")
    print(f"  cost basis:  recoverable(live ENTRY)={n_have_cost}   MISSING(phantom)={n_no_cost}")
    print(f"  on-chain redeemable winning-token balance still held: {redeemable:.4f}")
    print(f"  NOTE: true realized P&L is computable only for the {n_have_cost} with a live ENTRY cost basis;")
    print(f"        the {n_no_cost} phantoms have NO cost basis in the DB (entry_cost cleared on close + no ENTRY event)")
    await db.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
