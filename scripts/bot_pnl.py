#!/usr/bin/env python3
"""
Bot P&L Report — Canonical calculation from trade_events + positions.

Usage:
    python scripts/bot_pnl.py                    # WeatherBot, last 24h
    python scripts/bot_pnl.py EsportsBot         # Specific bot, last 24h
    python scripts/bot_pnl.py WeatherBot 8       # Specific bot, last 8h

EXIT side transition (S163, deployed 2026-04-08):
  - Before 2026-04-08T16:01:40Z: EXIT events have side='SELL' (hardcoded)
  - After  2026-04-08T16:01:40Z: EXIT events have side='YES' or 'NO' (token-outcome)
  - Integrity checks should NOT group by side for EXIT events. Use event_type only.

P&L math rules (uniform for YES and NO):
  - entry_price and current_price are ALWAYS token-specific prices
  - cost_basis = entry_price * size (for BOTH YES and NO)
  - unrealized_pnl = (current_price - entry_price) * size (for BOTH YES and NO)
  - realized_pnl on EXIT = (exit_price - entry_price) * size - fees
  - realized_pnl on RESOLUTION = (resolution_value - entry_price) * size - fees
    where resolution_value = 1.0 if your side wins, 0.0 if it loses

NEVER invert the formula for NO positions. Prices are already side-specific.
The position_manager uses (current - entry) * size uniformly.
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def bot_pnl(bot_name: str, hours: int = 24):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # 1. Open positions — mark-to-market
        r1 = await s.execute(text("""
            SELECT p.market_id, p.side, p.size, p.entry_price, p.current_price,
                   p.unrealized_pnl, p.opened_at
            FROM positions p
            WHERE (p.bot_id = :bot OR p.source_bot = :bot)
              AND p.status = 'open'
            ORDER BY p.opened_at DESC
        """), {"bot": bot_name})
        positions = r1.fetchall()

        print(f"=== {bot_name} P&L Report (last {hours}h) ===\n")

        total_cost = 0.0
        total_upnl = 0.0
        total_mkt_value = 0.0
        print(f"OPEN POSITIONS ({len(positions)}):")
        print(f"{'Market':<14} {'Side':>4} {'Shares':>8} {'Entry':>7} {'Curr':>7} {'Cost':>9} {'Value':>9} {'uPnL':>9}")
        print("-" * 80)
        for p in positions:
            mid = p[0][:12] + ".."
            side = p[1]
            sz = float(p[2] or 0)
            entry = float(p[3] or 0)
            cur = float(p[4] or 0)
            # UNIFORM formula — same for YES and NO (prices are token-specific)
            cost = entry * sz
            mkt_val = cur * sz
            upnl = float(p[5]) if p[5] is not None else (cur - entry) * sz
            total_cost += cost
            total_mkt_value += mkt_val
            total_upnl += upnl
            print(f"{mid:<14} {side:>4} {sz:>8.1f} {entry:>7.4f} {cur:>7.4f} ${cost:>8.2f} ${mkt_val:>8.2f} ${upnl:>+8.2f}")
        print("-" * 80)
        print(f"{'TOTAL':<14} {'':>4} {'':>8} {'':>7} {'':>7} ${total_cost:>8.2f} ${total_mkt_value:>8.2f} ${total_upnl:>+8.2f}")

        # 2. Trade events in window
        r2 = await s.execute(text("""
            SELECT event_type, market_id, side, size, price, fees,
                   realized_pnl, event_time, correlation_id
            FROM trade_events
            WHERE bot_name = :bot
              AND event_time > NOW() - INTERVAL '1 hour' * :hours
            ORDER BY event_time DESC
        """), {"bot": bot_name, "hours": hours})
        events = r2.fetchall()

        entries = [e for e in events if e[0] == 'ENTRY']
        exits = [e for e in events if e[0] == 'EXIT']
        resolutions = [e for e in events if e[0] == 'RESOLUTION']

        print(f"\nTRADE EVENTS (last {hours}h):")
        print(f"  Entries: {len(entries)}")
        for e in entries:
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. {e[2]} sz={float(e[3] or 0):.1f} @ {float(e[4] or 0):.4f} fee=${float(e[5] or 0):.2f}")

        realized_exit = 0.0
        print(f"  Exits: {len(exits)}")
        for e in exits:
            rpnl = float(e[6] or 0)
            realized_exit += rpnl
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. sz={float(e[3] or 0):.1f} @ {float(e[4] or 0):.4f} pnl=${rpnl:+.2f}")

        realized_res = 0.0
        print(f"  Resolutions: {len(resolutions)}")
        for e in resolutions:
            rpnl = float(e[6] or 0)
            realized_res += rpnl
            print(f"    {e[7].strftime('%H:%M')} {e[1][:12]}.. pnl=${rpnl:+.2f}")

        # 3. All-time from trade_events
        r3 = await s.execute(text("""
            SELECT event_type,
                   COUNT(*),
                   COALESCE(SUM(CAST(realized_pnl AS DOUBLE PRECISION)), 0),
                   COALESCE(SUM(CAST(fees AS DOUBLE PRECISION)), 0)
            FROM trade_events
            WHERE bot_name = :bot
            GROUP BY event_type
            ORDER BY event_type
        """), {"bot": bot_name})
        stats = r3.fetchall()
        print(f"\nALL-TIME TRADE EVENTS:")
        total_realized = 0.0
        total_fees = 0.0
        for st in stats:
            rpnl = float(st[2])
            fees = float(st[3])
            total_realized += rpnl
            total_fees += fees
            print(f"  {st[0]:<12} count={st[1]:<5} realized=${rpnl:>+10.2f}  fees=${fees:>8.2f}")
        print(f"  {'TOTAL':<12} {'':5} realized=${total_realized:>+10.2f}  fees=${total_fees:>8.2f}")

        # 4. Data integrity check — detect impossible states (S120 guardrail)
        # S163: Group by market_id only (not side). Historical EXIT events used
        # side='SELL' while ENTRYs used YES/NO, causing false positives on per-side
        # matching. event_type is the correct discriminator, not side.
        r4 = await s.execute(text("""
            SELECT market_id,
                   SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS entry_sz,
                   SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS exit_sz,
                   SUM(CASE WHEN event_type = 'RESOLUTION' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS res_sz
            FROM trade_events
            WHERE bot_name = :bot
            GROUP BY market_id
            HAVING SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                 > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
        """), {"bot": bot_name})
        violations = r4.fetchall()
        if violations:
            print(f"\n{'!'*50}")
            print(f"DATA INTEGRITY WARNINGS ({len(violations)}):")
            print(f"{'!'*50}")
            for v in violations:
                mid = v[0][:14] + ".." if len(v[0]) > 14 else v[0]
                print(f"  {mid}: entry={float(v[1]):.1f} exit={float(v[2]):.1f} res={float(v[3]):.1f} "
                      f"(disposal {float(v[2]) + float(v[3]):.1f} > entry {float(v[1]):.1f})")
            print(f"{'!'*50}")

        # Summary
        print(f"\n{'='*50}")
        print(f"SUMMARY")
        print(f"{'='*50}")
        print(f"  Open positions:     {len(positions)}")
        print(f"  Total cost basis:   ${total_cost:.2f}")
        print(f"  Total mkt value:    ${total_mkt_value:.2f}")
        print(f"  Unrealized P&L:     ${total_upnl:+.2f}")
        print(f"  Realized (exits):   ${realized_exit:+.2f}  (last {hours}h)")
        print(f"  Realized (resol):   ${realized_res:+.2f}  (last {hours}h)")
        print(f"  All-time realized:  ${total_realized:+.2f}")
        print(f"  Net P&L (window):   ${total_upnl + realized_exit + realized_res:+.2f}")

        # 5. WeatherBot dimensional breakdowns (S159)
        if bot_name == "WeatherBot":
            # Per-side breakdown: JOIN resolution/exit to their ENTRY for side.
            # WB-16: DISTINCT ON picks latest ENTRY side — if market re-entered on
            # opposite side, earlier exits misattributed. Rare for WeatherBot.
            r5 = await s.execute(text("""
                SELECT e_entry.side,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, side
                    FROM trade_events
                    WHERE bot_name = :bot AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = :bot
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                GROUP BY e_entry.side
                ORDER BY e_entry.side
            """), {"bot": bot_name})
            side_rows = r5.fetchall()
            if side_rows:
                print(f"\n{'='*50}")
                print(f"PER-SIDE BREAKDOWN (all-time, resolution+exit)")
                print(f"{'='*50}")
                print(f"  {'Side':<6} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*40}")
                for sr in side_rows:
                    wr = (float(sr[2]) / float(sr[1]) * 100) if sr[1] > 0 else 0
                    print(f"  {sr[0]:<6} {sr[1]:>6} {sr[2]:>6} {wr:>6.1f}% ${float(sr[3]):>+11.2f}")

            # Per-city breakdown
            r6 = await s.execute(text("""
                SELECT e_entry.event_data->>'city' AS city,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, event_data
                    FROM trade_events
                    WHERE bot_name = :bot AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = :bot
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.event_data->>'city' IS NOT NULL
                GROUP BY e_entry.event_data->>'city'
                ORDER BY total_pnl DESC
            """), {"bot": bot_name})
            city_rows = r6.fetchall()
            if city_rows:
                print(f"\n{'='*50}")
                print(f"PER-CITY BREAKDOWN (all-time, resolution+exit)")
                print(f"{'='*50}")
                print(f"  {'City':<20} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*55}")
                for cr in city_rows:
                    wr = (float(cr[2]) / float(cr[1]) * 100) if cr[1] > 0 else 0
                    print(f"  {(cr[0] or 'unknown'):<20} {cr[1]:>6} {cr[2]:>6} {wr:>6.1f}% ${float(cr[3]):>+11.2f}")

            # Per-lead-time bucket
            r7 = await s.execute(text("""
                SELECT CASE
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 24 THEN '<24h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 48 THEN '24-48h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 72 THEN '48-72h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 120 THEN '72-120h'
                         ELSE '>=120h'
                       END AS bucket,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, event_data
                    FROM trade_events
                    WHERE bot_name = :bot AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = :bot
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.event_data->>'lead_time_hours' IS NOT NULL
                GROUP BY bucket
                ORDER BY MIN((e_entry.event_data->>'lead_time_hours')::float)
            """), {"bot": bot_name})
            lt_rows = r7.fetchall()
            if lt_rows:
                print(f"\n{'='*50}")
                print(f"PER-LEAD-TIME BREAKDOWN (all-time, resolution+exit)")
                print(f"{'='*50}")
                print(f"  {'Bucket':<10} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*45}")
                for lr in lt_rows:
                    wr = (float(lr[2]) / float(lr[1]) * 100) if lr[1] > 0 else 0
                    print(f"  {lr[0]:<10} {lr[1]:>6} {lr[2]:>6} {wr:>6.1f}% ${float(lr[3]):>+11.2f}")

            # S162: Side x Lead-time cross-tabulation
            r9 = await s.execute(text("""
                SELECT e_entry.side,
                       CASE
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 24 THEN '<24h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 48 THEN '24-48h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 72 THEN '48-72h'
                         WHEN (e_entry.event_data->>'lead_time_hours')::float < 120 THEN '72-120h'
                         ELSE '>=120h'
                       END AS bucket,
                       COUNT(*) AS cnt,
                       SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(CAST(r.realized_pnl AS DOUBLE PRECISION)), 0) AS total_pnl
                FROM trade_events r
                JOIN (
                    SELECT DISTINCT ON (market_id) market_id, side, event_data
                    FROM trade_events
                    WHERE bot_name = :bot AND event_type = 'ENTRY'
                    ORDER BY market_id, event_time DESC
                ) e_entry ON e_entry.market_id = r.market_id
                WHERE r.bot_name = :bot
                  AND r.event_type IN ('RESOLUTION', 'EXIT')
                  AND r.realized_pnl IS NOT NULL
                  AND e_entry.event_data->>'lead_time_hours' IS NOT NULL
                GROUP BY e_entry.side, bucket
                ORDER BY e_entry.side, MIN((e_entry.event_data->>'lead_time_hours')::float)
            """), {"bot": bot_name})
            xt_rows = r9.fetchall()
            if xt_rows:
                print(f"\n{'='*60}")
                print(f"SIDE x LEAD-TIME CROSS-TAB (all-time, resolution+exit)")
                print(f"{'='*60}")
                print(f"  {'Side':<5} {'Bucket':<10} {'Count':>6} {'Wins':>6} {'WR%':>7} {'P&L':>12}")
                print(f"  {'-'*50}")
                for xr in xt_rows:
                    wr = (float(xr[3]) / float(xr[2]) * 100) if xr[2] > 0 else 0
                    print(f"  {xr[0]:<5} {xr[1]:<10} {xr[2]:>6} {xr[3]:>6} {wr:>6.1f}% ${float(xr[4]):>+11.2f}")

            # S159: Calibrator status from system_kv
            r8 = await s.execute(text(
                "SELECT value FROM system_kv WHERE key = 'weatherbot_cal_fit_history'"
            ))
            _cal_row = r8.scalar_one_or_none()
            if _cal_row:
                import json as _json
                _cal_hist = _json.loads(_cal_row)
                if _cal_hist:
                    _latest = _cal_hist[-1]
                    print(f"\n{'='*50}")
                    print(f"CALIBRATOR STATUS (latest fit)")
                    print(f"{'='*50}")
                    print(f"  n_no={_latest.get('n_no','?')}  n_yes={_latest.get('n_yes','?')}  "
                          f"holdout={'valid' if _latest.get('holdout_valid') else 'invalid'}  "
                          f"yes_widened={_latest.get('yes_widened', False)}")
                    _tb = _latest.get('train_brier')
                    _ob = _latest.get('oos_brier')
                    _rob = _latest.get('raw_oos_brier')
                    print(f"  train_brier={_tb}  oos_brier={_ob}  raw_oos_brier={_rob}")
                    print(f"  fitted={_latest.get('fitted')}  ts={_latest.get('ts','?')}")

    await db.close()


if __name__ == "__main__":
    bot = sys.argv[1] if len(sys.argv) > 1 else "WeatherBot"
    hrs = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    asyncio.run(bot_pnl(bot, hrs))
