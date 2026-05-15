#!/usr/bin/env python3
"""
Polymarket AI Dashboard — Read-only FastAPI backend.

Usage:
    cd ui && python app.py          # starts on port 8050
    python ui/app.py --port 8050    # explicit port

Zero footprint: SELECT-only queries against existing DB tables.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add project root so base_engine is importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from base_engine.data.database import Database

db: Database | None = None

BOTS = ["MirrorBot", "WeatherBot", "EsportsBot", "EsportsLiveBot"]
WINDOWS = [12, 6, 3, 1]
# Taker fee rate (1.5% = 150 bps) — matches settings.TAKER_FEE_BPS
TAKER_FEE_RATE = 0.015


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = Database()
    await db.init()
    yield
    if db:
        await db.close()


app = FastAPI(title="Polymarket AI Dashboard", lifespan=lifespan)

# Serve static files (index.html, etc.)
_STATIC = Path(__file__).resolve().parent / "static"
_STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(_STATIC / "index.html"))


@app.get("/api/summary")
async def api_summary():
    """System-wide + per-bot summary."""
    async with db.get_session() as s:
        bots_out = []
        for bot in BOTS:
            # positions — cost-adjusted unrealized P&L
            # Raw uPnL = (current - entry) * size (what DB stores)
            # Cost-adjusted = raw - entry_cost - est_exit_cost
            # entry_cost stored in DB; est_exit_cost = taker_fee * size * current_price
            r = await s.execute(text("""
                SELECT COUNT(*),
                       COALESCE(SUM(size * entry_price), 0),
                       COALESCE(SUM(
                           unrealized_pnl
                           - COALESCE(entry_cost, size * entry_price * :fee)
                           - (size * current_price * :fee)
                       ), 0)
                FROM positions
                WHERE (bot_id = :bot OR source_bot = :bot) AND status = 'open'
            """), {"bot": bot, "fee": TAKER_FEE_RATE})
            pos_count, deployed, upnl = r.fetchone()

            # all-time realized
            r = await s.execute(text("""
                SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0)
                FROM trade_events
                WHERE bot_name = :bot AND event_type IN ('EXIT', 'RESOLUTION')
            """), {"bot": bot})
            realized = float(r.scalar() or 0)

            # total entries
            r = await s.execute(text("""
                SELECT COUNT(*) FROM trade_events
                WHERE bot_name = :bot AND event_type = 'ENTRY'
            """), {"bot": bot})
            total_trades = int(r.scalar() or 0)

            # win/loss
            r = await s.execute(text("""
                SELECT COUNT(*) FILTER (WHERE realized_pnl > 0),
                       COUNT(*) FILTER (WHERE realized_pnl <= 0),
                       COUNT(*)
                FROM trade_events
                WHERE bot_name = :bot AND event_type IN ('EXIT','RESOLUTION')
                  AND realized_pnl IS NOT NULL
            """), {"bot": bot})
            wins, losses, resolved = r.fetchone()

            # last trade time
            r = await s.execute(text("""
                SELECT MAX(event_time) FROM trade_events WHERE bot_name = :bot
            """), {"bot": bot})
            last_trade = r.scalar()

            bots_out.append({
                "name": bot,
                "positions": int(pos_count or 0),
                "deployed": round(float(deployed or 0), 2),
                "unrealized": round(float(upnl or 0), 2),
                "realized": round(realized, 2),
                "total_trades": total_trades,
                "wins": int(wins or 0),
                "losses": int(losses or 0),
                "resolved": int(resolved or 0),
                "win_rate": round(int(wins or 0) / int(resolved or 1) * 100, 1),
                "last_trade": last_trade.isoformat() if last_trade else None,
            })

        total_realized = sum(b["realized"] for b in bots_out)
        total_unrealized = sum(b["unrealized"] for b in bots_out)
        total_deployed = sum(b["deployed"] for b in bots_out)
        total_positions = sum(b["positions"] for b in bots_out)

        return {
            "bots": bots_out,
            "system": {
                "capital": 20000,
                "realized": round(total_realized, 2),
                "unrealized": round(total_unrealized, 2),
                "equity": round(20000 + total_realized + total_unrealized, 2),
                "deployed": round(total_deployed, 2),
                "positions": total_positions,
                "exposure_pct": round(total_deployed / 20000 * 100, 1),
            },
        }


@app.get("/api/pnl_windows")
async def api_pnl_windows():
    """P&L breakdown by time window: 12h, 6h, 3h, 1h."""
    async with db.get_session() as s:
        result = {}
        for bot in BOTS:
            bot_windows = {}
            for w in WINDOWS:
                r = await s.execute(text("""
                    SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0),
                           COUNT(*) FILTER (WHERE realized_pnl > 0),
                           COUNT(*) FILTER (WHERE realized_pnl <= 0),
                           COUNT(*) FILTER (WHERE event_type = 'ENTRY')
                    FROM trade_events
                    WHERE bot_name = :bot
                      AND event_time > NOW() - INTERVAL '1 hour' * :hours
                      AND event_time <= NOW()
                """), {"bot": bot, "hours": w})
                rpnl, wins, losses, entries = r.fetchone()
                bot_windows[str(w)] = {
                    "realized": round(float(rpnl or 0), 2),
                    "wins": int(wins or 0),
                    "losses": int(losses or 0),
                    "entries": int(entries or 0),
                }
            result[bot] = bot_windows
        return {"windows": [12, 6, 3, 1], "bots": result}


@app.get("/api/positions")
async def api_positions(bot: str | None = None, limit: int = 100):
    """Open positions, optionally filtered by bot."""
    async with db.get_session() as s:
        clause = "AND (p.bot_id = :bot OR p.source_bot = :bot)" if bot else ""
        params = {"bot": bot, "lim": limit} if bot else {"lim": limit}
        r = await s.execute(text(f"""
            SELECT p.bot_id, p.market_id, p.side, p.size, p.entry_price,
                   p.current_price,
                   p.unrealized_pnl
                     - COALESCE(p.entry_cost, p.size * p.entry_price * :fee)
                     - (p.size * p.current_price * :fee)
                     AS cost_adj_upnl,
                   p.opened_at,
                   m.question, m.category,
                   p.unrealized_pnl AS raw_upnl,
                   COALESCE(p.entry_cost, p.size * p.entry_price * :fee) AS entry_cost_val,
                   p.breakeven_price
            FROM positions p
            LEFT JOIN markets m ON m.id = p.market_id
            WHERE p.status = 'open' {clause}
            ORDER BY ABS(p.unrealized_pnl
                     - COALESCE(p.entry_cost, p.size * p.entry_price * :fee)
                     - (p.size * p.current_price * :fee)) DESC
            LIMIT :lim
        """), {**params, "fee": TAKER_FEE_RATE})
        rows = r.fetchall()
        return {
            "positions": [
                {
                    "bot": r[0], "market_id": r[1], "side": r[2],
                    "size": round(float(r[3] or 0), 2),
                    "entry_price": round(float(r[4] or 0), 4),
                    "current_price": round(float(r[5] or 0), 4),
                    "unrealized_pnl": round(float(r[6] or 0), 2),
                    "opened_at": r[7].isoformat() if r[7] else None,
                    "question": r[8], "category": r[9],
                    "raw_upnl": round(float(r[10] or 0), 2),
                    "entry_cost": round(float(r[11] or 0), 2),
                    "breakeven_price": round(float(r[12] or 0), 4) if r[12] else None,
                }
                for r in rows
            ]
        }


@app.get("/api/trades")
async def api_trades(bot: str | None = None, hours: int = 24, limit: int = 50):
    """Recent trade events."""
    async with db.get_session() as s:
        clause = "AND te.bot_name = :bot" if bot else ""
        params = {"bot": bot, "hours": hours, "lim": limit} if bot else {"hours": hours, "lim": limit}
        r = await s.execute(text(f"""
            SELECT te.event_type, te.bot_name, te.market_id, te.side,
                   te.size, te.price, te.realized_pnl, te.event_time,
                   te.confidence, m.question
            FROM trade_events te
            LEFT JOIN markets m ON m.id = te.market_id
            WHERE te.event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
              AND te.event_time > NOW() - INTERVAL '1 hour' * :hours
              AND te.event_time <= NOW()
              {clause}
            ORDER BY te.event_time DESC
            LIMIT :lim
        """), params)
        rows = r.fetchall()
        return {
            "trades": [
                {
                    "type": r[0], "bot": r[1], "market_id": r[2],
                    "side": r[3],
                    "size": round(float(r[4] or 0), 2),
                    "price": round(float(r[5] or 0), 4),
                    "realized_pnl": round(float(r[6] or 0), 2) if r[6] is not None else None,
                    "time": r[7].isoformat() if r[7] else None,
                    "confidence": round(float(r[8] or 0), 3) if r[8] else None,
                    "question": r[9],
                }
                for r in rows
            ]
        }


@app.get("/api/risk")
async def api_risk():
    """Exposure breakdown by bot and category."""
    async with db.get_session() as s:
        # by bot (deployed = entry_cost, which includes slippage)
        r = await s.execute(text("""
            SELECT p.bot_id, COUNT(*),
                   SUM(p.size * p.entry_price) AS notional,
                   SUM(COALESCE(p.entry_cost, p.size * p.entry_price * :fee)) AS total_entry_cost,
                   SUM(p.unrealized_pnl
                     - COALESCE(p.entry_cost, p.size * p.entry_price * :fee)
                     - (p.size * p.current_price * :fee)) AS cost_adj_upnl
            FROM positions p WHERE p.status = 'open'
            GROUP BY p.bot_id ORDER BY 3 DESC
        """), {"fee": TAKER_FEE_RATE})
        by_bot = [
            {"bot": r[0], "count": int(r[1]),
             "exposure": round(float(r[2] or 0), 2),
             "entry_costs": round(float(r[3] or 0), 2),
             "cost_adj_upnl": round(float(r[4] or 0), 2)}
            for r in r.fetchall()
        ]

        # by category
        r = await s.execute(text("""
            SELECT COALESCE(m.category, 'unknown'), COUNT(*), SUM(p.size * p.entry_price)
            FROM positions p
            LEFT JOIN markets m ON m.id = p.market_id
            WHERE p.status = 'open'
            GROUP BY 1 ORDER BY 3 DESC
        """))
        by_cat = [{"category": r[0], "count": int(r[1]), "exposure": round(float(r[2] or 0), 2)} for r in r.fetchall()]

        # daily counters
        r = await s.execute(text("""
            SELECT bot_id, counter_name, counter_value
            FROM daily_counters WHERE counter_date = CURRENT_DATE
            ORDER BY bot_id, counter_name
        """))
        counters = [{"bot": r[0], "counter": r[1], "value": round(float(r[2] or 0), 2)} for r in r.fetchall()]

        # top concentrated markets
        r = await s.execute(text("""
            SELECT p.market_id, m.question, COUNT(DISTINCT p.bot_id) AS bots,
                   SUM(p.size * p.entry_price) AS total_exp
            FROM positions p
            LEFT JOIN markets m ON m.id = p.market_id
            WHERE p.status = 'open'
            GROUP BY p.market_id, m.question
            ORDER BY total_exp DESC LIMIT 10
        """))
        concentrated = [
            {"market_id": r[0], "question": r[1], "bots": int(r[2]),
             "exposure": round(float(r[3] or 0), 2)}
            for r in r.fetchall()
        ]

        return {"by_bot": by_bot, "by_category": by_cat, "daily_counters": counters, "concentrated": concentrated}


@app.get("/api/equity")
async def api_equity(days: int = 30):
    """Equity curve from snapshots."""
    async with db.get_session() as s:
        r = await s.execute(text("""
            SELECT snapshot_date, bot_name, total_equity, realized_pnl,
                   unrealized_pnl, open_positions, daily_trades, win_count,
                   loss_count, drawdown_pct, rolling_sharpe
            FROM equity_snapshots
            WHERE snapshot_date >= CURRENT_DATE - :days
            ORDER BY snapshot_date, bot_name
        """), {"days": days})
        rows = r.fetchall()
        return {
            "snapshots": [
                {
                    "date": str(r[0]), "bot": r[1],
                    "equity": round(float(r[2] or 0), 2),
                    "realized": round(float(r[3] or 0), 2),
                    "unrealized": round(float(r[4] or 0), 2),
                    "positions": int(r[5] or 0),
                    "trades": int(r[6] or 0),
                    "wins": int(r[7] or 0),
                    "losses": int(r[8] or 0),
                    "drawdown": round(float(r[9] or 0), 4),
                    "sharpe": round(float(r[10] or 0), 3) if r[10] else None,
                }
                for r in rows
            ]
        }


@app.get("/api/snapshot_compare")
async def api_snapshot_compare(start: str, end: str):
    """Compare two dates. Pass YYYY-MM-DD strings."""
    async with db.get_session() as s:
        result = {}
        for d in [start, end]:
            r = await s.execute(text("""
                SELECT bot_name, total_equity, realized_pnl, unrealized_pnl,
                       deployed_capital, open_positions, drawdown_pct, rolling_sharpe
                FROM equity_snapshots WHERE snapshot_date = :d
            """), {"d": d})
            rows = r.fetchall()
            result[d] = {
                r[0]: {
                    "equity": round(float(r[1] or 0), 2),
                    "realized": round(float(r[2] or 0), 2),
                    "unrealized": round(float(r[3] or 0), 2),
                    "deployed": round(float(r[4] or 0), 2),
                    "positions": int(r[5] or 0),
                    "drawdown": round(float(r[6] or 0), 4),
                    "sharpe": round(float(r[7] or 0), 3) if r[7] else None,
                }
                for r in rows
            }

        # trades in the period
        r = await s.execute(text("""
            SELECT bot_name,
                   COUNT(*) AS trades,
                   COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
                   COUNT(*) FILTER (WHERE realized_pnl <= 0) AS losses,
                   COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0) AS net
            FROM trade_events
            WHERE event_type IN ('EXIT', 'RESOLUTION')
              AND event_time::date BETWEEN :s AND :e
            GROUP BY bot_name
        """), {"s": start, "e": end})
        period_trades = {
            r[0]: {"trades": int(r[1]), "wins": int(r[2]), "losses": int(r[3]), "net": round(float(r[4]), 2)}
            for r in r.fetchall()
        }

        return {"start": start, "end": end, "snapshots": result, "period_trades": period_trades}


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"\n  Dashboard: http://localhost:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
