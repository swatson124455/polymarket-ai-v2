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
    ap.add_argument("--sort", choices=["time", "confidence", "edge", "amount"], default="time",
                    help="sort order (default time, newest first; others descending)")
    ap.add_argument("--source", choices=["trades", "positions"], default="trades",
                    help="trades = ENTRY-recorded trade_events (clean records); "
                         "positions = ALL wagers from the positions table (incl. phantom-entry, on-chain truth)")
    args = ap.parse_args()

    from sqlalchemy import text
    from base_engine.data.database import Database
    from base_engine.data.resolution_backfill import _fetch_market_by_condition_id, _clob_to_market_format
    from base_engine.execution.clob_adapter import check_ctf_balance

    mode_clause = "" if args.mode == "all" else f"AND te.execution_mode = '{args.mode}'"
    pmode_clause = "" if args.mode == "all" else f"AND p.is_paper = {'false' if args.mode == 'live' else 'true'}"
    db = Database()
    await db.init()
    async with db.get_session() as s:
        if args.source == "positions":
            # EVERY distinct (market, side) wager the bot took — the full on-chain footprint,
            # including phantom positions with no clean ENTRY trade_event. Cost basis comes
            # from the positions row (entry_price * size); confidence from prediction_log;
            # on-chain CTF balance + CLOB resolution give the true outcome.
            rows = (await s.execute(text(f"""
                SELECT DISTINCT ON (p.market_id, p.side)
                       p.opened_at::timestamp(0) AS t, p.market_id, p.side, p.token_id,
                       CAST(p.size AS numeric) AS size, CAST(p.entry_price AS numeric) AS price,
                       ROUND(CAST(p.size AS numeric) * CAST(p.entry_price AS numeric), 2) AS amount_usd,
                       pl.confidence, pl.predicted_prob, pl.edge,
                       EXISTS (SELECT 1 FROM trade_events ex
                               WHERE ex.bot_name='MirrorBot' AND ex.event_type='EXIT'
                                 AND ex.market_id=p.market_id AND ex.side=p.side) AS has_exit,
                       (SELECT COUNT(*) FROM trade_events en
                        WHERE en.bot_name='MirrorBot' AND en.event_type='ENTRY'
                          AND en.market_id=p.market_id AND en.side=p.side) AS entry_recs
                FROM positions p
                LEFT JOIN LATERAL (
                    SELECT confidence, predicted_prob, edge FROM prediction_log pl
                    WHERE pl.market_id = p.market_id
                    ORDER BY ABS(EXTRACT(EPOCH FROM (pl.prediction_time - p.opened_at))) ASC
                    LIMIT 1
                ) pl ON TRUE
                WHERE COALESCE(p.source_bot, p.bot_id) = 'MirrorBot' {pmode_clause}
                ORDER BY p.market_id, p.side, p.opened_at DESC
            """))).fetchall()
        else:
            # ENTRY trades + confidence (lateral nearest-time prediction) + whether a matching EXIT exists
            rows = (await s.execute(text(f"""
                SELECT te.event_time::timestamp(0) AS t, te.market_id, te.side, te.token_id,
                       CAST(te.size AS numeric) AS size, CAST(te.price AS numeric) AS price,
                       ROUND(CAST(te.size AS numeric) * CAST(te.price AS numeric), 2) AS amount_usd,
                       pl.confidence, pl.predicted_prob, pl.edge,
                       EXISTS (SELECT 1 FROM trade_events ex
                               WHERE ex.bot_name='MirrorBot' AND ex.event_type='EXIT'
                                 AND ex.market_id=te.market_id AND ex.side=te.side
                                 {mode_clause.replace('te.', 'ex.')}) AS has_exit,
                       1 AS entry_recs
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
        t, mkt, side, tok, size, price, amt, conf, pred, edge, has_exit, entry_recs = r
        oc = await outcome_for(mkt, side, has_exit)
        onchain = None
        if args.source == "positions" and tok:
            try:
                onchain = await check_ctf_balance(tok)
            except Exception:
                onchain = None
        row = {
            "time": str(t), "market_id": mkt, "side": side,
            "size": f"{float(size):.3f}" if size is not None else "",
            "price": f"{float(price):.3f}" if price is not None else "",
            "amount_usd": f"{float(amt):.2f}" if amt is not None else "",
            "confidence": f"{float(conf):.3f}" if conf is not None else "",
            "predicted_prob": f"{float(pred):.3f}" if pred is not None else "",
            "edge": f"{float(edge):.3f}" if edge is not None else "",
            "outcome": oc,
        }
        if args.source == "positions":
            row["onchain_tokens"] = f"{onchain:.3f}" if onchain is not None else ""
            row["record"] = "recorded" if (entry_recs or 0) > 0 else "phantom"
        out.append(row)

    # sort
    if args.sort != "time":
        keymap = {"confidence": "confidence", "edge": "edge", "amount": "amount_usd"}
        col = keymap[args.sort]
        out.sort(key=lambda o: float(o[col]) if o[col] not in (None, "") else -1.0, reverse=True)

    # table
    print(f"\n=== MirrorBot review (source={args.source}, {args.mode}, sort={args.sort}) — {len(out)} wagers ===")
    extra_h = f"{'rec':>8} {'chain':>6}" if args.source == "positions" else ""
    print(f"  {'time':19} {'side':4} {'amt$':>5} {'conf':>5} {'pred':>5} {'edge':>5}  {'outcome':7} {extra_h}")
    tally = {}
    conf_present = 0
    for o in out:
        tally[o["outcome"]] = tally.get(o["outcome"], 0) + 1
        if o["confidence"]:
            conf_present += 1
        extra = f"{o.get('record',''):>8} {o.get('onchain_tokens',''):>6}" if args.source == "positions" else ""
        print(f"  {o['time']:19} {o['side']:4} {o['amount_usd'] or '  -  ':>5} "
              f"{o['confidence'] or '  -  ':>5} {o['predicted_prob'] or '  -  ':>5} "
              f"{o['edge'] or '  -  ':>5}  {o['outcome']:7} {extra}")
    print(f"\n  outcomes: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print(f"  wagers with a confidence value: {conf_present}/{len(out)}")
    if args.source == "positions":
        rec = sum(1 for o in out if o.get("record") == "recorded")
        print(f"  record coverage: recorded={rec}  phantom(no ENTRY trade_event)={len(out)-rec}")

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
