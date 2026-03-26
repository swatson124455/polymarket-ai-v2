#!/usr/bin/env python3
"""WeatherBot actual P&L charts from trade_events (the P&L authority).

Generates charts showing real performance — no prediction_log, no was_correct.
Only realized_pnl from RESOLUTION and EXIT events.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
from datetime import date


async def fetch_data():
    import asyncpg
    conn = await asyncpg.connect(database='polymarket', user='postgres')

    # All realized P&L events
    rows = await conn.fetch("""
        SELECT event_time::date as day, event_type, side, realized_pnl, size, price,
               (event_data->>'edge')::float as edge,
               (event_data->>'lead_time_hours')::float as lead_h,
               (event_data->>'city')::text as city,
               (event_data->>'entry_price')::float as entry_price
        FROM trade_events
        WHERE bot_name = 'WeatherBot' AND event_type IN ('RESOLUTION', 'EXIT')
        ORDER BY event_time
    """)

    # Entry events for trade count
    entries = await conn.fetch("""
        SELECT event_time::date as day, side, size, price,
               (event_data->>'edge')::float as edge,
               (event_data->>'lead_time_hours')::float as lead_h,
               (event_data->>'city')::text as city
        FROM trade_events
        WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
        ORDER BY event_time
    """)

    # Current open positions
    positions = await conn.fetch("""
        SELECT market_id, side, entry_price, size, entry_cost, unrealized_pnl, current_price
        FROM positions
        WHERE bot_id = 'WeatherBot' AND status = 'open'
    """)

    await conn.close()
    return rows, entries, positions


def print_bar(label, value, max_val, width=40, char='█'):
    """Print a horizontal bar chart line."""
    if max_val == 0:
        bar_len = 0
    else:
        bar_len = int(abs(value) / max_val * width)
    if value >= 0:
        bar = char * bar_len
        print(f"  {label:>12s} │ {bar:<{width}s} ${value:>+9.2f}")
    else:
        pad = width - bar_len
        bar = ' ' * pad + '░' * bar_len
        print(f"  {label:>12s} │ {bar:<{width}s} ${value:>+9.2f}")


def main():
    rows, entries, positions = asyncio.run(fetch_data())

    print("=" * 72)
    print("  WEATHERBOT ACTUAL P&L — FROM trade_events (AUTHORITATIVE)")
    print("=" * 72)

    # ═══════════════════════════════════════════════════════════
    # CHART 1: Daily P&L waterfall
    # ═══════════════════════════════════════════════════════════
    daily_pnl = defaultdict(float)
    for r in rows:
        daily_pnl[r['day']] += float(r['realized_pnl'])

    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 1: DAILY REALIZED P&L                                   │")
    print("├─────────────────────────────────────────────────────────────────┤")

    max_daily = max(abs(v) for v in daily_pnl.values()) if daily_pnl else 1
    cumulative = 0.0
    for day in sorted(daily_pnl):
        pnl = daily_pnl[day]
        cumulative += pnl
        print_bar(day.strftime('%m-%d'), pnl, max_daily)

    print(f"  {'─' * 12}─┼{'─' * 51}")
    print(f"  {'CUMULATIVE':>12s} │ {'':40s} ${cumulative:>+9.2f}")
    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # CHART 2: Cumulative equity curve
    # ═══════════════════════════════════════════════════════════
    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 2: CUMULATIVE EQUITY CURVE                              │")
    print("├─────────────────────────────────────────────────────────────────┤")

    cum = 0.0
    max_cum = 0.0
    max_dd = 0.0
    equity_by_day = []
    for day in sorted(daily_pnl):
        cum += daily_pnl[day]
        equity_by_day.append((day, cum))
        if cum > max_cum:
            max_cum = cum
        dd = max_cum - cum
        if dd > max_dd:
            max_dd = dd

    if equity_by_day:
        max_eq = max(abs(e[1]) for e in equity_by_day) if equity_by_day else 1
        for day, eq in equity_by_day:
            bar_len = int(eq / max(max_eq, 1) * 40) if eq > 0 else 0
            bar = '▓' * bar_len
            print(f"  {day.strftime('%m-%d'):>12s} │ {bar:<40s} ${eq:>+9.2f}")

    print(f"  {'─' * 12}─┼{'─' * 51}")
    print(f"  {'Peak':>12s} │ {'':40s} ${max_cum:>+9.2f}")
    print(f"  {'Max DD':>12s} │ {'':40s} ${-max_dd:>+9.2f}")
    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # CHART 3: P&L by side (RESOLUTION only — actual token payouts)
    # ═══════════════════════════════════════════════════════════
    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 3: RESOLUTION P&L BY SIDE (actual token payouts)        │")
    print("├─────────────────────────────────────────────────────────────────┤")

    side_stats = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0, 'sizes': []})
    for r in rows:
        if r['event_type'] != 'RESOLUTION':
            continue
        pnl = float(r['realized_pnl'])
        side = r['side']
        side_stats[side]['n'] += 1
        side_stats[side]['pnl'] += pnl
        side_stats[side]['sizes'].append(float(r['size']) * float(r['price']))
        if pnl > 0:
            side_stats[side]['wins'] += 1

    for side in ['YES', 'NO']:
        s = side_stats.get(side, {'n': 0, 'wins': 0, 'pnl': 0.0, 'sizes': []})
        if s['n'] == 0:
            continue
        wr = s['wins'] / s['n'] * 100
        avg_size = sum(s['sizes']) / len(s['sizes']) if s['sizes'] else 0
        print(f"  {side:>12s} │ N={s['n']:>4d}  WR={wr:>5.1f}%  AvgSize=${avg_size:>6.2f}  PnL=${s['pnl']:>+9.2f}")

    total_res = sum(s['pnl'] for s in side_stats.values())
    total_n = sum(s['n'] for s in side_stats.values())
    total_w = sum(s['wins'] for s in side_stats.values())
    if total_n:
        print(f"  {'TOTAL':>12s} │ N={total_n:>4d}  WR={total_w/total_n*100:>5.1f}%  {'':18s}  PnL=${total_res:>+9.2f}")
    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # CHART 4: P&L by city (top 10)
    # ═══════════════════════════════════════════════════════════
    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 4: P&L BY CITY (top winners & losers)                   │")
    print("├─────────────────────────────────────────────────────────────────┤")

    city_pnl = defaultdict(lambda: {'pnl': 0.0, 'n': 0, 'wins': 0})
    for r in rows:
        city = r['city'] or 'Unknown'
        pnl = float(r['realized_pnl'])
        city_pnl[city]['pnl'] += pnl
        city_pnl[city]['n'] += 1
        if pnl > 0:
            city_pnl[city]['wins'] += 1

    sorted_cities = sorted(city_pnl.items(), key=lambda x: x[1]['pnl'], reverse=True)
    max_city_pnl = max(abs(c[1]['pnl']) for c in sorted_cities) if sorted_cities else 1

    # Top 8 winners
    for city, stats in sorted_cities[:8]:
        wr = stats['wins'] / stats['n'] * 100 if stats['n'] else 0
        print_bar(city[:12], stats['pnl'], max_city_pnl)
        print(f"  {'':>12s} │ {'':40s} N={stats['n']:>3d} WR={wr:.0f}%")

    if len(sorted_cities) > 8:
        print(f"  {'--- GAP ---':>12s} │")
        # Bottom 5 losers
        for city, stats in sorted_cities[-5:]:
            wr = stats['wins'] / stats['n'] * 100 if stats['n'] else 0
            print_bar(city[:12], stats['pnl'], max_city_pnl)
            print(f"  {'':>12s} │ {'':40s} N={stats['n']:>3d} WR={wr:.0f}%")

    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # CHART 5: P&L by edge bucket (THE edge cap question)
    # ═══════════════════════════════════════════════════════════
    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 5: RESOLUTION P&L BY ENTRY EDGE SIZE                    │")
    print("├─────────────────────────────────────────────────────────────────┤")

    edge_buckets = defaultdict(lambda: {'pnl': 0.0, 'n': 0, 'wins': 0})
    for r in rows:
        if r['event_type'] != 'RESOLUTION':
            continue
        edge = r['edge']
        if edge is None:
            bucket = 'no_edge'
        elif abs(edge) < 0.10:
            bucket = '< 0.10'
        elif abs(edge) < 0.15:
            bucket = '0.10-0.15'
        elif abs(edge) < 0.20:
            bucket = '0.15-0.20'
        elif abs(edge) < 0.30:
            bucket = '0.20-0.30'
        elif abs(edge) < 0.50:
            bucket = '0.30-0.50'
        else:
            bucket = '0.50+'

        pnl = float(r['realized_pnl'])
        edge_buckets[bucket]['pnl'] += pnl
        edge_buckets[bucket]['n'] += 1
        if pnl > 0:
            edge_buckets[bucket]['wins'] += 1

    bucket_order = ['< 0.10', '0.10-0.15', '0.15-0.20', '0.20-0.30', '0.30-0.50', '0.50+', 'no_edge']
    max_edge_pnl = max(abs(b['pnl']) for b in edge_buckets.values()) if edge_buckets else 1

    for bucket in bucket_order:
        if bucket not in edge_buckets:
            continue
        stats = edge_buckets[bucket]
        wr = stats['wins'] / stats['n'] * 100 if stats['n'] else 0
        print_bar(bucket, stats['pnl'], max_edge_pnl)
        print(f"  {'':>12s} │ {'':40s} N={stats['n']:>3d} WR={wr:.0f}%")

    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # CHART 6: P&L by week + side
    # ═══════════════════════════════════════════════════════════
    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 6: WEEKLY P&L BY SIDE                                   │")
    print("├─────────────────────────────────────────────────────────────────┤")

    weekly = defaultdict(lambda: {'YES_pnl': 0.0, 'NO_pnl': 0.0, 'YES_n': 0, 'NO_n': 0,
                                   'YES_w': 0, 'NO_w': 0})
    for r in rows:
        if r['event_type'] != 'RESOLUTION':
            continue
        # Get Monday of that week
        d = r['day']
        monday = d.toordinal() - d.weekday()
        week_start = date.fromordinal(monday)
        side = r['side']
        pnl = float(r['realized_pnl'])
        weekly[week_start][f'{side}_pnl'] += pnl
        weekly[week_start][f'{side}_n'] += 1
        if pnl > 0:
            weekly[week_start][f'{side}_w'] += 1

    print(f"  {'Week':>12s} │ {'YES P&L':>10s} {'YES WR':>8s} {'YES N':>6s} │ {'NO P&L':>10s} {'NO WR':>8s} {'NO N':>6s} │ {'Total':>10s}")
    print(f"  {'─'*12}─┼{'─'*27}┼{'─'*27}┼{'─'*10}")
    for week in sorted(weekly):
        w = weekly[week]
        yes_wr = f"{w['YES_w']/w['YES_n']*100:.0f}%" if w['YES_n'] else '-'
        no_wr = f"{w['NO_w']/w['NO_n']*100:.0f}%" if w['NO_n'] else '-'
        total = w['YES_pnl'] + w['NO_pnl']
        print(f"  {week.strftime('%m-%d'):>12s} │ ${w['YES_pnl']:>+8.2f} {yes_wr:>8s} {w['YES_n']:>6d} │ ${w['NO_pnl']:>+8.2f} {no_wr:>8s} {w['NO_n']:>6d} │ ${total:>+8.2f}")

    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # CHART 7: Trade volume (entries per day)
    # ═══════════════════════════════════════════════════════════
    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  CHART 7: DAILY TRADE ENTRIES                                  │")
    print("├─────────────────────────────────────────────────────────────────┤")

    daily_entries = defaultdict(lambda: {'n': 0, 'usd': 0.0})
    for e in entries:
        d = e['day']
        daily_entries[d]['n'] += 1
        daily_entries[d]['usd'] += float(e['size']) * float(e['price'])

    max_entries = max(d['n'] for d in daily_entries.values()) if daily_entries else 1
    for day in sorted(daily_entries):
        de = daily_entries[day]
        bar_len = int(de['n'] / max_entries * 35)
        bar = '▓' * bar_len
        print(f"  {day.strftime('%m-%d'):>12s} │ {bar:<35s} {de['n']:>4d} trades  ${de['usd']:>8.2f}")

    print("└─────────────────────────────────────────────────────────────────┘")

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 72)
    total_exit = sum(float(r['realized_pnl']) for r in rows if r['event_type'] == 'EXIT')
    total_res = sum(float(r['realized_pnl']) for r in rows if r['event_type'] == 'RESOLUTION')
    open_upnl = sum(float(p['unrealized_pnl']) for p in positions if p['unrealized_pnl'])
    open_cost = sum(float(p['entry_cost']) for p in positions if p['entry_cost'])

    print(f"  Realized (exits):       ${total_exit:>+10.2f}")
    print(f"  Realized (resolutions): ${total_res:>+10.2f}")
    print(f"  Total realized:         ${total_exit + total_res:>+10.2f}")
    print(f"  Open positions:         {len(positions):>10d}  (${open_cost:.2f} deployed)")
    print(f"  Unrealized P&L:         ${open_upnl:>+10.2f}")
    print(f"  Total (real + unreal):  ${total_exit + total_res + open_upnl:>+10.2f}")
    print(f"  Max drawdown:           ${-max_dd:>+10.2f}")
    print("=" * 72)


if __name__ == '__main__':
    main()
