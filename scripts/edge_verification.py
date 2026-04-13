#!/usr/bin/env python3
"""
1I: Edge Verification — S172 Phase 1 HARD GATE for Phases 5-7.

Bootstrap P(edge > 0) and Kelly fraction per bot from trade_events.
Uses only RESOLUTION + EXIT events with realized_pnl (closed trades).

Graduated response:
  P(edge>0) >= 0.9  → FULL elevation (Phases 5-7 proceed as planned)
  0.7 <= P < 0.9    → CORE ONLY (skip speculative items)
  P < 0.7           → ROOT-CAUSE INVESTIGATION replaces elevation

Usage:
    python scripts/edge_verification.py              # All 3 bots
    python scripts/edge_verification.py EsportsBot   # Single bot
"""
import asyncio
import sys
import numpy as np
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

N_BOOTSTRAP = 10_000
RNG_SEED = 42


async def edge_verification(bot_name: str | None = None):
    db = Database()
    await db.init()

    bots = [bot_name] if bot_name else ["WeatherBot", "MirrorBot", "EsportsBot"]

    async with db.get_session() as s:
        from sqlalchemy import text

        for bot in bots:
            # Fetch all closed-trade P&L and stake amounts
            r = await s.execute(text("""
                SELECT
                    CAST(realized_pnl AS DOUBLE PRECISION) AS pnl,
                    CAST(COALESCE(
                        (event_data->>'entry_price')::numeric * size,
                        price * size
                    ) AS DOUBLE PRECISION) AS stake,
                    event_type
                FROM trade_events
                WHERE bot_name = :bot
                  AND event_type IN ('RESOLUTION', 'EXIT')
                  AND realized_pnl IS NOT NULL
                ORDER BY event_time
            """), {"bot": bot})
            rows = r.fetchall()

            if not rows:
                print(f"\n{'='*60}")
                print(f"  {bot}: NO CLOSED TRADES — cannot verify edge")
                print(f"{'='*60}")
                continue

            pnls = np.array([float(r[0]) for r in rows])
            stakes = np.array([float(r[1]) if r[1] and float(r[1]) > 0 else 1.0 for r in rows])
            event_types = [r[2] for r in rows]

            n_res = sum(1 for e in event_types if e == 'RESOLUTION')
            n_exit = sum(1 for e in event_types if e == 'EXIT')
            n_win = int(np.sum(pnls > 0))
            n_loss = int(np.sum(pnls < 0))
            n_flat = int(np.sum(pnls == 0))

            total_pnl = float(np.sum(pnls))
            total_stake = float(np.sum(stakes))
            raw_edge = total_pnl / total_stake if total_stake > 0 else 0.0
            win_rate = n_win / len(pnls) if len(pnls) > 0 else 0.0

            # --- Bootstrap P(edge > 0) ---
            rng = np.random.default_rng(RNG_SEED)
            n = len(pnls)
            boot_edges = np.empty(N_BOOTSTRAP)
            for i in range(N_BOOTSTRAP):
                idx = rng.integers(0, n, size=n)
                boot_pnl = pnls[idx]
                boot_stake = stakes[idx]
                total_s = boot_stake.sum()
                boot_edges[i] = boot_pnl.sum() / total_s if total_s > 0 else 0.0

            p_edge_positive = float(np.mean(boot_edges > 0))
            edge_ci_lo = float(np.percentile(boot_edges, 2.5))
            edge_ci_hi = float(np.percentile(boot_edges, 97.5))
            edge_mean = float(np.mean(boot_edges))

            # --- Kelly fraction (from bootstrap) ---
            # Kelly = edge / odds, but for binary bets: f* = p - q/b
            # Simpler: f* = mean_return / variance_of_returns
            returns = pnls / stakes  # per-trade return on stake
            returns = returns[np.isfinite(returns)]
            if len(returns) > 1:
                kelly_mean = float(np.mean(returns))
                kelly_var = float(np.var(returns))
                kelly_full = kelly_mean / kelly_var if kelly_var > 0 else 0.0
                kelly_half = kelly_full / 2  # half-Kelly (practical)
            else:
                kelly_full = kelly_half = 0.0

            # --- Graduated decision ---
            if p_edge_positive >= 0.9:
                verdict = "FULL ELEVATION"
                verdict_detail = "Phases 5-7 proceed as planned"
            elif p_edge_positive >= 0.7:
                verdict = "CORE ONLY"
                verdict_detail = "Skip speculative items in elevation phases"
            else:
                verdict = "ROOT-CAUSE INVESTIGATION"
                verdict_detail = "Replace elevation with investigation"

            # --- Output ---
            print(f"\n{'='*60}")
            print(f"  EDGE VERIFICATION: {bot}")
            print(f"{'='*60}")
            print(f"  Closed trades:     {len(pnls):>6}  (RES={n_res}, EXIT={n_exit})")
            print(f"  Win/Loss/Flat:     {n_win}/{n_loss}/{n_flat}  (WR={win_rate:.1%})")
            print(f"  Total P&L:         ${total_pnl:>+12.2f}")
            print(f"  Total stake:       ${total_stake:>12.2f}")
            print(f"  Raw edge:          {raw_edge:>+.4f}  ({raw_edge:.2%})")
            print(f"")
            print(f"  Bootstrap ({N_BOOTSTRAP:,} samples):")
            print(f"    P(edge > 0):     {p_edge_positive:.4f}")
            print(f"    Edge mean:       {edge_mean:>+.4f}")
            print(f"    Edge 95% CI:     [{edge_ci_lo:>+.4f}, {edge_ci_hi:>+.4f}]")
            print(f"")
            print(f"  Kelly fraction:")
            print(f"    Full Kelly:      {kelly_full:>+.4f}")
            print(f"    Half Kelly:      {kelly_half:>+.4f}")
            print(f"")
            print(f"  >>> VERDICT: {verdict}")
            print(f"      {verdict_detail}")
            print(f"{'='*60}")

    await db.close()


if __name__ == "__main__":
    bot = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(edge_verification(bot))
