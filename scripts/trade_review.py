#!/usr/bin/env python3
"""Per-trade review for MirrorBot live trades: amount + model confidence + W/L outcome.

Joins three sources, each canonical for its column:
  - trade_events   — the trade itself (side, size, price, amount, time). Counts agree
                     with bot_pnl.py (ENTRY/EXIT tallies).
  - prediction_log — model confidence / predicted_prob / edge, attached by market +
                     nearest-prediction-time (trade_events doesn't store confidence inline).
  - on-chain / CLOB — WIN/LOSS/EXITED/OPEN. Per the standing rule, on-chain reconciliation
                     is canonical for LIVE outcomes (the bot's internal P&L ledger is
                     structurally incomplete: cost basis cleared on close, ~0 live RESOLUTION
                     events). Same logic as reconcile_live_onchain.py.

Writes a CSV (full market_ids) and prints a table + outcome summary.
Read-only. Usage: python scripts/trade_review.py [--mode live|paper|all] [--csv PATH]
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["live", "paper", "all"], default="live")
    ap.add_argument("--csv", default="trade_review.csv")
    args = ap.parse_args()

    from sqlalchemy import text
    from base_engine.data.database import Database
    from base_engine.data.resolution_backfill import _fetch_market_by_condition_id, _clob_to_market_format

    mode_clause = "" if args.mode == "all" else f"AND te.execution_mode = '{args.mode}'"
    db = Database()
    await db.init()
    async with db.get_session() as s:
        # ENTRY trades + confidence (lateral nearest-time prediction) + whether a matching EXIT exists
        rows = (await s.execute(text(f"""
            SELECT te.event_time::timestamp(0) AS t, te.market_id, te.side, te.token_id,
                   CAST(te.size AS numeric) AS size, CAST(te.price AS numeric) AS price,
                   ROUND(CAST(te.size AS numeric) * CAST(te.price AS numeric), 2) AS amount_usd,
                   pl.confidence, pl.predicted_prob, pl.edge,
                   EXISTS (SELECT 1 FROM trade_events ex
                           WHERE ex.bot_name='MirrorBot' AND ex.event_type='EXIT'
                             AND ex.market_id=te.market_id AND ex.side=te.side
                             {mode_clause.replace('te.', 'ex.')}) AS has_exit
            FROM trade_events te
            LEFT JOIN LATERAL (
                SELECT confidence, predicted_prob, edge FROM prediction_log pl
                WHERE pl.market_id = te.market_id
                ORDER BY ABS(EXTRACT(EPOCH FROM (pl.prediction_time - te.event_time))) ASC
                LIMIT 1
            ) pl ON TRUE
            WHERE te.bot_name='MirrorBot' AND te.event_type='ENTRY' {mode_clause}
            ORDER BY te.event_time DESC
        """))).fetchall()

    # resolve W/L per market via CLOB (cache per market_id)
    resolved_cache = {}

    async def outcome_for(market_id, side, has_exit):
        if has_exit:
            return "EXITED"
        if market_id not in resolved_cache:
            clob = await _fetch_market_by_condition_id(market_id)
            res = _clob_to_market_format(clob, market_id).get("resolution") if clob else None
            closed = bool(clob and clob.get("closed"))
            resolved_cache[market_id] = (closed, res)
        closed, res = resolved_cache[market_id]
        if not closed:
            return "OPEN"
        if res in ("YES", "NO"):
            return "WIN" if side == res else "LOSS"
        return "?"

    out = []
    for r in rows:
        t, mkt, side, tok, size, price, amt, conf, pred, edge, has_exit = r
        oc = await outcome_for(mkt, side, has_exit)
        out.append({
            "time": str(t), "market_id": mkt, "side": side,
            "size": f"{float(size):.3f}", "price": f"{float(price):.3f}",
            "amount_usd": f"{float(amt):.2f}",
            "confidence": f"{float(conf):.3f}" if conf is not None else "",
            "predicted_prob": f"{float(pred):.3f}" if pred is not None else "",
            "edge": f"{float(edge):.3f}" if edge is not None else "",
            "outcome": oc,
        })

    # table
    print(f"\n=== MirrorBot trade review ({args.mode}) — {len(out)} entries ===")
    print(f"  {'time':19} {'side':4} {'amt$':>5} {'conf':>5} {'pred':>5} {'edge':>5}  outcome")
    tally = {}
    for o in out:
        tally[o["outcome"]] = tally.get(o["outcome"], 0) + 1
        print(f"  {o['time']:19} {o['side']:4} {o['amount_usd']:>5} "
              f"{o['confidence'] or '  -  ':>5} {o['predicted_prob'] or '  -  ':>5} "
              f"{o['edge'] or '  -  ':>5}  {o['outcome']}")
    print(f"\n  outcomes: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))

    # csv
    csv_path = Path(args.csv)
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()) if out else
                           ["time", "market_id", "side", "size", "price", "amount_usd",
                            "confidence", "predicted_prob", "edge", "outcome"])
        w.writeheader()
        w.writerows(out)
    print(f"\n  CSV written: {csv_path.resolve()}  ({len(out)} rows)")
    await db.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
