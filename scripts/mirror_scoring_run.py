#!/usr/bin/env python3
"""Shadow-mode runner for the v3 MirrorBot trader-scoring engine.

Reads the DB, writes a JSON report. NO order authority — this script must
never import order_gateway, place_order, or any execution path.

Usage (VPS):
  python scripts/mirror_scoring_run.py --stage q
  python scripts/mirror_scoring_run.py --stage q --cutoff 2026-05-15T00:00:00
  python scripts/mirror_scoring_run.py --stage validate --cutoff 2026-05-15T00:00:00

The two engine queries extend their own session timeout (SET LOCAL '300s' —
the default 30s bot-tier statement_timeout cancels the unbounded universe
scan). Belt-and-braces for a heavily loaded DB:
  DB_STATEMENT_TIMEOUT_MS=300000 python scripts/mirror_scoring_run.py ...
(with sudo: sudo --preserve-env=DB_STATEMENT_TIMEOUT_MS -u polymarket env ...)

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
    ap.add_argument("--stream", choices=["legacy", "v3", "all"], default="legacy",
                    help="which mirror_rejected_signals population to validate "
                         "against (F3: old-MB gate rejections vs the v3 raw "
                         "collector stream; runs are labeled and not comparable "
                         "across streams)")
    args = ap.parse_args()

    cfg = ScoringConfig()
    # Fail fast on invalid invocations BEFORE any DB work — the 2026-07-02 VPS
    # run used '--stage validate' with no --cutoff and burned the entire
    # universe scan before hitting the (previously post-scan) cutoff check.
    if args.stage == "validate" and not args.cutoff:
        print("validate requires --cutoff", file=sys.stderr)
        return 2
    if args.cutoff:
        # Parse now (bad ISO fails fast, pre-DB) and normalize tz-aware input to
        # naive UTC: markets.resolved_at is TIMESTAMP WITHOUT TIME ZONE, and an
        # aware cutoff raises TypeError inside split_events_by_resolution AFTER
        # the full universe fetch. cfg keeps its Optional[str] ISO contract.
        _cut = datetime.fromisoformat(args.cutoff)
        if _cut.tzinfo is not None:
            _cut = _cut.astimezone(timezone.utc).replace(tzinfo=None)
        cfg.HOLDOUT_CUTOFF_ISO = _cut.isoformat()

    # Fail fast on an unwritable report dir — pre-fix this was created only at
    # the END of the run, so a PermissionError discarded all computed scores.
    try:
        os.makedirs(cfg.REPORT_DIR, exist_ok=True)
        _probe = os.path.join(cfg.REPORT_DIR, ".write_probe")
        with open(_probe, "w") as _pf:
            _pf.write("ok")
        os.remove(_probe)
    except OSError as _dir_err:
        print(f"report dir '{cfg.REPORT_DIR}' not writable from cwd {os.getcwd()}: "
              f"{_dir_err}", file=sys.stderr)
        return 2

    db = Database()
    # Database exposes init(), not initialize() — same API-mismatch class as the
    # shadow_analysis.py bug in the salvage catalog. Crashed the 2026-07-02 VPS
    # validate run at startup (AttributeError) before any query executed.
    await db.init()
    try:
        # init() NEVER raises: empty/bad DATABASE_URL logs a warning and returns
        # with session_factory=None ('API-only mode'), and the run would then die
        # inside run_universe with a misleading error. Fail fast with a clear one
        # (inside try so the finally still closes the db).
        if db.session_factory is None:
            print("DB init failed — DATABASE_URL missing/invalid (run with the "
                  "same env as scripts/bot_pnl.py)", file=sys.stderr)
            return 3
        scores = await run_universe(db, cfg)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stage": args.stage,
            "config": _json_safe(cfg),
            "label": cfg.UNVERIFIED_LABEL,
            "n_scored": len(scores),
            "n_admitted": sum(1 for t in scores if t.admitted),
            # condition_ids is F1 plumbing for validation's overlap exclusion
            # (can be thousands of ids per trader) — keep it out of the report.
            "scores": [
                {k: v for k, v in _json_safe(t).items() if k != "condition_ids"}
                for t in sorted(scores, key=lambda t: (-int(t.admitted), t.p_holdout))
            ],
        }
        if args.stage == "validate":
            # --cutoff presence already enforced pre-DB (top of main()).
            vr = await validate_ranking(
                db, scores, datetime.fromisoformat(cfg.HOLDOUT_CUTOFF_ISO), cfg,
                signal_stream=args.stream,
            )
            report["validation"] = _json_safe(vr)
            print(f"VALIDATION: {'PASS' if vr.passed else 'FAIL'} "
                  f"[stream={vr.signal_stream}, overlap_excluded="
                  f"{vr.n_excluded_overlap}] — {vr.detail}")

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
