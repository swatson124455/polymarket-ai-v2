"""EsportsBot — Win Rate & P&L by Game × Model Confidence Bucket"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import os

# Raw query results — game, prob_bucket, n, res_w, res_n, res_wr, res_pnl, exit_pnl, total_pnl
RAW = [
    ("cod",      "0.50-0.55",  2, 0,  2,   0.0,     0.00,  -126.14,   -126.14),
    ("cod",      "0.60-0.65",  3, 0,  3,   0.0,     0.00,  -727.91,   -727.91),
    ("cod",      "0.65-0.70",  1, 0,  1,   0.0,  -307.89,     0.00,   -307.89),
    ("cod",      "0.75+",      3, 0,  3,   0.0,     0.00,  -292.02,   -292.02),
    ("cs2",      "0.50-0.55", 11, 0,  5,   0.0,  -774.49,  -882.82,  -1657.31),
    ("cs2",      "0.55-0.60",  6, 0,  6,   0.0, -1227.47,  -269.00,  -1496.47),
    ("cs2",      "0.60-0.65", 19, 5, 13,  38.5,  1679.92,  -783.22,    896.71),
    ("cs2",      "0.65-0.70", 15, 2, 10,  20.0,  -430.43,  -377.91,   -808.34),
    ("cs2",      "0.70-0.75", 11, 2,  4,  50.0,  -287.10,  -187.30,   -474.40),
    ("cs2",      "0.75+",     37,10, 17,  58.8,   340.32,  -853.69,   -513.37),
    ("dota2",    "0.50-0.55",  8, 0,  6,   0.0, -5198.25,  -149.98,  -5348.22),
    ("dota2",    "0.55-0.60",  1, 0,  1,   0.0,  -388.21,     0.00,   -388.21),
    ("dota2",    "0.60-0.65",  3, 0,  1,   0.0,     0.00,  -290.68,   -290.68),
    ("dota2",    "0.65-0.70",  2, 1,  2,  50.0,   102.06,   -52.02,     50.05),
    ("dota2",    "0.70-0.75",  5, 2,  2, 100.0,   229.09,  -757.55,   -528.46),
    ("dota2",    "0.75+",      6, 4,  5,  80.0,   217.20,   -89.45,    127.75),
    ("lol",      "0.50-0.55", 27, 1, 23,   4.3,  -552.17,   516.73,    -35.44),
    ("lol",      "0.60-0.65",  4, 0,  3,   0.0,  -526.13,     0.00,   -526.13),
    ("lol",      "0.65-0.70",  4, 1,  4,  25.0,  -274.91,     0.00,   -274.91),
    ("lol",      "0.70-0.75",  1, 0,  0,   None,    0.00,    -6.50,     -6.50),
    ("lol",      "0.75+",      1, 1,  1, 100.0,    33.89,     0.00,     33.89),
    ("valorant", "0.50-0.55",  1, 0,  1,   0.0,     0.00,   -18.13,    -18.13),
    ("valorant", "0.55-0.60",  1, 1,  1, 100.0,   157.34,     0.00,    157.34),
    ("valorant", "0.60-0.65",  6, 1,  2,  50.0,   409.35,   -66.82,    342.53),
    ("valorant", "0.65-0.70",  5, 0,  2,   0.0,  -309.29,  1635.83,   1326.54),
    ("valorant", "0.70-0.75",  2, 0,  2,   0.0,  -350.39,  1719.82,   1369.43),
    ("valorant", "0.75+",      1, 0,  1,   0.0,     0.00,  1719.82,   1719.82),
]

GAMES = ["cs2", "dota2", "lol", "valorant", "cod"]
GAME_COLORS = {
    "cs2": "#FF6B35", "dota2": "#E63946", "lol": "#457B9D",
    "valorant": "#2A9D8F", "cod": "#6C757D",
}
BUCKET_ORDER = ["0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70-0.75", "0.75+"]

def build_game_data(game):
    rows = [r for r in RAW if r[0] == game]
    buckets, trades, wr, pnl = [], [], [], []
    for b in BUCKET_ORDER:
        match = [r for r in rows if r[1] == b]
        if match:
            r = match[0]
            buckets.append(b)
            trades.append(r[2])
            wr.append(r[5] if r[5] is not None else 0)
            pnl.append(r[8])
    return buckets, trades, wr, pnl

fig, axes = plt.subplots(len(GAMES), 1, figsize=(12, 4 * len(GAMES)))
fig.suptitle("EsportsBot — Win Rate & P&L by Model Confidence\n(All-Time, per Game)",
             fontsize=16, fontweight='bold', y=0.995)

for idx, game in enumerate(GAMES):
    ax = axes[idx]
    buckets, trades, wr, pnl = build_game_data(game)
    if not buckets:
        ax.text(0.5, 0.5, f"{game.upper()} — No Data", ha='center', va='center', fontsize=14)
        ax.set_axis_off()
        continue

    x = np.arange(len(buckets))
    width = 0.38
    color = GAME_COLORS.get(game, "#333")

    # Bar chart: WR% on left axis
    bars = ax.bar(x - width/2, wr, width, color=color, alpha=0.85, label='Win Rate %', zorder=3)
    for bar, w, n in zip(bars, wr, trades):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.5,
                f"{w:.0f}%\n(n={n})", ha='center', va='bottom', fontsize=8, fontweight='bold')

    # 50% reference line
    ax.axhline(y=50, color='gray', linestyle='--', alpha=0.5, linewidth=0.8)

    ax.set_ylim(0, max(max(wr) + 20, 60))
    ax.set_ylabel("Resolution Win Rate %", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(buckets, fontsize=9)
    ax.set_xlabel("Model Probability (chosen side)", fontsize=10)

    # P&L on right axis
    ax2 = ax.twinx()
    pnl_colors = ['#2ecc71' if p >= 0 else '#e74c3c' for p in pnl]
    bars2 = ax2.bar(x + width/2, pnl, width, color=pnl_colors, alpha=0.7, label='Total P&L $', zorder=2)
    for bar, p in zip(bars2, pnl):
        yoff = 10 if p >= 0 else -10
        va = 'bottom' if p >= 0 else 'top'
        ax2.text(bar.get_x() + bar.get_width()/2, p + yoff,
                 f"${p:+,.0f}", ha='center', va=va, fontsize=8, color='#333')

    pnl_abs = max(abs(p) for p in pnl) if pnl else 100
    ax2.set_ylim(-pnl_abs * 1.4, pnl_abs * 1.4)
    ax2.set_ylabel("Total P&L ($)", fontsize=10)
    ax2.axhline(y=0, color='black', linewidth=0.5)

    # Title with summary
    total = sum(pnl)
    total_n = sum(trades)
    ax.set_title(f"{game.upper()}  —  {total_n} trades  |  ${total:+,.0f} total P&L",
                 fontsize=13, fontweight='bold', pad=10,
                 color=GAME_COLORS.get(game, '#333'))

    # Legend
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=8)
    ax.grid(axis='y', alpha=0.3)

plt.tight_layout(rect=[0, 0, 1, 0.97])
out = os.path.join(os.path.dirname(__file__), '..', 'esports_wr_by_game.png')
plt.savefig(out, dpi=150, bbox_inches='tight')
print(f"Saved: {os.path.abspath(out)}")
