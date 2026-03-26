#!/usr/bin/env python3
"""WeatherBot 48h analysis charts — same format as chart_visual.py."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from collections import defaultdict
from datetime import datetime
import os, sys

def main():
    data_file = os.path.join(os.path.dirname(__file__), "..", "wb_48h_raw.csv")
    if not os.path.exists(data_file):
        print(f"Missing {data_file} — run VPS export first")
        sys.exit(1)

    rows = []
    with open(data_file) as f:
        for line in f:
            p = line.strip().split("|")
            if len(p) < 9:
                continue
            try:
                rows.append({
                    "day": p[0], "side": p[1], "pnl": float(p[2]),
                    "cost": float(p[3]), "price": float(p[4]),
                    "city": p[5], "lead_h": float(p[6]) if p[6] != "-1" else None,
                    "conf": float(p[7]) if p[7] else 0.5,
                    "won": int(p[8]) if p[8] else 0,
                })
            except (ValueError, IndexError):
                continue

    colors = {
        "pos": "#2ecc71", "neg": "#e74c3c",
        "YES": "#3498db", "NO": "#e67e22",
        "cum": "#2c3e50",
    }

    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle(f"WeatherBot \u2014 Last 48 Hours ({len(rows)} resolutions)",
                 fontsize=16, fontweight="bold")
    plt.subplots_adjust(hspace=0.35, wspace=0.3, top=0.93)

    # 1: Cumulative equity curve
    ax = axes[0, 0]
    daily = defaultdict(float)
    for r in rows:
        daily[r["day"]] += r["pnl"]
    days = sorted(daily)
    dates = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    cum = []
    c = 0.0
    for d in days:
        c += daily[d]
        cum.append(c)
    fill_color = colors["pos"] if cum and cum[-1] >= 0 else colors["neg"]
    ax.fill_between(dates, cum, alpha=0.3, color=fill_color)
    ax.plot(dates, cum, color=colors["cum"], linewidth=2.5)
    ax.set_title("Cumulative Equity Curve", fontweight="bold")
    ax.set_ylabel("Realized P&L ($)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color="black", linewidth=0.5)
    if dates:
        ax.annotate(f"${cum[-1]:+,.0f}", (dates[-1], cum[-1]),
                    textcoords="offset points", xytext=(10, 5),
                    fontsize=11, fontweight="bold", color=colors["cum"])

    # 2: Daily P&L bars
    ax = axes[0, 1]
    daily_vals = [daily[d] for d in days]
    bar_colors = [colors["pos"] if v >= 0 else colors["neg"] for v in daily_vals]
    ax.bar(dates, daily_vals, color=bar_colors, width=0.8,
           edgecolor="white", linewidth=0.5)
    ax.set_title("Daily Realized P&L", fontweight="bold")
    ax.set_ylabel("P&L ($)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="black", linewidth=0.5)
    for d, v in zip(dates, daily_vals):
        ax.text(d, v + (10 if v >= 0 else -25), f"${v:+,.0f}",
                ha="center", fontsize=9, fontweight="bold")

    # 3: P&L by side
    ax = axes[1, 0]
    sd = defaultdict(lambda: {"n": 0, "w": 0, "p": 0.0})
    for r in rows:
        sd[r["side"]]["n"] += 1
        sd[r["side"]]["p"] += r["pnl"]
        if r["pnl"] > 0:
            sd[r["side"]]["w"] += 1
    sides = ["NO", "YES"]
    pnls = [sd[s]["p"] for s in sides]
    ns = [sd[s]["n"] for s in sides]
    wrs = [sd[s]["w"] / sd[s]["n"] * 100 if sd[s]["n"] else 0 for s in sides]
    bars = ax.bar(sides, pnls, color=[colors["NO"], colors["YES"]],
                  edgecolor="white", linewidth=1, width=0.5)
    ax.set_title("Resolution P&L by Side", fontweight="bold")
    ax.set_ylabel("P&L ($)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="black", linewidth=0.5)
    for bar, n, wr, p in zip(bars, ns, wrs, pnls):
        y = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2,
                y + (15 if y >= 0 else -50),
                f"${p:+,.0f}\n{n} trades\nWR={wr:.0f}%",
                ha="center", va="bottom" if y >= 0 else "top",
                fontsize=10, fontweight="bold")

    # 4: Price bucket P&L
    ax = axes[1, 1]
    pb = defaultdict(lambda: {"p": 0.0, "n": 0, "w": 0})
    for r in rows:
        if r["price"] < 0.20:
            b = "0-20c"
        elif r["price"] < 0.40:
            b = "20-40c"
        elif r["price"] < 0.60:
            b = "40-60c"
        elif r["price"] < 0.80:
            b = "60-80c"
        else:
            b = "80-100c"
        pb[b]["p"] += r["pnl"]
        pb[b]["n"] += 1
        if r["pnl"] > 0:
            pb[b]["w"] += 1
    order = ["0-20c", "20-40c", "40-60c", "60-80c", "80-100c"]
    order = [o for o in order if o in pb]
    bp = [pb[o]["p"] for o in order]
    bn = [pb[o]["n"] for o in order]
    bwr = [pb[o]["w"] / pb[o]["n"] * 100 if pb[o]["n"] else 0 for o in order]
    bc = [colors["pos"] if p >= 0 else colors["neg"] for p in bp]
    bars = ax.bar(order, bp, color=bc, edgecolor="white", linewidth=0.5)
    for bar, n, wr in zip(bars, bn, bwr):
        y = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2,
                y + (10 if y >= 0 else -20),
                f"N={n}\n{wr:.0f}%", ha="center", fontsize=9, fontweight="bold")
    ax.set_title("P&L by Entry Price Bucket", fontweight="bold")
    ax.set_ylabel("P&L ($)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="black", linewidth=0.5)

    # 5: City P&L (top/bottom 6)
    ax = axes[2, 0]
    cp = defaultdict(lambda: {"p": 0.0, "n": 0, "w": 0})
    for r in rows:
        if r["city"] == "Unknown":
            continue
        cp[r["city"]]["p"] += r["pnl"]
        cp[r["city"]]["n"] += 1
        if r["pnl"] > 0:
            cp[r["city"]]["w"] += 1
    sc = sorted(cp.items(), key=lambda x: x[1]["p"], reverse=True)
    show = sc[:6] + sc[-6:] if len(sc) > 12 else sc
    show = sorted(show, key=lambda x: x[1]["p"], reverse=True)
    cn = [c[0][:14] for c in show]
    cpnl = [c[1]["p"] for c in show]
    cc = [colors["pos"] if p >= 0 else colors["neg"] for p in cpnl]
    ax.barh(cn[::-1], cpnl[::-1], color=cc[::-1],
            edgecolor="white", linewidth=0.5)
    ax.set_title("P&L by City (top/bottom 6)", fontweight="bold")
    ax.set_xlabel("P&L ($)")
    ax.axvline(x=0, color="black", linewidth=0.5)
    ax.grid(True, alpha=0.3, axis="x")
    for i, (name, p) in enumerate(zip(cn[::-1], cpnl[::-1])):
        ax.text(p + (3 if p >= 0 else -3), i, f"${p:+,.0f}",
                va="center", ha="left" if p >= 0 else "right",
                fontsize=9, fontweight="bold")

    # 6: Calibration
    ax = axes[2, 1]
    cal = defaultdict(lambda: {"n": 0, "w": 0, "conf_sum": 0.0})
    for r in rows:
        d = min(int(r["conf"] * 10), 9)
        cal[d]["n"] += 1
        cal[d]["w"] += r["won"]
        cal[d]["conf_sum"] += r["conf"]
    deciles = sorted(cal.keys())
    pred = [cal[d]["conf_sum"] / cal[d]["n"] for d in deciles]
    act = [cal[d]["w"] / cal[d]["n"] for d in deciles]
    ns_cal = [cal[d]["n"] for d in deciles]
    labels = [f"{d * 10}-{d * 10 + 10}%" for d in deciles]
    x = list(range(len(deciles)))
    ax.plot(x, pred, "b-o", label="Predicted", linewidth=2.5, markersize=8)
    ax.plot(x, act, "r-s", label="Actual WR", linewidth=2.5, markersize=8)
    ax.plot([0, len(deciles) - 1],
            [deciles[0] / 10, (deciles[-1] + 1) / 10],
            "k--", alpha=0.3, linewidth=1.5, label="Perfect")
    ax.fill_between(x, pred, act, alpha=0.12, color="red")
    for i, n in enumerate(ns_cal):
        ax.annotate(f"n={n}", (i, act[i]), textcoords="offset points",
                    xytext=(0, 12), ha="center", fontsize=8, color="#555")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, fontsize=8)
    ax.set_ylabel("Probability")
    ax.set_title("Calibration: Predicted vs Actual", fontweight="bold")
    ax.legend(fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.grid(True, alpha=0.3)

    out = "C:/lockes-picks/polymarket-ai-v2/weather_48h_analysis.png"
    plt.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
