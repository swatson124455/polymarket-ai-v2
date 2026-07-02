#!/usr/bin/env python3
"""Shadow-mode runner for the v3 MirrorBot trader-scoring engine.

Reads the DB, writes a JSON report. NO order authority — this script must
never import order_gateway, place_order, or any execution path.

Usage (VPS):
  python scripts/mirror_scoring_run.py --stage q
  python scripts/mirror_scoring_run.py --stage q --cutoff 2026-05-15T00:00:00
  python scripts/mirror_scoring_run.py --stage validate --cutoff 2026-05-15T00:00:00

Stages:
  q         Stage-1 quality scores + BH selection + shadow Kelly weights
  validate  Stage-1 + counterfactual kill-criterion vs mirror_rejected_signals
(Stage-2 tailability runs per-trader via bots.mirror_scoring.tailability
 once Stage-1 validation PASSES; deliberately not exposed here before that.)
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_engine.data.database import Database  # noqa: E402
from bots.mirror_scoring.config import ScoringConfig  # noqa: E402
from bots.mirror_scoring.q_score import run_universe  # noqa: E402
from bots.mirror_scoring.validation import validate_ranking  # noqa: E402


def _json_safe(obj):
    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["q", "validate"], default="q")
    ap.add_argument("--cutoff", default=None, help="ISO holdout cutoff")
    args = ap.parse_args()

    cfg = ScoringConfig()
    if args.cutoff:
        cfg.HOLDOUT_CUTOFF_ISO = args.cutoff

    db = Database()
    await db.initialize()
    try:
        scores = await run_universe(db, cfg)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stage": args.stage,
            "config": _json_safe(cfg),
            "label": cfg.UNVERIFIED_LABEL,
            "n_scored": len(scores),
            "n_admitted": sum(1 for t in scores if t.admitted),
            "scores": [_json_safe(t) for t in sorted(
                scores, key=lambda t: (-int(t.admitted), t.p_holdout)
            )],
        }
        if args.stage == "validate":
            if cfg.HOLDOUT_CUTOFF_ISO is None:
                print("validate requires --cutoff", file=sys.stderr)
                return 2
            vr = await validate_ranking(
                db, scores, datetime.fromisoformat(cfg.HOLDOUT_CUTOFF_ISO), cfg
            )
            report["validation"] = _json_safe(vr)
            print(f"VALIDATION: {'PASS' if vr.passed else 'FAIL'} — {vr.detail}")

        os.makedirs(cfg.REPORT_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(cfg.REPORT_DIR, f"mirror_scoring_{args.stage}_{stamp}.json")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[{cfg.UNVERIFIED_LABEL}] scored={report['n_scored']} "
              f"admitted={report['n_admitted']} report={path}")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
