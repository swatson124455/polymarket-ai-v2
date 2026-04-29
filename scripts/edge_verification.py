#!/usr/bin/env python3
"""
1I: Edge Verification — Phase 7 elevation gate evaluator.

Bootstrap P(edge > 0) and Kelly fraction per bot from trade_events.
Uses only RESOLUTION + EXIT events with realized_pnl (closed trades).

Phase 7 v7 gate (S172_CONSOLIDATED_PLAN.md:441-446) — applies to MirrorBot
and WeatherBot on post-Day-2 data (--since 20260414_132211 --clean):
  P(edge>0) >= 0.30  → PROCEED (directionally positive, Phase 7 elevation)
  0.10 <= P < 0.30   → AMBIGUOUS (continue collecting, re-run weekly)
  P < 0.10           → INVESTIGATE (remaining loss drivers — do not elevate)
  n < 500 closed     → INSUFFICIENT SAMPLE (gate not yet evaluable)

EsportsBotV2 has a SEPARATE gate — Phase 5v2-D paper-trading promotion
(S172_CONSOLIDATED_PLAN.md:347, :357, :556):
  P(edge>0) >= 0.70 — different threshold from v7's 0.30 PROCEED line
  n >= 100 resolved predictions — different sample-size from v7's 500
  Plus accuracy >55%, wl_ratio >0.80, max drawdown <25% (not measured here)
The v7_verdict() function below applies the v7 ladder uniformly. For EB v2
the operator must read the printed P(edge>0) and compare to 0.70 manually
(rather than trust the v7 verdict label). EB v1 (legacy 'EsportsBot') and
EB v2 ('EsportsBotV2') are DISJOINT cohorts in this tool — Phase 5v2-D
evaluates v2 alone, independent of v1's frozen-sample shadow window.

Usage:
    python scripts/edge_verification.py                          # All 4 bots, all-time, raw
    python scripts/edge_verification.py EsportsBotV2             # Phase 5v2-D candidate
    python scripts/edge_verification.py MirrorBot --since 20260414_132211 --clean
        # MirrorBot post-Day-2 trades on whole-history-clean markets — formal Phase 7 gate.

S199: --since windows trades to event_time >= the parsed UTC stamp; --clean
excludes (bot, market) pairs whose all-time disposal exceeds entry by >0.1%
(matching scripts/bot_pnl.py CLEAN logic). Both default off (pre-S199 behavior).

S203: default bot list expanded to include EsportsBotV2 so default
invocation does not silently skip v2 post-flag-flip.
"""
import argparse
import asyncio
from datetime import datetime
import numpy as np
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

N_BOOTSTRAP = 10_000
RNG_SEED = 42

# Phase 7 v7 thresholds. See module docstring for context.
V7_PROCEED_THRESHOLD = 0.30
V7_INVESTIGATE_THRESHOLD = 0.10
V7_MIN_SAMPLE = 500

# Phase 5v2-D thresholds — apply to EsportsBotV2 only. See module docstring.
PHASE_5V2D_P_EDGE_THRESHOLD = 0.70
PHASE_5V2D_MIN_SAMPLE = 100

# Default bot list when no positional arg is passed. S203: EsportsBotV2 added
# alongside the legacy 3 so default invocation does not silently skip v2.
# v1 and v2 are DISJOINT entries here (gate-evaluation independence) — see
# S203_EB_ROUTING_AUDIT.md §3.2.
_DEFAULT_BOTS = ["WeatherBot", "MirrorBot", "EsportsBot", "EsportsBotV2"]


def parse_deploy_timestamp(ts: str) -> datetime:
    """Parse a deploy-stamp string `YYYYMMDD_HHMMSS` into a naive UTC datetime.

    Mirrors scripts/bot_pnl.py:parse_deploy_timestamp — same format expected.
    """
    return datetime.strptime(ts, "%Y%m%d_%H%M%S")


def v7_verdict(p_edge_positive: float, n_closed: int) -> tuple[str, str]:
    """Map (P(edge>0), n_closed) → (verdict, detail) per S172 v7 gate.

    Sample-size check is independent — even a high P(edge>0) is INSUFFICIENT
    SAMPLE below n=500. Above n=500, the threshold ladder applies.
    """
    if n_closed < V7_MIN_SAMPLE:
        return ("INSUFFICIENT SAMPLE",
                f"n={n_closed} < {V7_MIN_SAMPLE}: gate not yet evaluable; collect more trades")
    if p_edge_positive >= V7_PROCEED_THRESHOLD:
        return ("PROCEED",
                "Directionally positive — Phase 7 elevation may proceed")
    if p_edge_positive >= V7_INVESTIGATE_THRESHOLD:
        return ("AMBIGUOUS",
                "Mid-range — continue collecting; re-run weekly until decisive")
    return ("INVESTIGATE",
            "Below floor — investigate remaining loss drivers before any elevation")


async def edge_verification(
    bot_name: str | None = None,
    since: datetime | None = None,
    clean: bool = False,
):
    db = Database()
    await db.init()

    bots = [bot_name] if bot_name else list(_DEFAULT_BOTS)

    # Optional clauses shared across bots. --since filters event_time; --clean
    # excludes whole markets that ever exhibited size_invariant contamination
    # (CTE matches scripts/bot_pnl.py:140 — single source of truth for the
    # contamination definition: SUM(EXIT+RESOLUTION size) > SUM(ENTRY size) * 1.001).
    since_clause = "AND event_time >= :since_ts" if since is not None else ""
    clean_clause = (
        "AND market_id NOT IN ("
        "  SELECT market_id FROM trade_events"
        "  WHERE bot_name = :bot"
        "    AND event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')"
        "    AND size IS NOT NULL"
        "  GROUP BY market_id"
        "  HAVING SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION')"
        "                  THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)"
        "       > SUM(CASE WHEN event_type = 'ENTRY'"
        "                  THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001"
        ")"
        if clean else ""
    )
    filter_label = []
    if since is not None:
        filter_label.append(f"since {since.strftime('%Y%m%d_%H%M%S')}")
    if clean:
        filter_label.append("clean")
    filter_str = f" [{' + '.join(filter_label)}]" if filter_label else ""

    async with db.get_session() as s:
        from sqlalchemy import text

        for bot in bots:
            params: dict = {"bot": bot}
            if since is not None:
                params["since_ts"] = since
            # Fetch all closed-trade P&L and stake amounts
            r = await s.execute(text(f"""
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
                  {since_clause}
                  {clean_clause}
                ORDER BY event_time
            """), params)
            rows = r.fetchall()

            if not rows:
                print(f"\n{'='*60}")
                print(f"  {bot}{filter_str}: NO CLOSED TRADES — cannot verify edge")
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

            # --- Graduated decision (v7) ---
            verdict, verdict_detail = v7_verdict(p_edge_positive, len(pnls))

            # --- Output ---
            print(f"\n{'='*60}")
            print(f"  EDGE VERIFICATION: {bot}{filter_str}")
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
            if bot == "EsportsBotV2":
                # S203: v7 ladder does not apply to EB v2 — different gate
                # (Phase 5v2-D, S172_CONSOLIDATED_PLAN.md:347, :357, :556).
                # The v7 verdict label above is informational only for v2;
                # this annotation is the load-bearing pass/fail for the gate.
                v2_meets_p_edge = p_edge_positive >= PHASE_5V2D_P_EDGE_THRESHOLD
                v2_meets_n      = len(pnls) >= PHASE_5V2D_MIN_SAMPLE
                v2_marker = "MEETS" if (v2_meets_p_edge and v2_meets_n) else "DOES NOT MEET"
                print(f"      [Phase 5v2-D gate: P(edge>0)>={PHASE_5V2D_P_EDGE_THRESHOLD} "
                      f"AND n>={PHASE_5V2D_MIN_SAMPLE} → {v2_marker}]")
            print(f"{'='*60}")

    await db.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI parser. Preserves pre-S199 invocation: `edge_verification.py [bot]`."""
    p = argparse.ArgumentParser(description="Edge verification — Phase 7 v7 gate evaluator")
    p.add_argument("bot_name", nargs="?", default=None,
                   help="Bot name (default: all 3 bots)")
    p.add_argument("--since", type=parse_deploy_timestamp, default=None,
                   metavar="YYYYMMDD_HHMMSS",
                   help="Filter trades to event_time >= this UTC stamp. "
                        "Day 2 deploy is 20260414_132211.")
    p.add_argument("--clean", action="store_true", default=False,
                   help="Exclude whole-history-contaminated markets "
                        "(matches scripts/bot_pnl.py CLEAN logic).")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(edge_verification(args.bot_name, since=args.since, clean=args.clean))
