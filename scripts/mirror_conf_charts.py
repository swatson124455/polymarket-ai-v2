"""S130 MirrorBot confidence spread + P&L charts — runs on VPS, saves PNGs"""
import asyncio
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    async with db.get_session() as s:

        # --- Chart 1: Confidence scatter since S130 deploy ---
        r1 = await s.execute(text(
            "SELECT event_time, "
            "  COALESCE(confidence, "
            "    (event_data->>'conf_base')::float "
            "    + COALESCE((event_data->>'conf_cat_adj')::float, 0) "
            "    + COALESCE((event_data->>'conf_price_adj')::float, 0) "
            "    + COALESCE((event_data->>'conf_conv_adj')::float, 0) "
            "  ) as conf "
            "FROM trade_events "
            "WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY' "
            "AND event_time >= '2026-03-25 17:55:00' "
            "ORDER BY event_time"
        ))
        rows1 = r1.fetchall()
        confs = [r[1] for r in rows1 if r[1] is not None]

        fig, ax = plt.subplots(figsize=(14, 6))
        ax.scatter(range(len(confs)), confs, alpha=0.3, s=8, c="steelblue")
        ax.set_xlabel("Entry # (since S130 deploy)")
        ax.set_ylabel("Confidence")
        ax.set_title(f"MirrorBot Confidence Scatter post-S130 ({len(confs)} entries)")
        ax.axhline(y=0.55, color="red", linestyle="--", alpha=0.5, label="Old floor (0.55)")
        if confs:
            ax.axhline(y=np.median(confs), color="green", linestyle="--", alpha=0.5,
                        label=f"Median ({np.median(confs):.3f})")
        ax.set_ylim(0.45, 0.85)
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig("/tmp/mirror_conf_scatter.png", dpi=150)
        plt.close()
        print(f"Chart 1: {len(confs)} entries, min={min(confs):.3f}, max={max(confs):.3f}, median={np.median(confs):.3f}")

        # --- Chart 2: Confidence bucket vs WR + P&L (7 day) ---
        r2 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side, "
            "    COALESCE(e.confidence, "
            "      (e.event_data->>'conf_base')::float "
            "      + COALESCE((e.event_data->>'conf_cat_adj')::float, 0) "
            "      + COALESCE((e.event_data->>'conf_price_adj')::float, 0) "
            "      + COALESCE((e.event_data->>'conf_conv_adj')::float, 0) "
            "    ) as conf "
            "  FROM trade_events e "
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY' "
            "  AND e.event_time >= NOW() - INTERVAL '7 days' AND e.event_time <= NOW()"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl "
            "  FROM trade_events "
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION' "
            ") SELECT "
            "  CASE "
            "    WHEN e.conf < 0.53 THEN '0.50-0.52' "
            "    WHEN e.conf < 0.55 THEN '0.53-0.54' "
            "    WHEN e.conf < 0.57 THEN '0.55-0.56' "
            "    WHEN e.conf < 0.59 THEN '0.57-0.58' "
            "    WHEN e.conf < 0.61 THEN '0.59-0.60' "
            "    WHEN e.conf < 0.63 THEN '0.61-0.62' "
            "    WHEN e.conf < 0.65 THEN '0.63-0.64' "
            "    WHEN e.conf < 0.70 THEN '0.65-0.69' "
            "    ELSE '>=0.70' "
            "  END as bucket, "
            "  COUNT(*) as n, "
            "  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins, "
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr, "
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl "
            "FROM entries e "
            "JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side "
            "GROUP BY 1 ORDER BY 1"
        ))
        rows2 = r2.fetchall()
        buckets = [r[0] for r in rows2]
        ns = [r[1] for r in rows2]
        wrs = [float(r[3]) if r[3] else 0 for r in rows2]
        pnls = [float(r[4]) if r[4] else 0 for r in rows2]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
        colors_wr = ["#2ecc71" if wr >= 50 else "#e74c3c" for wr in wrs]
        bars1 = ax1.bar(range(len(buckets)), wrs, color=colors_wr, alpha=0.8, edgecolor="black", linewidth=0.5)
        ax1.axhline(y=50, color="gray", linestyle="--", alpha=0.5)
        ax1.set_ylabel("Win Rate %")
        ax1.set_title("MirrorBot — Confidence Bucket vs Win Rate (7-day resolved)")
        for i, (b, n) in enumerate(zip(bars1, ns)):
            ax1.text(i, b.get_height() + 1, f"n={n}", ha="center", va="bottom", fontsize=8)
        ax1.set_ylim(0, 70)
        ax1.grid(True, alpha=0.3, axis="y")

        colors_pnl = ["#2ecc71" if p >= 0 else "#e74c3c" for p in pnls]
        ax2.bar(range(len(buckets)), pnls, color=colors_pnl, alpha=0.8, edgecolor="black", linewidth=0.5)
        ax2.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax2.set_ylabel("Total P&L ($)")
        ax2.set_xlabel("Confidence Bucket")
        ax2.set_xticks(range(len(buckets)))
        ax2.set_xticklabels(buckets, rotation=45, ha="right")
        ax2.grid(True, alpha=0.3, axis="y")
        plt.tight_layout()
        plt.savefig("/tmp/mirror_conf_buckets.png", dpi=150)
        plt.close()
        print(f"Chart 2: {len(buckets)} buckets, total resolved={sum(ns)}")
        for row in rows2:
            print(f"  {row[0]}: n={row[1]}, wins={row[2]}, WR={row[3]}%, P&L=${row[4]}")

        # --- Chart 3: Category P&L bars (7 day) ---
        r3 = await s.execute(text(
            "WITH entries AS ("
            "  SELECT e.market_id, e.side, "
            "    COALESCE(e.event_data->>'category', 'unknown') as cat "
            "  FROM trade_events e "
            "  WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY' "
            "  AND e.event_time >= NOW() - INTERVAL '7 days' AND e.event_time <= NOW()"
            "), resolutions AS ("
            "  SELECT market_id, side, realized_pnl "
            "  FROM trade_events "
            "  WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION' "
            ") SELECT "
            "  e.cat, COUNT(*) as n, "
            "  ROUND(100.0 * SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as wr, "
            "  ROUND(SUM(r.realized_pnl)::numeric, 2) as total_pnl "
            "FROM entries e "
            "JOIN resolutions r ON r.market_id = e.market_id AND r.side = e.side "
            "GROUP BY 1 ORDER BY total_pnl ASC"
        ))
        rows3 = r3.fetchall()
        cats = [r[0] for r in rows3]
        cat_ns = [r[1] for r in rows3]
        cat_wrs = [float(r[2]) if r[2] else 0 for r in rows3]
        cat_pnls = [float(r[3]) if r[3] else 0 for r in rows3]

        fig, ax = plt.subplots(figsize=(12, 6))
        colors_cat = ["#2ecc71" if p >= 0 else "#e74c3c" for p in cat_pnls]
        ax.barh(range(len(cats)), cat_pnls, color=colors_cat, alpha=0.8, edgecolor="black", linewidth=0.5)
        ax.set_yticks(range(len(cats)))
        ax.set_yticklabels([f"{c} (n={n}, WR={wr}%)" for c, n, wr in zip(cats, cat_ns, cat_wrs)])
        ax.axvline(x=0, color="gray", linestyle="--", alpha=0.5)
        ax.set_xlabel("Total P&L ($)")
        ax.set_title("MirrorBot — Category P&L (7-day resolved)")
        ax.grid(True, alpha=0.3, axis="x")
        plt.tight_layout()
        plt.savefig("/tmp/mirror_cat_pnl.png", dpi=150)
        plt.close()
        print(f"Chart 3: {len(cats)} categories")

    await db.close()
    print("\nAll charts saved to /tmp/mirror_*.png")

asyncio.run(main())
