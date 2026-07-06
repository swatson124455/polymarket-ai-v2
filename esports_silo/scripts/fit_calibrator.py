#!/usr/bin/env python3
"""esports_silo — fit the Platt calibrator on backfilled (closing line, outcome) pairs.

Turns the historical backfill into a usable signal: joins labelled `matches` rows to their
last PRE-START sharp line in `odds_raw`, fits `PlattCalibrator` (raw vig-kept consensus
score → P(team_a); calibration from OUTCOMES, never de-vig — Cmd 2), evaluates on a
TIME-ORDERED holdout, and writes the fitted parameters to a JSON artifact the runner loads.

WHAT THIS DOES **NOT** DO (read before trusting any number it prints):
  * It does NOT prove skill. A backfit FITS; the skill gate (`scripts/skill_report.py`)
    proves on FORWARD predictions vs the Polymarket price, per D2/Cmd 4. `SILO_ENTRY_HALT`
    stays true regardless of anything printed here.
  * The holdout "baseline" is the RAW consensus score used as if it were a probability —
    NOT the Polymarket price (historical rows have no Polymarket price). `brier_skill > 0`
    here means only "the calibrated map forecasts better than the raw vig-kept score".
  * No P&L anywhere (Cmd 1).

Honesty guards baked in:
  * time-ordered split — train on the OLDEST (1−holdout) fraction, evaluate on the NEWEST;
    never shuffled (shuffling leaks future team strength into the past).
  * only PRE-START lines (line_time <= start_time) ever enter a score (look-ahead defense;
    the backfill already guards this, the SQL re-guards it).
  * refuses to write an artifact when the fit fails (< CALIB_MIN_N pairs or single-class)
    or the holdout ECE exceeds FIT_MAX_HOLDOUT_ECE (a miscalibrated calibrator is worse
    than none — the signal would emit confident nonsense).

Run (operator's box, after the backfill):
  python -m esports_silo.scripts.fit_calibrator                    # fit + write artifact
  python -m esports_silo.scripts.fit_calibrator --dry-run          # report only, no write
  python -m esports_silo.scripts.fit_calibrator --games cs2 --holdout 0.2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..config import CONFIG
    from ..eval import skill_metrics
    from ..signal.sharp_consensus import PlattCalibrator, consensus_raw_score
except ImportError:  # loose-file fallback
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import CONFIG  # type: ignore
    from eval import skill_metrics  # type: ignore
    from signal.sharp_consensus import PlattCalibrator, consensus_raw_score  # type: ignore

import logging

log = logging.getLogger("fit_calibrator")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

MODEL_VERSION = "sharp_consensus_v1"          # must match pipeline.MODEL_VERSION
# Max holdout ECE the artifact may carry — above this the fit is refused outright.
FIT_MAX_HOLDOUT_ECE = float(os.getenv("FIT_MAX_HOLDOUT_ECE", "0.10"))


# ======================================================================================
# pure core (tested without a DB)
# ======================================================================================
def build_training_rows(odds_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flat (match, book) closing rows → one training row per match, time-sorted.

    Input rows: {match_id, book, team_a_odds, team_b_odds, line_time, winner, start_time,
    game} — already the LAST pre-start line per (match, book) (the SQL's DISTINCT ON).
    Output: {match_id, game, start_time, outcome, book_odds:[{book, team_a_odds, ...}]}.
    Matches whose winner isn't team_a/team_b are dropped (unsettled/void — no label).
    """
    by_match: Dict[str, Dict[str, Any]] = {}
    for r in odds_rows:
        winner = r.get("winner")
        if winner not in ("team_a", "team_b"):
            continue
        mid = str(r.get("match_id"))
        m = by_match.setdefault(mid, {
            "match_id": mid, "game": r.get("game"), "start_time": r.get("start_time"),
            "outcome": 1 if winner == "team_a" else 0, "book_odds": []})
        m["book_odds"].append({"book": r.get("book"), "team_a_odds": r.get("team_a_odds"),
                               "team_b_odds": r.get("team_b_odds"),
                               "line_time": r.get("line_time")})
    rows = [m for m in by_match.values() if m["book_odds"]]
    rows.sort(key=lambda m: str(m["start_time"]))  # ISO strings / datetimes both sort
    return rows


def time_split(rows: List[Dict[str, Any]], holdout_frac: float
               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Oldest (1−frac) → train, newest frac → holdout. NEVER shuffled (look-ahead)."""
    if not 0.0 < holdout_frac < 1.0:
        raise ValueError(f"holdout_frac must be in (0,1), got {holdout_frac}")
    cut = int(len(rows) * (1.0 - holdout_frac))
    return rows[:cut], rows[cut:]


def fit_and_eval(rows: List[Dict[str, Any]], holdout_frac: float = 0.2, min_books: int = 1
                 ) -> Tuple[PlattCalibrator, Dict[str, Any]]:
    """Fit on the train slice, evaluate on the time-ordered holdout. Pure.

    Returns (calibrator, report). report["holdout"] carries the skill_metrics summary where
    `market_price` is the RAW consensus score (baseline = "raw score as probability" — the
    only honest historical reference; NOT Polymarket, and it does NOT satisfy the gate).
    """
    rows = [r for r in rows if len(r["book_odds"]) >= min_books]
    train, hold = time_split(rows, holdout_frac)
    cal = PlattCalibrator().fit(
        [consensus_raw_score(r["book_odds"]).raw_score for r in train],
        [r["outcome"] for r in train])
    report: Dict[str, Any] = {
        "n_rows": len(rows), "n_train": len(train), "n_holdout": len(hold),
        "min_books": min_books,
        "train_range": [str(train[0]["start_time"]), str(train[-1]["start_time"])] if train else None,
        "holdout_range": [str(hold[0]["start_time"]), str(hold[-1]["start_time"])] if hold else None,
        "fitted": cal.fitted,
        "baseline_note": ("holdout 'market' baseline = RAW vig-kept consensus score, "
                          "NOT Polymarket; beating it does NOT pass the skill gate"),
    }
    if not cal.fitted:
        report["error"] = (f"calibrator refused to fit (n={cal.n} usable pairs; "
                           f"needs >= {os.getenv('CALIB_MIN_N', '50')} and both classes)")
        return cal, report
    preds = []
    for r in hold:
        score = consensus_raw_score(r["book_odds"]).raw_score
        p = cal.predict(score)
        if p is None:
            continue
        preds.append({
            "p_model": p, "outcome": r["outcome"], "game": r.get("game"),
            "market_price": score,                       # baseline: raw score AS IF a prob
            "team_a_odds": r["book_odds"][0].get("team_a_odds"),  # RAW closing (diagnostic)
        })
    sk = skill_metrics.evaluate(preds)
    report["holdout"] = {
        "n": sk.n, "model_brier": sk.model_brier, "model_log_loss": sk.model_log_loss,
        "model_ece": sk.model_ece, "model_auc": sk.model_auc,
        "raw_score_brier": sk.market_brier,
        "brier_vs_raw_score": sk.brier_skill,   # >0 ⇒ calibration adds forecasting value
        "per_game": sk.per_game, "reliability_bins": sk.reliability_bins,
    }
    report["holdout_summary"] = sk.summary()
    return cal, report


def make_artifact(cal: PlattCalibrator, report: Dict[str, Any], books: List[str],
                  games: List[str], fitted_at: str) -> Dict[str, Any]:
    """The JSON artifact the runner loads. Pure (fitted_at injected for testability)."""
    return {
        "model_version": MODEL_VERSION,
        "fitted_at": fitted_at,
        "books": books,
        "games": games,
        "calibrator": cal.to_dict(),
        "fit_report": {k: v for k, v in report.items() if k != "holdout_summary"},
        "halt_note": ("this artifact enables p_model EMISSION only; betting stays no_bet "
                      "until the FORWARD skill gate passes and SILO_ENTRY_HALT is lifted"),
    }


def refuse_reasons(cal: PlattCalibrator, report: Dict[str, Any]) -> List[str]:
    """Why the artifact must NOT be written (empty list = OK to write). Pure."""
    reasons = []
    if not cal.fitted:
        reasons.append(report.get("error") or "calibrator not fitted")
        return reasons
    h = report.get("holdout") or {}
    ece = h.get("model_ece")
    if h.get("n", 0) == 0:
        reasons.append("empty holdout — cannot check calibration out-of-time")
    elif ece is not None and ece > FIT_MAX_HOLDOUT_ECE:
        reasons.append(f"holdout ECE {ece:.4f} > FIT_MAX_HOLDOUT_ECE {FIT_MAX_HOLDOUT_ECE} "
                       "— a miscalibrated calibrator emits confident nonsense; refusing")
    return reasons


# ======================================================================================
# thin DB layer (operator's box)
# ======================================================================================
# Last PRE-START oddspapi line per (match, book) for labelled matches — the SQL re-guards
# the look-ahead even though the backfill already did (defense in depth).
_TRAINING_SQL = """
SELECT DISTINCT ON (o.match_id, o.book)
       o.match_id, o.book, o.team_a_odds, o.team_b_odds, o.line_time,
       m.winner, m.start_time, m.game
  FROM odds_raw o
  JOIN matches m USING (match_id)
 WHERE m.winner IN ('team_a','team_b')
   AND o.aggregator = 'pinnodds'
   AND o.book = ANY($1::text[])
   AND o.line_time <= m.start_time
   AND m.game = ANY($2::text[])
 ORDER BY o.match_id, o.book, o.line_time DESC
"""


async def run(games: List[str], books: List[str], holdout: float, min_books: int,
              out_path: str, dry_run: bool) -> int:
    import asyncpg
    if not CONFIG.database_url:
        raise SystemExit("DATABASE_URL not set")
    conn = await asyncpg.connect(CONFIG.database_url)
    try:
        rows = [dict(r) for r in await conn.fetch(_TRAINING_SQL, books, games)]
    finally:
        await conn.close()
    log.info("loaded %d (match, book) pre-start closing rows", len(rows))
    training = build_training_rows(rows)
    log.info("-> %d labelled matches with >=1 sharp line", len(training))

    cal, report = fit_and_eval(training, holdout_frac=holdout, min_books=min_books)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("holdout_summary",)}, indent=2, default=str))
    if report.get("holdout_summary"):
        print("\n" + report["holdout_summary"])
        print("\nREMINDER: this is a BACKFIT (baseline = raw score, not Polymarket). "
              "It fits; it does not prove. The forward gate is scripts/skill_report.py.")

    reasons = refuse_reasons(cal, report)
    if reasons:
        for r in reasons:
            log.error("artifact REFUSED: %s", r)
        return 1
    if dry_run:
        log.info("--dry-run: artifact NOT written (would go to %s)", out_path)
        return 0
    artifact = make_artifact(cal, report, books, games,
                             datetime.now(timezone.utc).isoformat())
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=2, default=str)
    log.info("artifact written -> %s (n_train=%d)", out_path, report["n_train"])
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="fit the Platt calibrator on backfilled closing lines")
    ap.add_argument("--games", default=",".join(CONFIG.games))
    ap.add_argument("--books", default=",".join(CONFIG.sharp_books),
                    help="books whose pre-start lines feed the consensus score")
    ap.add_argument("--holdout", type=float, default=0.2,
                    help="newest fraction held out for time-ordered evaluation")
    ap.add_argument("--min-books", type=int, default=1,
                    help="min sharp books per match (old archive rows are pinnacle-only)")
    ap.add_argument("--out", default=CONFIG.calibrator_path,
                    help="artifact path (runner reads CALIBRATOR_PATH)")
    ap.add_argument("--dry-run", action="store_true", help="fit + report only; write nothing")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(
        [g.strip() for g in args.games.split(",") if g.strip()],
        [b.strip().lower() for b in args.books.split(",") if b.strip()],
        args.holdout, args.min_books, args.out, args.dry_run)))


if __name__ == "__main__":
    main()
