#!/usr/bin/env python3
"""
Counterfactual P&L — shadow fill quality and sizing analysis.

Queries shadow_fills for a given bot and time window, then reports:
  - Fill quality: actual VWAP vs intended VWAP, slippage comparison
  - Book walk accuracy: fill_fraction (actual vs intended)
  - Sizing bias: max_bet_usd cap vs intended Kelly size (fires WARNING when cap < $50)
  - Rows missing intended_* fields (expected for fills before P0.2/P0.3 deploy)

Used as P0.20 criterion 5 — must run to completion (exit 0) with full
intended_* fields populated before shadow-live → $25-ramp flip.

Usage:
    python scripts/counterfactual_pnl.py                       # MirrorBot, last 7 days
    python scripts/counterfactual_pnl.py --bot MirrorBot       # explicit bot
    python scripts/counterfactual_pnl.py --bot MirrorBot --days 7
    python scripts/counterfactual_pnl.py --bot MirrorBot --days 14
"""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta

from base_engine.data.database import Database
from dotenv import load_dotenv

load_dotenv()

# Bias-warning threshold: fires when max_bet_usd < this value.
# Locked at $50 — see RAMP_FLIP_CHECKLIST.md §Step-7 §Bias warning.
_BIAS_WARNING_THRESHOLD_USD = 50.0


async def counterfactual_pnl(bot_name: str, days: int = 7) -> int:
    """Run counterfactual P&L analysis. Returns 0 on success, 1 on fatal error."""
    db = Database()
    await db.init()

    since_ts = datetime.utcnow() - timedelta(days=days)
    print(f"=== Counterfactual P&L — {bot_name} (last {days}d) ===")
    print(f"    Window: {since_ts.strftime('%Y-%m-%d %H:%M')} UTC → now")
    print()

    async with db.get_session() as s:
        from sqlalchemy import text

        # ── Block 1: row counts and NULL coverage ──────────────────────────
        r_counts = await s.execute(text("""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(CASE WHEN trade_executed THEN 1 END) AS executed_rows,
                COUNT(CASE WHEN NOT trade_executed THEN 1 END) AS rejected_rows,
                COUNT(intended_size_usd) AS has_intended_size,
                COUNT(vwap_at_intended) AS has_intended_vwap,
                COUNT(fill_frac_at_intended) AS has_fill_frac,
                COUNT(CASE WHEN intended_size_usd IS NULL THEN 1 END) AS missing_intended_size,
                COUNT(CASE WHEN vwap_at_intended IS NULL AND intended_size_usd IS NOT NULL THEN 1 END) AS has_size_no_vwap
            FROM shadow_fills
            WHERE bot_name = :bot_name
              AND created_at >= :since_ts
        """), {"bot_name": bot_name, "since_ts": since_ts})
        counts = r_counts.fetchone()

        total = int(counts[0] or 0)
        executed = int(counts[1] or 0)
        rejected = int(counts[2] or 0)
        has_intended = int(counts[3] or 0)
        has_vwap = int(counts[4] or 0)
        has_frac = int(counts[5] or 0)
        missing_intended = int(counts[6] or 0)
        has_size_no_vwap = int(counts[7] or 0)

        print(f"SHADOW FILL COVERAGE ({days}d):")
        print(f"  Total rows        : {total:>6}")
        print(f"  Executed (live)   : {executed:>6}")
        print(f"  Rejected (eroded) : {rejected:>6}")
        print(f"  Has intended_size : {has_intended:>6}  ({100*has_intended/max(total,1):.0f}%)")
        print(f"  Has intended_vwap : {has_vwap:>6}  ({100*has_vwap/max(total,1):.0f}%)")
        print(f"  Missing intended  : {missing_intended:>6}  (expected pre-P0.2/P0.3 deploy)")
        if has_size_no_vwap:
            print(f"  WARNING: {has_size_no_vwap} rows have intended_size but no vwap_at_intended "
                  f"(book walk likely failed for those signals)")
        print()

        if total == 0:
            print("No shadow fills found for this bot/window. Nothing to analyse.")
            print("  This is expected if the bot has not yet been running in shadow mode.")
            return 0

        # ── Block 2: Fill quality — actual vs intended VWAP ───────────────
        r_quality = await s.execute(text("""
            SELECT
                AVG(vwap_fill_price)              AS avg_actual_vwap,
                AVG(vwap_at_intended)              AS avg_intended_vwap,
                AVG(vwap_at_intended - vwap_fill_price) AS avg_vwap_gap,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY
                    vwap_at_intended - vwap_fill_price) AS median_vwap_gap,
                AVG(fill_fraction)                 AS avg_fill_frac_actual,
                AVG(fill_frac_at_intended)         AS avg_fill_frac_intended,
                AVG(fill_frac_at_intended - fill_fraction) AS avg_frac_gap,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fill_frac_at_intended) AS median_fill_frac_intended,
                AVG(book_walk_slippage)            AS avg_actual_slippage,
                AVG(slippage_at_intended)          AS avg_intended_slippage,
                COUNT(*)                           AS quality_rows
            FROM shadow_fills
            WHERE bot_name = :bot_name
              AND created_at >= :since_ts
              AND vwap_fill_price IS NOT NULL
              AND vwap_at_intended IS NOT NULL
        """), {"bot_name": bot_name, "since_ts": since_ts})
        quality = r_quality.fetchone()

        quality_rows = int(quality[10] or 0)
        print(f"FILL QUALITY (rows with both vwap fields: {quality_rows}):")
        if quality_rows > 0:
            avg_actual = float(quality[0] or 0)
            avg_intended = float(quality[1] or 0)
            avg_gap = float(quality[2] or 0)
            median_gap = float(quality[3] or 0)
            avg_ff_actual = float(quality[4] or 0)
            avg_ff_intended = float(quality[5] or 0)
            avg_ff_gap = float(quality[6] or 0)
            median_ff_intended = float(quality[7] or 0)
            avg_slippage_actual = float(quality[8] or 0)
            avg_slippage_intended = float(quality[9] or 0)

            print(f"  Avg actual VWAP           : {avg_actual:.4f}")
            print(f"  Avg intended VWAP         : {avg_intended:.4f}")
            print(f"  Avg VWAP gap (int-act)    : {avg_gap:+.4f}  (+ = intended fills worse than actual)")
            print(f"  Median VWAP gap           : {median_gap:+.4f}")
            print()
            print(f"  Avg actual fill_fraction  : {avg_ff_actual:.3f}")
            print(f"  Avg intended fill_fraction: {avg_ff_intended:.3f}")
            print(f"  Median intended fill_frac : {median_ff_intended:.3f}")
            print(f"  Avg frac gap (int-act)    : {avg_ff_gap:+.3f}")
            print()
            print(f"  Avg actual slippage       : {avg_slippage_actual:+.4f}")
            print(f"  Avg intended slippage     : {avg_slippage_intended:+.4f}")

            # Ramp-exit threshold check: median fill_frac_at_intended > 0.80
            # (RAMP_FLIP_CHECKLIST.md §Step-7 criterion 2)
            if median_ff_intended < 0.80:
                print(f"  WARNING: median fill_frac_at_intended {median_ff_intended:.3f} < 0.80 "
                      f"(ramp-exit threshold). Liquidity may be too thin at intended size.")
            else:
                print(f"  OK: median fill_frac_at_intended {median_ff_intended:.3f} >= 0.80")

            # VWAP gap threshold: within 0.5¢ of actual fill
            gap_cents = abs(avg_gap) * 100
            if gap_cents > 0.5:
                print(f"  WARNING: avg VWAP gap {gap_cents:.2f}¢ > 0.5¢ threshold. "
                      f"Intended-size walk diverges from actual fill.")
            else:
                print(f"  OK: avg VWAP gap {gap_cents:.2f}¢ <= 0.5¢ threshold")
        else:
            print("  No rows with both vwap_fill_price and vwap_at_intended — "
                  "check P0.3 deployment.")
        print()

        # ── Block 3: Sizing bias — cap vs intended Kelly ───────────────────
        r_sizing = await s.execute(text("""
            SELECT
                AVG(order_size_usd)         AS avg_actual_size,
                MAX(order_size_usd)         AS max_actual_size,
                AVG(intended_size_usd)      AS avg_intended_size,
                MAX(intended_size_usd)      AS max_intended_size,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY order_size_usd) AS median_actual_size,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY intended_size_usd) AS median_intended_size,
                COUNT(*)                    AS sizing_rows,
                AVG(CASE WHEN intended_size_usd > 0
                         THEN order_size_usd / intended_size_usd END) AS avg_cap_ratio
            FROM shadow_fills
            WHERE bot_name = :bot_name
              AND created_at >= :since_ts
              AND order_size_usd IS NOT NULL
              AND intended_size_usd IS NOT NULL
              AND trade_executed = FALSE
        """), {"bot_name": bot_name, "since_ts": since_ts})
        sizing = r_sizing.fetchone()

        sizing_rows = int(sizing[6] or 0)
        print(f"SIZING BIAS — rejected signals (rows with both size fields: {sizing_rows}):")
        if sizing_rows > 0:
            avg_actual = float(sizing[0] or 0)
            max_actual = float(sizing[1] or 0)
            avg_intended = float(sizing[2] or 0)
            max_intended = float(sizing[3] or 0)
            median_actual = float(sizing[4] or 0)
            median_intended = float(sizing[5] or 0)
            avg_cap_ratio = float(sizing[7] or 0)

            print(f"  Avg actual size (USD)  : {avg_actual:>8.2f}")
            print(f"  Avg intended size (USD): {avg_intended:>8.2f}")
            print(f"  Median actual size     : {median_actual:>8.2f}")
            print(f"  Median intended size   : {median_intended:>8.2f}")
            print(f"  Max actual size        : {max_actual:>8.2f}")
            print(f"  Max intended size      : {max_intended:>8.2f}")
            print(f"  Avg cap ratio (act/int): {avg_cap_ratio:>8.3f}  (1.0 = no capping)")
            if avg_cap_ratio < 1.0:
                bias_factor = 1.0 / avg_cap_ratio if avg_cap_ratio > 0 else float("inf")
                print(f"  Bias factor            : {bias_factor:>8.1f}×  "
                      f"(counterfactual P&L is an upper bound, not authoritative)")
        else:
            print("  No rejected signals with both size fields — check P0.2/P0.3 deployment.")
        print()

        # ── Block 4: Bias warning (threshold locked at $50) ───────────────
        # Read current max_bet_usd from the most recent signal's order_size_usd
        # as a proxy. The actual env value is only readable at runtime; we warn
        # when the cap appears below threshold based on observed max fill size.
        r_maxbet = await s.execute(text("""
            SELECT MAX(order_size_usd) AS observed_max
            FROM shadow_fills
            WHERE bot_name = :bot_name
              AND created_at >= :since_ts
              AND trade_executed = FALSE
        """), {"bot_name": bot_name, "since_ts": since_ts})
        maxbet_row = r_maxbet.fetchone()
        observed_max = float(maxbet_row[0] or 0) if maxbet_row else 0.0

        if observed_max < _BIAS_WARNING_THRESHOLD_USD and observed_max > 0:
            print(f"  *** BIAS WARNING ***")
            print(f"  Observed max bet size {observed_max:.2f} < ${_BIAS_WARNING_THRESHOLD_USD:.0f} threshold.")
            print(f"  Counterfactual P&L overstates potential returns.")
            print(f"  Use for plumbing-correctness and directional consistency only.")
            print(f"  DO NOT use counterfactual figures to justify cap increases.")
            print()

        # ── Block 5: Sample rows (last 5 executed) ────────────────────────
        r_sample = await s.execute(text("""
            SELECT
                LEFT(market_id::text, 12) AS mid,
                side,
                ROUND(order_size_usd::numeric, 2) AS size_usd,
                ROUND(intended_size_usd::numeric, 2) AS intended_usd,
                ROUND(vwap_fill_price::numeric, 4) AS vwap,
                ROUND(vwap_at_intended::numeric, 4) AS vwap_int,
                ROUND(fill_fraction::numeric, 3) AS ff,
                ROUND(fill_frac_at_intended::numeric, 3) AS ff_int,
                created_at
            FROM shadow_fills
            WHERE bot_name = :bot_name
              AND created_at >= :since_ts
              AND trade_executed = FALSE
            ORDER BY created_at DESC
            LIMIT 5
        """), {"bot_name": bot_name, "since_ts": since_ts})
        sample_rows = r_sample.fetchall()

        print("RECENT REJECTED SIGNALS (last 5):")
        print(f"{'Market':<14} {'Side':>4} {'ActUSD':>8} {'IntUSD':>8} "
              f"{'VWAP':>7} {'VWAPint':>8} {'FF':>5} {'FFint':>6}  Created")
        print("-" * 90)
        for row in sample_rows:
            mid = (str(row[0]) + "..") if row[0] else "N/A"
            side = str(row[1] or "")
            sz = f"{float(row[2] or 0):8.2f}" if row[2] is not None else "    NULL"
            int_sz = f"{float(row[3] or 0):8.2f}" if row[3] is not None else "    NULL"
            vwap = f"{float(row[4] or 0):7.4f}" if row[4] is not None else "   NULL"
            vwap_i = f"{float(row[5] or 0):8.4f}" if row[5] is not None else "    NULL"
            ff = f"{float(row[6] or 0):5.3f}" if row[6] is not None else " NULL"
            ff_i = f"{float(row[7] or 0):6.3f}" if row[7] is not None else "  NULL"
            ts = row[8].strftime("%m-%d %H:%M") if row[8] else "N/A"
            print(f"{mid:<14} {side:>4} {sz} {int_sz} {vwap} {vwap_i} {ff} {ff_i}  {ts}")

        print()
        print(f"=== Done. Exit 0. ===")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Counterfactual P&L for shadow fills.")
    parser.add_argument("--bot", default="MirrorBot", help="Bot name (default: MirrorBot)")
    parser.add_argument("--days", type=int, default=7,
                        help="Look-back window in days (default: 7)")
    args = parser.parse_args()

    rc = asyncio.run(counterfactual_pnl(bot_name=args.bot, days=args.days))
    sys.exit(rc)


if __name__ == "__main__":
    main()
