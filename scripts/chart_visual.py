#!/usr/bin/env python3
"""WeatherBot actual P&L visual charts."""
import os, tempfile
from collections import defaultdict
from datetime import date, datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def load_data():
    raw = []
    with open(os.path.join(tempfile.gettempdir(), 'wb_pnl_raw.csv')) as f:
        for line in f:
            p = line.strip().split('|')
            if len(p) < 6: continue
            raw.append({
                'day': p[0], 'type': p[1], 'side': p[2],
                'pnl': float(p[3]) if p[3] else 0,
                'size': float(p[4]) if p[4] else 0,
                'price': float(p[5]) if p[5] else 0,
            })

    res = []
    with open(os.path.join(tempfile.gettempdir(), 'wb_res_enriched.csv')) as f:
        for line in f:
            p = line.strip().split('|')
            if len(p) < 9: continue
            res.append({
                'day': p[0], 'side': p[1],
                'pnl': float(p[2]) if p[2] else 0,
                'size': float(p[3]) if p[3] else 0,
                'entry_price': float(p[5]) if p[5] else 0,
                'city': p[6] if p[6] else 'Unknown',
                'lead_h': float(p[7]) if p[7] else None,
            })

    # Dedup resolutions
    seen = set()
    uniq = []
    for r in res:
        k = (r['day'], r['side'], f"{r['pnl']:.4f}", r['city'])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return raw, uniq

def main():
    raw, res = load_data()
    out = os.path.join(tempfile.gettempdir(), 'wb_charts.png')

    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle('WeatherBot Actual P&L — trade_events (Authoritative)', fontsize=16, fontweight='bold')
    plt.subplots_adjust(hspace=0.35, wspace=0.3, top=0.93)

    colors = {'pos': '#2ecc71', 'neg': '#e74c3c', 'YES': '#3498db', 'NO': '#e67e22',
              'cum': '#2c3e50', 'bar': '#7f8c8d'}

    # ── 1: Equity curve ──
    ax = axes[0, 0]
    daily = defaultdict(float)
    for r in raw:
        daily[r['day']] += r['pnl']

    days = sorted(daily)
    dates = [datetime.strptime(d, '%Y-%m-%d') for d in days]
    cum = []
    c = 0.0
    for d in days:
        c += daily[d]
        cum.append(c)

    ax.fill_between(dates, cum, alpha=0.3, color=colors['pos'])
    ax.plot(dates, cum, color=colors['cum'], linewidth=2.5)
    ax.set_title('Cumulative Equity Curve', fontweight='bold')
    ax.set_ylabel('Realized P&L ($)')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color='black', linewidth=0.5)
    for i, (d, v) in enumerate(zip(dates, cum)):
        if i == len(dates) - 1:
            ax.annotate(f'${v:+,.0f}', (d, v), textcoords="offset points",
                       xytext=(10, 5), fontsize=10, fontweight='bold', color=colors['cum'])

    # ── 2: Daily P&L bars ──
    ax = axes[0, 1]
    daily_vals = [daily[d] for d in days]
    bar_colors = [colors['pos'] if v >= 0 else colors['neg'] for v in daily_vals]
    ax.bar(dates, daily_vals, color=bar_colors, width=0.8, edgecolor='white', linewidth=0.5)
    ax.set_title('Daily Realized P&L', fontweight='bold')
    ax.set_ylabel('P&L ($)')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(True, alpha=0.3, axis='y')
    ax.axhline(y=0, color='black', linewidth=0.5)

    # ── 3: P&L by side (resolution) ──
    ax = axes[1, 0]
    side_data = defaultdict(lambda: {'n': 0, 'w': 0, 'p': 0.0})
    for r in res:
        s = r['side']
        side_data[s]['n'] += 1
        side_data[s]['p'] += r['pnl']
        if r['pnl'] > 0: side_data[s]['w'] += 1

    sides = ['NO', 'YES']
    pnls = [side_data[s]['p'] for s in sides]
    ns = [side_data[s]['n'] for s in sides]
    wrs = [side_data[s]['w']/side_data[s]['n']*100 if side_data[s]['n'] else 0 for s in sides]
    side_colors = [colors['NO'], colors['YES']]

    bars = ax.bar(sides, pnls, color=side_colors, edgecolor='white', linewidth=1, width=0.5)
    ax.set_title('Resolution P&L by Side', fontweight='bold')
    ax.set_ylabel('P&L ($)')
    ax.grid(True, alpha=0.3, axis='y')
    for bar, n, wr, p in zip(bars, ns, wrs, pnls):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 20,
                f'${p:+,.0f}\n{n} trades\nWR={wr:.0f}%',
                ha='center', va='bottom', fontsize=9, fontweight='bold')

    # ── 4: Weekly stacked by side ──
    ax = axes[1, 1]
    wk_data = defaultdict(lambda: {'YES': 0.0, 'NO': 0.0})
    for r in res:
        d = datetime.strptime(r['day'], '%Y-%m-%d').date()
        wk = date.fromordinal(d.toordinal() - d.weekday()).strftime('%m-%d')
        wk_data[wk][r['side']] += r['pnl']

    weeks = sorted(wk_data)
    yes_vals = [wk_data[w]['YES'] for w in weeks]
    no_vals = [wk_data[w]['NO'] for w in weeks]
    x = range(len(weeks))

    ax.bar(x, no_vals, label='NO', color=colors['NO'], width=0.4, align='center')
    ax.bar([i + 0.4 for i in x], yes_vals, label='YES', color=colors['YES'], width=0.4, align='center')
    ax.set_xticks([i + 0.2 for i in x])
    ax.set_xticklabels([f'Wk {w}' for w in weeks])
    ax.set_title('Weekly Resolution P&L by Side', fontweight='bold')
    ax.set_ylabel('P&L ($)')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    # ── 5: City P&L (top/bottom) ──
    ax = axes[2, 0]
    cp = defaultdict(lambda: {'p': 0.0, 'n': 0, 'w': 0})
    for r in res:
        c = r['city']
        if c == 'Unknown': continue
        cp[c]['p'] += r['pnl']
        cp[c]['n'] += 1
        if r['pnl'] > 0: cp[c]['w'] += 1

    sc = sorted(cp.items(), key=lambda x: x[1]['p'], reverse=True)
    # Top 6 + bottom 6
    show = sc[:6] + sc[-6:] if len(sc) > 12 else sc
    show = sorted(show, key=lambda x: x[1]['p'], reverse=True)

    city_names = [c[0][:14] for c in show]
    city_pnls = [c[1]['p'] for c in show]
    city_colors = [colors['pos'] if p >= 0 else colors['neg'] for p in city_pnls]

    ax.barh(city_names[::-1], city_pnls[::-1], color=city_colors[::-1], edgecolor='white', linewidth=0.5)
    ax.set_title('P&L by City (top/bottom)', fontweight='bold')
    ax.set_xlabel('P&L ($)')
    ax.axvline(x=0, color='black', linewidth=0.5)
    ax.grid(True, alpha=0.3, axis='x')

    # ── 6: Lead time P&L ──
    ax = axes[2, 1]
    lt = defaultdict(lambda: {'p': 0.0, 'n': 0, 'w': 0})
    for r in res:
        h = r['lead_h']
        if h is None: continue
        if h < 6: b = '<6h'
        elif h < 12: b = '6-12h'
        elif h < 24: b = '12-24h'
        elif h < 48: b = '24-48h'
        elif h < 96: b = '48-96h'
        else: b = '96h+'
        lt[b]['p'] += r['pnl']
        lt[b]['n'] += 1
        if r['pnl'] > 0: lt[b]['w'] += 1

    if lt:
        order = ['<6h', '6-12h', '12-24h', '24-48h', '48-96h', '96h+']
        order = [o for o in order if o in lt]
        lt_pnls = [lt[o]['p'] for o in order]
        lt_ns = [lt[o]['n'] for o in order]
        lt_wrs = [lt[o]['w']/lt[o]['n']*100 if lt[o]['n'] else 0 for o in order]
        lt_colors = [colors['pos'] if p >= 0 else colors['neg'] for p in lt_pnls]

        bars = ax.bar(order, lt_pnls, color=lt_colors, edgecolor='white', linewidth=0.5)
        for bar, n, wr in zip(bars, lt_ns, lt_wrs):
            y = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, y + (5 if y >= 0 else -15),
                    f'N={n}\n{wr:.0f}%', ha='center', fontsize=8)
    ax.set_title('Resolution P&L by Lead Time', fontweight='bold')
    ax.set_ylabel('P&L ($)')
    ax.set_xlabel('Lead time at entry')
    ax.grid(True, alpha=0.3, axis='y')
    ax.axhline(y=0, color='black', linewidth=0.5)

    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='white')
    print(f'Charts saved to: {out}')


if __name__ == '__main__':
    main()
