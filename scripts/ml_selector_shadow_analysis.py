"""
ML Selector Shadow Analysis — Three-ledger comparison (S124).

Queries MirrorBot trade_events where ml_score_xgb / ml_score_ql are present
in event_data, reconstructs three hypothetical P&L ledgers, and prints
side-by-side comparison.

Usage:
    python scripts/ml_selector_shadow_analysis.py [--hours 48]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=48)
    args = parser.parse_args()

    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    sql = text("""
        WITH entries AS (
            SELECT
                te.market_id, te.token_id, te.side, te.price, te.confidence,
                te.event_data,
                te.event_data->>'ml_score_xgb' AS ml_score_xgb,
                te.event_data->>'ml_decision_xgb' AS ml_decision_xgb,
                te.event_data->>'ml_score_ql' AS ml_score_ql,
                te.event_data->>'ml_decision_ql' AS ml_decision_ql,
                te.event_data->>'ml_decision_combo' AS ml_decision_combo,
                te.event_data->>'ml_score_combo' AS ml_score_combo
            FROM trade_events te
            WHERE te.bot_name = 'MirrorBot'
              AND te.event_type = 'ENTRY'
              AND te.event_time >= NOW() - MAKE_INTERVAL(hours => :hours)
              AND te.event_data ? 'ml_score_xgb'
        ),
        resolutions AS (
            SELECT DISTINCT ON (market_id, token_id)
                market_id, token_id, realized_pnl
            FROM trade_events
            WHERE bot_name = 'MirrorBot'
              AND event_type IN ('EXIT', 'RESOLUTION')
              AND realized_pnl IS NOT NULL
            ORDER BY market_id, token_id, event_time DESC
        )
        SELECT
            e.*,
            r.realized_pnl
        FROM entries e
        LEFT JOIN resolutions r ON r.market_id = e.market_id AND r.token_id = e.token_id
        ORDER BY e.ml_score_xgb::float NULLS LAST
    """)

    async with db.get_session() as session:
        result = await session.execute(sql, {"hours": args.hours})
        rows = result.fetchall()

    if not rows:
        print(f"No shadow-scored trades found in last {args.hours}h.")
        print("ML selector may not be deployed yet, or no trades occurred.")
        return

    # Separate resolved vs unresolved
    resolved = [r for r in rows if r.realized_pnl is not None]
    unresolved = [r for r in rows if r.realized_pnl is None]

    print(f"{'=' * 70}")
    print(f"ML Selector Shadow Analysis — Last {args.hours}h")
    print(f"{'=' * 70}")
    print(f"Total shadow-scored entries: {len(rows)}")
    print(f"Resolved (have P&L): {len(resolved)}")
    print(f"Unresolved (still open): {len(unresolved)}")

    if not resolved:
        print("\nNo resolved trades to analyze yet. Wait for positions to close.")
        return

    # Analyze each strategy
    strategies = [
        ("A: XGBoost", "ml_decision_xgb", "ml_score_xgb"),
        ("B: Q-learning", "ml_decision_ql", "ml_score_ql"),
        ("C: Combo (A+B)", "ml_decision_combo", "ml_score_combo"),
    ]

    print(f"\n{'Strategy':<20} {'Bucket':<10} {'N':>5} {'Win%':>6} {'Avg P&L':>10} {'Total P&L':>12} {'Worst':>10}")
    print("-" * 75)

    for name, dec_key, score_key in strategies:
        accept = [r for r in resolved if _parse_bool(getattr(r, dec_key, "true"))]
        reject = [r for r in resolved if not _parse_bool(getattr(r, dec_key, "true"))]

        for bucket_name, bucket in [("ACCEPT", accept), ("REJECT", reject)]:
            if not bucket:
                print(f"{name:<20} {bucket_name:<10} {'0':>5} {'--':>6} {'--':>10} {'--':>12} {'--':>10}")
                continue

            pnls = [float(r.realized_pnl) for r in bucket]
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(pnls) * 100
            avg = sum(pnls) / len(pnls)
            total = sum(pnls)
            worst = min(pnls)

            print(f"{name:<20} {bucket_name:<10} {len(pnls):>5} {wr:>5.1f}% ${avg:>9.2f} ${total:>11.2f} ${worst:>9.2f}")
        name = ""  # Don't repeat strategy name for reject row

    # Status quo comparison
    all_pnls = [float(r.realized_pnl) for r in resolved]
    print(f"\n{'STATUS QUO (no ML)':<20} {'ALL':<10} {len(all_pnls):>5} "
          f"{sum(1 for p in all_pnls if p > 0)/len(all_pnls)*100:>5.1f}% "
          f"${sum(all_pnls)/len(all_pnls):>9.2f} "
          f"${sum(all_pnls):>11.2f} "
          f"${min(all_pnls):>9.2f}")

    # Recommendation
    print(f"\n{'=' * 70}")
    print("RECOMMENDATION:")
    best_strategy = None
    best_lift = -float("inf")
    status_quo_pnl = sum(all_pnls)

    for name, dec_key, _ in strategies:
        accept = [r for r in resolved if _parse_bool(getattr(r, dec_key, "true"))]
        if accept:
            accept_pnl = sum(float(r.realized_pnl) for r in accept)
            lift = accept_pnl - status_quo_pnl
            if lift > best_lift:
                best_lift = lift
                best_strategy = name

    if best_lift > 0:
        print(f"  {best_strategy} would have improved P&L by ${best_lift:+.2f}")
        print(f"  Consider enabling with MIRROR_USE_ML_SELECTOR=true")
    else:
        print(f"  No strategy beat status quo. Keep collecting shadow data.")
    print(f"{'=' * 70}")


def _parse_bool(val) -> bool:
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")


if __name__ == "__main__":
    asyncio.run(main())
