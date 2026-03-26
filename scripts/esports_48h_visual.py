#!/usr/bin/env python3
"""EsportsBot 48h P&L visual charts — S120 (mirrors WeatherBot chart_visual.py format)."""
import os, sys
from collections import defaultdict
from datetime import datetime, date
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def load_data(path):
    rows = []
    with open(path) as f:
        for line in f:
            p = line.strip().split('|')
            if len(p) < 8:
                continue
            rows.append({
                'time': p[0], 'type': p[1], 'game': p[2] or 'unknown',
                'side': p[3], 'price': float(p[4]), 'size': float(p[5]),
                'pnl': float(p[6]), 'conf': float(p[7]),
            })
    return rows


def main():
    csv_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'esports_48h_raw.csv')
    # Also check /tmp
    for candidate in [csv_path, '/tmp/esports_48h_raw.csv',
                      os.path.join(os.environ.get('TEMP', '/tmp'), 'esports_48h_raw.csv')]:
        if os.path.exists(candidate):
            csv_path = candidate
            break

    rows = load_data(csv_path)
    print(f"Loaded {len(rows)} rows from {csv_path}")

    entries = [r for r in rows if r['type'] == 'ENTRY']
    exits = [r for r in rows if r['type'] == 'EXIT']
    resolutions = [r for r in rows if r['type'] == 'RESOLUTION']

    colors = {
        'pos': '#2ecc71', 'neg': '#e74c3c',
        'YES': '#3498db', 'NO': '#e67e22', 'EXIT': '#7f8c8d',
        'cum': '#2c3e50', 'bar': '#7f8c8d',
        'cs2': '#e74c3c', 'lol': '#3498db', 'cod': '#2ecc71',
        'valorant': '#9b59b6', 'dota2': '#f39c12', 'unknown': '#95a5a6',
    }

    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle('EsportsBot P&L Analysis — 48h (trade_events)', fontsize=16, fontweight='bold')
    plt.subplots_adjust(hspace=0.35, wspace=0.3, top=0.93)

    # ── 1: Equity Curve ──
    ax = axes[0, 0]
    daily = defaultdict(float)
    for r in rows:
        if r['pnl'] != 0:
            day = r['time'][:10]
            daily[day] += r['pnl']

    days = sorted(daily)
    dates_parsed = [datetime.strptime(d, '%Y-%m-%d') for d in days]
    cum = []
    c = 0.0
    for d in days:
        c += daily[d]
        cum.append(c)

    if cum:
        ax.fill_between(dates_parsed, cum, alpha=0.3, color=colors['pos'] if cum[-1] >= 0 else colors['neg'])
        ax.plot(dates_parsed, cum, color=colors['cum'], linewidth=2.5)
        ax.annotate(f'${cum[-1]:+,.0f}', (dates_parsed[-1], cum[-1]),
                    textcoords="offset points", xytext=(10, 5),
                    fontsize=11, fontweight='bold', color=colors['cum'])
    ax.set_title('Cumulative Equity Curve', fontweight='bold')
    ax.set_ylabel('Realized P&L ($)')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color='black', linewidth=0.5)

    # ── 2: Daily P&L Bars ──
    ax = axes[0, 1]
    if daily:
        daily_vals = [daily[d] for d in days]
        bar_colors = [colors['pos'] if v >= 0 else colors['neg'] for v in daily_vals]
        ax.bar(dates_parsed, daily_vals, color=bar_colors, width=0.8, edgecolor='white', linewidth=0.5)
        for d, v in zip(dates_parsed, daily_vals):
            offset = max(abs(v) * 0.05, 15)
            ax.text(d, v + (offset if v >= 0 else -offset), f'${v:+,.0f}',
                    ha='center', va='bottom' if v >= 0 else 'top', fontsize=9, fontweight='bold')
    ax.set_title('Daily Realized P&L', fontweight='bold')
    ax.set_ylabel('P&L ($)')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(True, alpha=0.3, axis='y')
    ax.axhline(y=0, color='black', linewidth=0.5)

    # ── 3: P&L by Side (resolution + exit) ──
    ax = axes[1, 0]
    side_data = defaultdict(lambda: {'n': 0, 'w': 0, 'p': 0.0})
    for r in resolutions:
        s = r['side']
        side_data[s]['n'] += 1
        side_data[s]['p'] += r['pnl']
        if r['pnl'] > 0:
            side_data[s]['w'] += 1
    for r in exits:
        side_data['EXIT']['n'] += 1
        side_data['EXIT']['p'] += r['pnl']
        if r['pnl'] > 0:
            side_data['EXIT']['w'] += 1

    show_sides = [s for s in ['YES', 'NO', 'EXIT'] if side_data[s]['n'] > 0]
    if show_sides:
        pnls = [side_data[s]['p'] for s in show_sides]
        ns = [side_data[s]['n'] for s in show_sides]
        wrs = [side_data[s]['w']/side_data[s]['n']*100 if side_data[s]['n'] else 0 for s in show_sides]
        side_colors = [colors.get(s, colors['bar']) for s in show_sides]
        bars = ax.bar(show_sides, pnls, color=side_colors, edgecolor='white', linewidth=1, width=0.5)
        for bar, n, wr, p in zip(bars, ns, wrs, pnls):
            offset = max(abs(p) * 0.05, 15)
            ax.text(bar.get_x() + bar.get_width()/2,
                    bar.get_height() + (offset if p >= 0 else -offset),
                    f'${p:+,.0f}\n{n} trades\nWR={wr:.0f}%',
                    ha='center', va='bottom' if p >= 0 else 'top', fontsize=9, fontweight='bold')
    ax.set_title('P&L by Side (Resolution + Exit)', fontweight='bold')
    ax.set_ylabel('P&L ($)')
    ax.grid(True, alpha=0.3, axis='y')
    ax.axhline(y=0, color='black', linewidth=0.5)

    # ── 4: P&L by Game (horizontal bars, like city chart) ──
    ax = axes[1, 1]
    game_pnl = defaultdict(lambda: {'p': 0.0, 'n': 0, 'w': 0})
    for r in rows:
        if r['pnl'] == 0:
            continue
        g = r['game']
        game_pnl[g]['p'] += r['pnl']
        game_pnl[g]['n'] += 1
        if r['pnl'] > 0:
            game_pnl[g]['w'] += 1

    if game_pnl:
        sg = sorted(game_pnl.items(), key=lambda x: x[1]['p'], reverse=True)
        gnames = [g[0] for g in sg]
        gpnls = [g[1]['p'] for g in sg]
        gcolors = [colors.get(g[0], colors['bar']) for g in sg]
        bars = ax.barh(gnames[::-1], gpnls[::-1], color=gcolors[::-1], edgecolor='white', linewidth=0.5)
        for bar, g in zip(bars, sg[::-1]):
            n = g[1]['n']
            wr = g[1]['w']/n*100 if n else 0
            p = g[1]['p']
            xpos = bar.get_width() + (10 if p >= 0 else -10)
            ax.text(xpos, bar.get_y() + bar.get_height()/2,
                    f'${p:+,.0f} ({n}t, {wr:.0f}%WR)',
                    ha='left' if p >= 0 else 'right', va='center', fontsize=9, fontweight='bold')
    ax.set_title('P&L by Game (sector)', fontweight='bold')
    ax.set_xlabel('P&L ($)')
    ax.axvline(x=0, color='black', linewidth=0.5)
    ax.grid(True, alpha=0.3, axis='x')

    # ── 5: Entry Price Distribution ──
    ax = axes[2, 0]
    if entries:
        prices = [r['price'] for r in entries]
        ax.hist(prices, bins=np.arange(0, 1.0, 0.1), color=colors['cum'], edgecolor='white', alpha=0.85)
        med = np.median(prices)
        ax.axvline(med, color=colors['neg'], linestyle='--', linewidth=2, label=f'Median: {med:.2f}')
        ax.legend()
    ax.set_title('Entry Price Distribution', fontweight='bold')
    ax.set_xlabel('Entry Price')
    ax.set_ylabel('Count')
    ax.grid(True, alpha=0.3, axis='y')

    # ── 6: Confidence by Game (box plot) ──
    ax = axes[2, 1]
    game_confs = defaultdict(list)
    for r in entries:
        if r['conf'] > 0:
            game_confs[r['game']].append(r['conf'])

    if game_confs:
        games = sorted(game_confs, key=lambda x: len(game_confs[x]), reverse=True)
        bp_data = [game_confs[g] for g in games]
        bp = ax.boxplot(bp_data, tick_labels=games, patch_artist=True,
                        medianprops={'color': 'black', 'linewidth': 2})
        for patch, g in zip(bp['boxes'], games):
            patch.set_facecolor(colors.get(g, colors['bar']))
            patch.set_alpha(0.7)
        ax.axhline(0.48, color='red', linestyle=':', linewidth=1, label='Min conf (0.48)')
        ax.legend(fontsize=8)
    ax.set_title('Model Confidence at Entry', fontweight='bold')
    ax.set_ylabel('Confidence')
    ax.grid(True, alpha=0.3, axis='y')

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'esports_48h_charts.png')
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='white')
    print(f'Charts saved to: {out}')


if __name__ == '__main__':
    main()
