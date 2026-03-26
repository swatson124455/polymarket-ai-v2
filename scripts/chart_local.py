#!/usr/bin/env python3
"""WeatherBot ACTUAL P&L charts from trade_events."""
import os, tempfile
from collections import defaultdict
from datetime import date, datetime

def main():
    # Load resolution data enriched with entry metadata
    res_path = os.path.join(tempfile.gettempdir(), 'wb_res_enriched.csv')
    pnl_path = os.path.join(tempfile.gettempdir(), 'wb_pnl_raw.csv')

    # Raw P&L (all events)
    raw = []
    with open(pnl_path) as f:
        for line in f:
            p = line.strip().split('|')
            if len(p) < 6: continue
            raw.append({
                'day': p[0], 'type': p[1], 'side': p[2],
                'pnl': float(p[3]) if p[3] else 0,
                'size': float(p[4]) if p[4] else 0,
                'price': float(p[5]) if p[5] else 0,
            })

    # Enriched resolution data (with city, lead_time from entry)
    res = []
    with open(res_path) as f:
        for line in f:
            p = line.strip().split('|')
            if len(p) < 9: continue
            res.append({
                'day': p[0], 'side': p[1],
                'pnl': float(p[2]) if p[2] else 0,
                'size': float(p[3]) if p[3] else 0,
                'res_price': float(p[4]) if p[4] else 0,
                'entry_price': float(p[5]) if p[5] else 0,
                'city': p[6] if p[6] else 'Unknown',
                'lead_h': float(p[7]) if p[7] else None,
                'mkt_type': p[8] if p[8] else 'temperature',
            })

    # Deduplicate: same market_id can have multiple entries
    # Group by (day, side, pnl, city) and take first
    seen_res = set()
    uniq_res = []
    for r in res:
        key = (r['day'], r['side'], f"{r['pnl']:.4f}", r['city'])
        if key not in seen_res:
            seen_res.add(key)
            uniq_res.append(r)

    print('=' * 72)
    print('  WEATHERBOT ACTUAL P&L — trade_events ONLY')
    print('  Every number = real money gained/lost. Zero predictions.')
    print('=' * 72)

    # ── CHART 1: Daily P&L + equity curve ──
    daily = defaultdict(float)
    for r in raw:
        daily[r['day']] += r['pnl']

    print()
    print('+' + '-' * 70 + '+')
    print('|  DAILY P&L + CUMULATIVE EQUITY                                  |')
    print('+' + '-' * 70 + '+')

    cum = 0.0; mx_cum = 0.0; mx_dd = 0.0
    mx = max(abs(v) for v in daily.values()) if daily else 1

    for day in sorted(daily):
        p = daily[day]
        cum += p
        if cum > mx_cum: mx_cum = cum
        dd = mx_cum - cum
        if dd > mx_dd: mx_dd = dd
        w = 22
        if p >= 0:
            bar = '#' * int(p / mx * w)
        else:
            bl = int(abs(p) / mx * w)
            bar = ' ' * (w - bl) + '-' * bl
        print(f'  {day}  {bar:<22s}  day=${p:>+8.2f}  cum=${cum:>+9.2f}')

    print(f'  {"PEAK":>10s}  {" ":22s}  ${mx_cum:>+19.2f}')
    print(f'  {"MAX DD":>10s}  {" ":22s}  ${-mx_dd:>+19.2f}')

    # ── CHART 2: Resolution by side ──
    print()
    print('+' + '-' * 70 + '+')
    print('|  RESOLUTION P&L BY SIDE                                         |')
    print('+' + '-' * 70 + '+')

    for side in ['NO', 'YES']:
        trades = [r for r in uniq_res if r['side'] == side]
        if not trades: continue
        n = len(trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        pnl = sum(t['pnl'] for t in trades)
        avg_cost = sum(t['entry_price'] * t['size'] for t in trades) / n
        wr = wins / n * 100
        print(f'  {side:>4s}  {n:>4d} trades  WR={wr:>5.1f}%  AvgCost=${avg_cost:>6.2f}  PnL=${pnl:>+9.2f}')

    all_n = len(uniq_res)
    all_w = sum(1 for t in uniq_res if t['pnl'] > 0)
    all_p = sum(t['pnl'] for t in uniq_res)
    print(f'  BOTH  {all_n:>4d} trades  WR={all_w/all_n*100:>5.1f}%{"":18s}PnL=${all_p:>+9.2f}')

    # ── CHART 3: Weekly by side ──
    print()
    print('+' + '-' * 78 + '+')
    print('|  WEEKLY RESOLUTION P&L BY SIDE                                             |')
    print('+----------+----------------------------+----------------------------+--------+')
    print('|   Week   |  YES:  N    WR     PnL     |  NO:   N    WR     PnL     | Total  |')
    print('+----------+----------------------------+----------------------------+--------+')

    wk_data = defaultdict(lambda: {'YES_n':0,'YES_w':0,'YES_p':0.0,'NO_n':0,'NO_w':0,'NO_p':0.0})
    for r in uniq_res:
        d = datetime.strptime(r['day'], '%Y-%m-%d').date()
        wk = date.fromordinal(d.toordinal() - d.weekday()).strftime('%m-%d')
        s = r['side']
        wk_data[wk][f'{s}_n'] += 1
        wk_data[wk][f'{s}_p'] += r['pnl']
        if r['pnl'] > 0: wk_data[wk][f'{s}_w'] += 1

    for wk in sorted(wk_data):
        w = wk_data[wk]
        y_wr = f'{w["YES_w"]/w["YES_n"]*100:.0f}%' if w['YES_n'] else '  -'
        n_wr = f'{w["NO_w"]/w["NO_n"]*100:.0f}%' if w['NO_n'] else '  -'
        tot = w['YES_p'] + w['NO_p']
        print(f'| {wk:>7s}  | {w["YES_n"]:>4d}  {y_wr:>4s} ${w["YES_p"]:>+8.2f}    | {w["NO_n"]:>4d}  {n_wr:>4s} ${w["NO_p"]:>+8.2f}    |{tot:>+7.0f} |')

    print('+----------+----------------------------+----------------------------+--------+')

    # ── CHART 4: City P&L ──
    print()
    print('+' + '-' * 70 + '+')
    print('|  P&L BY CITY (resolution only)                                  |')
    print('+' + '-' * 70 + '+')

    cp = defaultdict(lambda: {'p': 0.0, 'n': 0, 'w': 0})
    for r in uniq_res:
        c = r['city']
        cp[c]['p'] += r['pnl']
        cp[c]['n'] += 1
        if r['pnl'] > 0: cp[c]['w'] += 1

    sc = sorted(cp.items(), key=lambda x: x[1]['p'], reverse=True)
    mx_c = max(abs(c[1]['p']) for c in sc) if sc else 1

    for city, s in sc[:15]:
        wr = s['w']/s['n']*100 if s['n'] else 0
        w = 22
        if s['p'] >= 0:
            bar = '#' * max(1, int(s['p'] / mx_c * w))
        else:
            bl = int(abs(s['p']) / mx_c * w)
            bar = ' ' * (w - bl) + '-' * bl
        print(f'  {city[:18]:<18s} {bar:<22s} ${s["p"]:>+8.2f}  N={s["n"]:>3d} WR={wr:.0f}%')

    if len(sc) > 15:
        print(f'  {"...":>18s}')
        for city, s in sc[-5:]:
            wr = s['w']/s['n']*100 if s['n'] else 0
            w = 22
            bl = int(abs(s['p']) / mx_c * w) if s['p'] < 0 else 0
            bar = ' ' * (w - bl) + '-' * bl if s['p'] < 0 else '#' * max(1, int(s['p'] / mx_c * w))
            print(f'  {city[:18]:<18s} {bar:<22s} ${s["p"]:>+8.2f}  N={s["n"]:>3d} WR={wr:.0f}%')

    # ── CHART 5: Lead time performance ──
    print()
    print('+' + '-' * 70 + '+')
    print('|  RESOLUTION P&L BY LEAD TIME AT ENTRY                           |')
    print('+' + '-' * 70 + '+')

    lt = defaultdict(lambda: {'p': 0.0, 'n': 0, 'w': 0})
    for r in uniq_res:
        h = r['lead_h']
        if h is None:
            b = 'f) unknown'
        elif h < 6:
            b = 'a) <6h'
        elif h < 12:
            b = 'b) 6-12h'
        elif h < 24:
            b = 'c) 12-24h'
        elif h < 48:
            b = 'd) 24-48h'
        elif h < 96:
            b = 'e) 48-96h'
        else:
            b = 'f) 96h+'
        lt[b]['p'] += r['pnl']
        lt[b]['n'] += 1
        if r['pnl'] > 0: lt[b]['w'] += 1

    mx_lt = max(abs(s['p']) for s in lt.values()) if lt else 1
    for bucket in sorted(lt):
        s = lt[bucket]
        wr = s['w']/s['n']*100 if s['n'] else 0
        w = 22
        if s['p'] >= 0:
            bar = '#' * max(1, int(s['p'] / mx_lt * w))
        else:
            bl = int(abs(s['p']) / mx_lt * w)
            bar = ' ' * (w - bl) + '-' * bl
        print(f'  {bucket:<14s} {bar:<22s} ${s["p"]:>+8.2f}  N={s["n"]:>3d} WR={wr:.0f}%')

    # ── SUMMARY ──
    print()
    print('=' * 72)
    exit_p = sum(r['pnl'] for r in raw if r['type'] == 'EXIT')
    res_p = sum(r['pnl'] for r in raw if r['type'] == 'RESOLUTION')
    n_e = sum(1 for r in raw if r['type'] == 'EXIT')
    n_r = sum(1 for r in raw if r['type'] == 'RESOLUTION')
    print(f'  Exit P&L:        ${exit_p:>+10.2f}  ({n_e} exits)')
    print(f'  Resolution P&L:  ${res_p:>+10.2f}  ({n_r} resolutions)')
    print(f'  TOTAL REALIZED:  ${exit_p + res_p:>+10.2f}')
    print(f'  Peak equity:     ${mx_cum:>+10.2f}')
    print(f'  Max drawdown:    ${-mx_dd:>+10.2f}  ({mx_dd/mx_cum*100:.1f}% of peak)' if mx_cum > 0 else '')
    print('=' * 72)

if __name__ == '__main__':
    main()
