#!/usr/bin/env python3
"""esports_silo — THE forward skill gate: model vs Polymarket on resolved predictions.

This is the script whose verdict decides everything downstream. It loads FORWARD
predictions (written by the runner's --predict pass), joins them to resolved outcomes,
and runs `eval/skill_metrics.evaluate` — P&L-free (Cmd 1), de-vig-free (Cmd 2).
PASS means: on >= SKILL_MIN_N resolved forward predictions carrying a Polymarket price,
the model's Brier beat the market's AND calibration held (ECE <= SKILL_MAX_ECE).

Selection rules (each defends a known failure mode):
  * LAST pre-event prediction per (match, market) — one verdict row per market, no
    double-counting a market that was predicted on every 15-min pass.
  * ingest_time < event_time — a prediction logged after the match started is look-ahead
    and NEVER scored (defense in depth; the runner shouldn't write them anyway).
  * only calibrated rows (p_model NOT NULL) — the unfitted era can't dilute the sample.
  * model_version pinned — a refit under a new version starts its own ledger.

The verdict prints and the exit code carries it (0 = PASS, 1 = FAIL/insufficient) so the
operator can script it. NOTHING here flips the halt: even on PASS, lifting
SILO_ENTRY_HALT is a deliberate human action (see GO_LIVE_CHECKLIST).

Run (operator's box):
  python -m esports_silo.scripts.skill_report            # human summary + exit code
  python -m esports_silo.scripts.skill_report --json     # machine-readable
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Dict, List

try:
    from ..config import CONFIG
    from ..eval import skill_metrics
except ImportError:  # loose-file fallback
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import CONFIG  # type: ignore
    from eval import skill_metrics  # type: ignore

MODEL_VERSION = "sharp_consensus_v1"   # must match pipeline.MODEL_VERSION

# Last pre-event prediction per (match, market), calibrated rows only, resolved matches only.
_PREDICTIONS_SQL = """
SELECT DISTINCT ON (p.match_id, p.market_id)
       p.match_id, p.market_id, p.p_model, p.market_price, p.event_time,
       m.winner, m.game
  FROM predictions p
  JOIN matches m ON m.match_id = p.match_id
 WHERE p.model_version = $1
   AND p.p_model IS NOT NULL
   AND p.ingest_time < p.event_time
   AND m.winner IN ('team_a','team_b')
 ORDER BY p.match_id, p.market_id, p.ingest_time DESC
"""

# RAW closing sharp line per match (diagnostic closing-line agreement; Cmd 2: raw 1/odds).
_CLOSING_SQL = """
SELECT DISTINCT ON (o.match_id)
       o.match_id, o.team_a_odds, o.team_b_odds
  FROM odds_raw o
  JOIN matches m USING (match_id)
 WHERE o.aggregator = 'oddspapi'
   AND o.book = ANY($1::text[])
   AND o.line_time <= m.start_time
 ORDER BY o.match_id, o.line_time DESC
"""


def build_records(pred_rows: List[Dict[str, Any]],
                  closing_by_match: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DB rows → skill_metrics prediction records. Pure."""
    out = []
    for r in pred_rows:
        winner = r.get("winner")
        if winner not in ("team_a", "team_b"):
            continue
        rec = {
            "p_model": r.get("p_model"),
            "market_price": r.get("market_price"),
            "outcome": 1 if winner == "team_a" else 0,
            "game": r.get("game"),
        }
        close = closing_by_match.get(str(r.get("match_id")))
        if close:
            rec["team_a_odds"] = close.get("team_a_odds")
        out.append(rec)
    return out


async def run(as_json: bool) -> int:
    import asyncpg
    if not CONFIG.database_url:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(CONFIG.database_url)
    try:
        preds = [dict(r) for r in await conn.fetch(_PREDICTIONS_SQL, MODEL_VERSION)]
        books = [b.lower() for b in CONFIG.sharp_books]
        closing = {str(r["match_id"]): dict(r)
                   for r in await conn.fetch(_CLOSING_SQL, books)}
    finally:
        await conn.close()

    records = build_records(preds, closing)
    report = skill_metrics.evaluate(records)
    ok, reasons = report.passes_skill_gate()

    if as_json:
        print(json.dumps({
            "model_version": MODEL_VERSION,
            "gate": "PASS" if ok else "FAIL",
            "reasons": reasons,
            "n": report.n, "n_with_market": report.n_with_market,
            "model_brier": report.model_brier, "market_brier": report.market_brier,
            "brier_skill": report.brier_skill, "model_ece": report.model_ece,
            "model_auc": report.model_auc, "per_game": report.per_game,
            "closing_line": report.closing_line,
        }, indent=2, default=str))
    else:
        print(report.summary())
        print(f"\nverdict: {'PASS' if ok else 'FAIL'} (model_version={MODEL_VERSION}, "
              f"forward resolved rows={report.n}, with market price={report.n_with_market})")
        if not ok:
            for r in reasons:
                print(f"  - {r}")
        print("\nNOTE: PASS does not lift the halt — flipping SILO_ENTRY_HALT is a "
              "deliberate human action (GO_LIVE_CHECKLIST).")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="forward skill-gate verdict (P&L-free)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.json)))


if __name__ == "__main__":
    main()
