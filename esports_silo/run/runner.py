#!/usr/bin/env python3
"""esports_silo — scheduled collection pass (item #9) + optional forward-prediction pass.

One pass = append-only odds collection + Polymarket snapshot collection + results settle.
Thin glue over the collectors' `run_once`; scheduled by the systemd timer / cron in
`esports_silo/deploy/`. Collectors need network + DB, so this runs on the operator's box.

--predict (explicit opt-in, off by default) additionally runs the prediction pass:
`pipeline.process_market` over the freshest Polymarket snapshots, writing a `predictions`
row per linkable market. This is what accumulates the FORWARD (p_model, market_price,
outcome) ledger the skill gate (`scripts/skill_report.py`) judges. It emits nothing until
`scripts/fit_calibrator.py` has written a fitted artifact (unfitted ⇒ p_model=None ⇒ rows
are skipped, loudly). Decisions remain `no_bet` throughout: `skill_proven` is hardcoded
False here (the gate hasn't passed — that is the point of collecting), and
`SILO_ENTRY_HALT` backstops it regardless.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("runner")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# Snapshots older than this are stale for prediction (the collector runs every 15 min).
PREDICT_SNAPSHOT_MAX_AGE_MIN = int(os.getenv("PREDICT_SNAPSHOT_MAX_AGE_MIN", "30"))
# Only pre-match, near-term matches are prediction candidates.
PREDICT_HORIZON_H = int(os.getenv("PREDICT_HORIZON_H", "48"))


async def run_pass(dry_run: bool) -> None:
    # lazy imports: collectors pull aiohttp/asyncpg; keep this module importable without them
    try:
        from ..collectors import odds_collector, polymarket_collector, results_collector
    except ImportError:
        from collectors import odds_collector, polymarket_collector, results_collector  # type: ignore

    # odds (the signal's input) → Polymarket snapshots (the comparison price) →
    # results (fills matches.winner so settled matches become scorable — unblocks the skill gate).
    await odds_collector.run_once(dry_run)
    await polymarket_collector.run_once(dry_run)
    await results_collector.run_once(dry_run)


# ======================================================================================
# forward-prediction pass (--predict)
# ======================================================================================
def load_signal(path: str):
    """Fitted SharpSignal from the calibrator artifact, or None (with the reason logged).

    Refuses artifacts whose model_version doesn't match the pipeline's — a refit under a
    new version must not silently write rows into the old version's forward ledger.
    """
    try:
        from ..pipeline import MODEL_VERSION
        from ..signal.sharp_consensus import PlattCalibrator, SharpSignal
    except ImportError:
        from pipeline import MODEL_VERSION  # type: ignore
        from signal.sharp_consensus import PlattCalibrator, SharpSignal  # type: ignore
    if not os.path.exists(path):
        log.warning("no calibrator artifact at %s — prediction pass skipped "
                    "(run scripts/fit_calibrator.py first)", path)
        return None
    try:
        with open(path) as f:
            art = json.load(f)
        if art.get("model_version") != MODEL_VERSION:
            log.error("artifact model_version=%r != pipeline %r — refusing to predict",
                      art.get("model_version"), MODEL_VERSION)
            return None
        cal = PlattCalibrator.from_dict(art.get("calibrator") or {})
    except (ValueError, OSError, json.JSONDecodeError) as e:
        log.error("calibrator artifact unreadable (%s) — prediction pass skipped", e)
        return None
    if not cal.fitted:
        log.warning("artifact calibrator is UNFITTED — prediction pass skipped (Cmd 4)")
        return None
    log.info("calibrator loaded (n=%d, fitted %s)", cal.n, art.get("fitted_at"))
    return SharpSignal(cal)


# freshest snapshot per market (the price side of every forward prediction)
_SNAPSHOTS_SQL = """
SELECT DISTINCT ON (market_id) market_id, question, game, yes_price, snapshot_time
  FROM polymarket_snapshots
 WHERE snapshot_time > now() - ($1 || ' minutes')::interval
 ORDER BY market_id, snapshot_time DESC
"""

# upcoming (pre-match) matches per game — the linker's candidate set
_UPCOMING_SQL = """
SELECT match_id, team_a, team_b, start_time, game FROM matches
 WHERE game = $1 AND start_time > now()
   AND start_time < now() + ($2 || ' hours')::interval
"""

# latest line per (match, book) for the sharp books (matches are pre-start, so all
# rows are pre-start; DISTINCT ON takes the freshest per book)
_ODDS_SQL = """
SELECT DISTINCT ON (match_id, book) match_id, book, team_a_odds, team_b_odds, line_time
  FROM odds_raw
 WHERE match_id = ANY($1::text[]) AND aggregator = 'oddspapi' AND book = ANY($2::text[])
 ORDER BY match_id, book, line_time DESC
"""


async def predict_pass(dry_run: bool) -> None:
    import asyncpg
    try:
        from ..config import CONFIG
        from ..markets.match_matcher import load_alias_map
        from .. import pipeline
    except ImportError:
        from config import CONFIG  # type: ignore
        from markets.match_matcher import load_alias_map  # type: ignore
        import pipeline  # type: ignore

    signal = load_signal(CONFIG.calibrator_path)
    if signal is None:
        return
    if not CONFIG.database_url:
        raise SystemExit("DATABASE_URL not set")
    books = [b.lower() for b in CONFIG.sharp_books]
    bankroll = float(os.getenv("SILO_BANKROLL_USD", "1000"))
    now = datetime.now(timezone.utc)
    written = skipped_uncal = skipped_unlinked = 0

    conn = await asyncpg.connect(CONFIG.database_url)
    try:
        snaps = [dict(r) for r in await conn.fetch(
            _SNAPSHOTS_SQL, str(PREDICT_SNAPSHOT_MAX_AGE_MIN))]
        log.info("prediction pass: %d fresh market snapshots", len(snaps))
        cache: dict = {}  # per-game: (candidates, odds_by_match, alias_map)
        for s in snaps:
            game = str(s.get("game") or "")
            if game not in CONFIG.games:
                continue
            if game not in cache:
                cands = [dict(r) for r in await conn.fetch(
                    _UPCOMING_SQL, game, str(PREDICT_HORIZON_H))]
                ids = [c["match_id"] for c in cands]
                odds_by_match: dict = {}
                if ids:
                    for o in await conn.fetch(_ODDS_SQL, ids, books):
                        odds_by_match.setdefault(str(o["match_id"]), []).append(dict(o))
                cache[game] = (cands, odds_by_match, await load_alias_map(conn, game))
            cands, odds_by_match, alias_map = cache[game]
            market = {"question": s.get("question") or "", "market_id": s.get("market_id"),
                      "yes_price": s.get("yes_price")}
            rec = pipeline.process_market(market, cands, odds_by_match, alias_map, signal,
                                          skill_proven=False, bankroll=bankroll)
            if rec is None:
                skipped_unlinked += 1
                continue
            if rec.p_model is None:            # no calibrated probability — nothing to score
                skipped_uncal += 1
                continue
            # pre-match only: a prediction at/after start is look-ahead and never written
            et = rec.event_time
            if not isinstance(et, datetime):
                try:
                    et = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    et = None
            if et is None or et <= now:
                log.warning("market %s linked to non-future match %s (start=%r) — not written",
                            rec.market_id, rec.match_id, rec.event_time)
                continue
            if dry_run:
                log.info("DRY predict %s match=%s p=%.4f price=%s decision=%s",
                         rec.market_id, rec.match_id, rec.p_model, rec.market_price,
                         rec.decision)
            else:
                await pipeline.write_prediction(conn, rec)
            written += 1
    finally:
        await conn.close()
    log.info("prediction pass: %d written%s, %d unlinked, %d uncalibrated "
             "(all decisions no_bet until the gate passes + halt lifted)",
             written, " (dry)" if dry_run else "", skipped_unlinked, skipped_uncal)


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo scheduled collection pass")
    ap.add_argument("--once", action="store_true", help="run a single pass then exit")
    ap.add_argument("--dry-run", action="store_true", help="probe only; no DB writes")
    ap.add_argument("--predict", action="store_true",
                    help="after collecting, run the forward-prediction pass (requires a "
                         "fitted calibrator artifact; decisions stay no_bet)")
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented; schedule it via the systemd timer / cron "
                         "in esports_silo/deploy/")
    asyncio.run(run_pass(dry_run=args.dry_run))
    if args.predict:
        asyncio.run(predict_pass(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
