#!/usr/bin/env python3
"""
System Snapshot — Full read-only dashboard in the terminal.

Usage:
    python scripts/snapshot.py              # Full snapshot
    python scripts/snapshot.py --bot Mirror # Filter to one bot (partial name match)

Shows:
  1. System-wide totals (all bots)
  2. Per-bot card: positions, deployed, realized, unrealized
  3. P&L breakdown by time window: 12h, 6h, 3h, 1h
  4. Top 10 positions by absolute uPnL
  5. Recent trades (last 10)

Read-only. Zero footprint. Queries trade_events + positions only.
"""
import asyncio
import sys
from datetime import datetime, timezone
from base_engine.data.database import Database
from dotenv import load_dotenv

load_dotenv()

BOTS = ["MirrorBot", "WeatherBot", "EsportsBot", "EsportsLiveBot"]
WINDOWS = [12, 6, 3, 1]  # hours
# Taker fee rate (1.5% = 150 bps) — matches settings.TAKER_FEE_BPS
TAKER_FEE_RATE = 0.015


def _bar(pct: float, width: int = 20) -> str:
    filled = int(min(pct, 1.0) * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _pnl_color(val: float) -> str:
    """ANSI green for positive, red for negative, reset after."""
    if val > 0:
        return f"\033[32m${val:>+,.2f}\033[0m"
    elif val < 0:
        return f"\033[31m${val:>+,.2f}\033[0m"
    return f"${val:>+,.2f}"


async def snapshot(bot_filter: str | None = None):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        bots = BOTS
        if bot_filter:
            bots = [b for b in BOTS if bot_filter.lower() in b.lower()]
            if not bots:
                print(f"No bot matching '{bot_filter}'. Available: {', '.join(BOTS)}")
                await db.close()
                return

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        print()
        print(f"\033[1m{'=' * 72}\033[0m")
        print(f"\033[1m  POLYMARKET AI — SYSTEM SNAPSHOT  {now_str}\033[0m")
        print(f"\033[1m{'=' * 72}\033[0m")

        # ── 1. Per-bot summary ──────────────────────────────────────────
        all_realized = 0.0
        all_unrealized = 0.0
        all_positions = 0
        all_deployed = 0.0
        bot_data = {}

        for bot in bots:
            # Open positions — cost-adjusted uPnL
            # Deducts entry cost (stored or estimated) + estimated exit cost
            r = await s.execute(text("""
                SELECT COUNT(*),
                       COALESCE(SUM(size * entry_price), 0),
                       COALESCE(SUM(
                           unrealized_pnl
                           - COALESCE(entry_cost, size * entry_price * :fee)
                           - (size * current_price * :fee)
                       ), 0)
                FROM positions
                WHERE (bot_id = :bot OR source_bot = :bot)
                  AND status = 'open'
            """), {"bot": bot, "fee": TAKER_FEE_RATE})
            pos_count, deployed, upnl = r.fetchone()
            pos_count = int(pos_count or 0)
            deployed = float(deployed or 0)
            upnl = float(upnl or 0)

            # All-time realized
            r = await s.execute(text("""
                SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0)
                FROM trade_events
                WHERE bot_name = :bot
                  AND event_type IN ('EXIT', 'RESOLUTION')
            """), {"bot": bot})
            realized = float(r.scalar() or 0)

            # Total trades
            r = await s.execute(text("""
                SELECT COUNT(*)
                FROM trade_events
                WHERE bot_name = :bot AND event_type = 'ENTRY'
            """), {"bot": bot})
            total_trades = int(r.scalar() or 0)

            # Win/loss
            r = await s.execute(text("""
                SELECT COUNT(*) FILTER (WHERE realized_pnl > 0),
                       COUNT(*) FILTER (WHERE realized_pnl <= 0)
                FROM trade_events
                WHERE bot_name = :bot
                  AND event_type IN ('EXIT', 'RESOLUTION')
                  AND realized_pnl IS NOT NULL
            """), {"bot": bot})
            wins, losses = r.fetchone()
            wins = int(wins or 0)
            losses = int(losses or 0)
            wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

            bot_data[bot] = {
                "positions": pos_count, "deployed": deployed,
                "unrealized": upnl, "realized": realized,
                "trades": total_trades, "wins": wins, "losses": losses, "wr": wr,
            }

            all_realized += realized
            all_unrealized += upnl
            all_positions += pos_count
            all_deployed += deployed

        # ── System totals ───────────────────────────────────────────────
        total_equity = 20000 + all_realized + all_unrealized
        exposure_pct = all_deployed / 20000 if all_deployed else 0

        print()
        print(f"  Total Equity:  \033[1m${total_equity:>,.2f}\033[0m")
        print(f"  Realized P&L:  {_pnl_color(all_realized)}  (all-time)")
        print(f"  Unrealized:    {_pnl_color(all_unrealized)}")
        print(f"  Positions:     {all_positions}")
        print(f"  Deployed:      ${all_deployed:>,.2f} / $20,000  [{_bar(exposure_pct)}] {exposure_pct*100:.0f}%")
        print()

        # ── Bot cards ───────────────────────────────────────────────────
        print(f"\033[1m  {'Bot':<16} {'Pos':>5} {'Deployed':>10} {'Realized':>12} {'Unrealized':>12} {'W/L':>8} {'WR':>6}\033[0m")
        print(f"  {'-'*70}")
        for bot in bots:
            d = bot_data[bot]
            status = "\033[32m●\033[0m" if d["positions"] > 0 or d["trades"] > 0 else "\033[90m○\033[0m"
            print(
                f"  {status} {bot:<14} {d['positions']:>5} "
                f"${d['deployed']:>9,.2f} "
                f"{_pnl_color(d['realized']):>21} "
                f"{_pnl_color(d['unrealized']):>21} "
                f"{d['wins']:>3}/{d['losses']:<3} "
                f"{d['wr']:>5.1f}%"
            )
        print()

        # ── 2. P&L by time window ──────────────────────────────────────
        print(f"\033[1m  P&L BY TIME WINDOW\033[0m")
        print(f"  {'Bot':<16}", end="")
        for w in WINDOWS:
            print(f" {'Last ' + str(w) + 'h':>12}", end="")
        print()
        print(f"  {'-'*64}")

        window_totals = {w: 0.0 for w in WINDOWS}

        for bot in bots:
            print(f"  {bot:<16}", end="")
            for w in WINDOWS:
                r = await s.execute(text("""
                    SELECT COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0)
                    FROM trade_events
                    WHERE bot_name = :bot
                      AND event_type IN ('EXIT', 'RESOLUTION')
                      AND event_time > NOW() - INTERVAL '1 hour' * :hours
                      AND event_time <= NOW()
                """), {"bot": bot, "hours": w})
                rpnl = float(r.scalar() or 0)
                window_totals[w] += rpnl
                print(f" {_pnl_color(rpnl):>21}", end="")
            print()

        print(f"  {'-'*64}")
        print(f"  {'TOTAL':<16}", end="")
        for w in WINDOWS:
            print(f" {_pnl_color(window_totals[w]):>21}", end="")
        print()
        print()

        # ── 3. Trades by window (entry count) ──────────────────────────
        print(f"\033[1m  ENTRIES BY TIME WINDOW\033[0m")
        print(f"  {'Bot':<16}", end="")
        for w in WINDOWS:
            print(f" {'Last ' + str(w) + 'h':>12}", end="")
        print()
        print(f"  {'-'*64}")

        for bot in bots:
            print(f"  {bot:<16}", end="")
            for w in WINDOWS:
                r = await s.execute(text("""
                    SELECT COUNT(*)
                    FROM trade_events
                    WHERE bot_name = :bot
                      AND event_type = 'ENTRY'
                      AND event_time > NOW() - INTERVAL '1 hour' * :hours
                      AND event_time <= NOW()
                """), {"bot": bot, "hours": w})
                cnt = int(r.scalar() or 0)
                print(f" {cnt:>12}", end="")
            print()
        print()

        # ── 4. Top positions by |uPnL| ─────────────────────────────────
        bot_clause = "AND (p.bot_id = :bot OR p.source_bot = :bot)" if bot_filter else ""
        params = {"bot": bots[0], "fee": TAKER_FEE_RATE} if bot_filter else {"fee": TAKER_FEE_RATE}
        r = await s.execute(text(f"""
            SELECT p.bot_id, p.side, p.size, p.entry_price, p.current_price,
                   p.unrealized_pnl
                     - COALESCE(p.entry_cost, p.size * p.entry_price * :fee)
                     - (p.size * p.current_price * :fee)
                     AS cost_adj_upnl,
                   p.opened_at, m.question
            FROM positions p
            LEFT JOIN markets m ON m.id = p.market_id
            WHERE p.status = 'open' {bot_clause}
            ORDER BY ABS(p.unrealized_pnl
                     - COALESCE(p.entry_cost, p.size * p.entry_price * :fee)
                     - (p.size * p.current_price * :fee)) DESC
            LIMIT 10
        """), params)
        top_pos = r.fetchall()

        print(f"\033[1m  TOP 10 POSITIONS (by |uPnL|)\033[0m")
        print(f"  {'Bot':<14} {'Side':>4} {'Shares':>7} {'Entry':>6} {'Curr':>6} {'uPnL':>10}  Market")
        print(f"  {'-'*80}")
        for p in top_pos:
            bot_short = (p[0] or "?")[:12]
            question = (p[7] or "?")[:38]
            upnl = float(p[5] or 0)
            print(
                f"  {bot_short:<14} {p[1]:>4} {float(p[2] or 0):>7.1f} "
                f"{float(p[3] or 0):>6.3f} {float(p[4] or 0):>6.3f} "
                f"{_pnl_color(upnl):>19}  {question}"
            )
        print()

        # ── 5. Recent trades ────────────────────────────────────────────
        bot_clause_te = "AND bot_name = :bot" if bot_filter else ""
        r = await s.execute(text(f"""
            SELECT event_type, bot_name, market_id, side, size, price,
                   realized_pnl, event_time
            FROM trade_events
            WHERE event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
              {bot_clause_te}
            ORDER BY event_time DESC
            LIMIT 10
        """), params)
        recent = r.fetchall()

        print(f"\033[1m  LAST 10 TRADES\033[0m")
        print(f"  {'Time':>8} {'Type':<6} {'Bot':<14} {'Side':>4} {'Size':>7} {'Price':>6} {'PnL':>10}  Market")
        print(f"  {'-'*80}")
        for t in recent:
            etype = t[0][:5]
            bot_short = (t[1] or "?")[:12]
            mid = (t[2] or "?")[:10] + ".."
            rpnl = float(t[6] or 0)
            pnl_str = _pnl_color(rpnl) if t[6] is not None else "       —"
            time_str = t[7].strftime("%H:%M:%S") if t[7] else "??:??:??"
            print(
                f"  {time_str:>8} {etype:<6} {bot_short:<14} {(t[3] or ''):>4} "
                f"{float(t[4] or 0):>7.1f} {float(t[5] or 0):>6.3f} "
                f"{pnl_str:>19}  {mid}"
            )

        print()
        print(f"\033[1m{'=' * 72}\033[0m")
        print()

    await db.close()


if __name__ == "__main__":
    filt = None
    for arg in sys.argv[1:]:
        if arg.startswith("--bot"):
            continue
        if sys.argv.index(arg) > 0 and sys.argv[sys.argv.index(arg) - 1] == "--bot":
            filt = arg
            continue
        # positional: treat as bot filter
        filt = arg
    asyncio.run(snapshot(filt))
