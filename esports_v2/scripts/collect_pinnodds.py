#!/usr/bin/env python3
"""Forward-collect PinnOdds esports match-winner lines into a JSONL snapshot log.

WHY: there is no cheap/clean source of HISTORICAL Pinnacle esports closing lines
(B13). So we collect the LIVE line forward: run this on a schedule (cron) and it
appends one timestamped snapshot per match each run. Over the days before a match
the snapshots capture the line's movement; the CLOSING line is later derived as
the last snapshot with captured_at <= starts. Paired with the free match-results
data we already have, that yields a real backtest set over time.

Storage is an append-only JSONL — deliberately DECOUPLED from the DB:
  - the `esports_odds` table FKs match_id -> esports_matches and has no team-name
    column, so it can't hold odds for not-yet-ingested upcoming matches;
  - JSONL needs no migration, no FK, no shared-prod-DB write, and matches the
    match_key contract the sharp signal already consumes.

Each line: {captured_at, match_key, home, away, starts, league_name,
            odds_a, odds_b, event_type,
            condition_id, yes_token_id, yes_outcome, market_price}.

The last four are the matched Polymarket match-winner reference (GAP B): the
bet-time PM price + the condition_id/yes_token_id needed for the flip-proof
orientation backfill. They enable the real ``edge = sharp_prob - PM_price``
backtest (``sharp_eval.edge_backtest``). They are ``None`` when no clean,
unambiguous PM match-winner market matches the odds row — correct-or-absent, and
never blocks odds collection (a Gamma failure just yields null PM fields).

Env:
  PINNACLE_ODDS_API_KEY   — PinnOdds key (already in /opt/pa2-shared/.env)
  PINNODDS_SNAPSHOT_PATH  — output JSONL (default: data/odds/pinnodds_snapshots.jsonl)

Usage (one shot; schedule via cron/systemd-timer, e.g. every 15 min):
    python -m esports_v2.scripts.collect_pinnodds
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from esports_v2.data.pinnodds_loader import PinnOddsLoader
from esports_v2.data.pm_market_index import PMMarketRef, build_pm_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("collect_pinnodds")

_DEFAULT_PATH = "data/odds/pinnodds_snapshots.jsonl"


def build_snapshot_records(
    rows: List[dict],
    captured_at: str,
    pm_index: Optional[Dict[str, PMMarketRef]] = None,
) -> List[dict]:
    """Stamp each fetched row with the capture time, and attach the matched
    Polymarket match-winner reference (GAP B) when one exists. Pure —
    unit-testable. ``pm_index`` maps ``match_key -> PMMarketRef``; a row with no
    match keeps ``None`` PM fields (correct-or-absent)."""
    pm_index = pm_index or {}
    out = []
    for r in rows:
        pm = pm_index.get(r.get("match_key"))
        out.append({
            "captured_at": captured_at,
            "match_key": r.get("match_key"),
            "home": r.get("home"),
            "away": r.get("away"),
            "starts": r.get("starts"),
            "league_name": r.get("league_name"),
            "odds_a": r.get("odds_a"),
            "odds_b": r.get("odds_b"),
            "event_type": r.get("event_type"),
            "condition_id": pm.condition_id if pm else None,
            "yes_token_id": pm.yes_token_id if pm else None,
            "yes_outcome": pm.yes_outcome if pm else None,
            "market_price": pm.market_price if pm else None,
        })
    return out


def append_jsonl(records: List[dict], path: Path) -> int:
    """Append records as JSON lines. Append-only — never rewrites history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def _safe_build_pm_index() -> Dict[str, PMMarketRef]:
    """Build the PM match-winner index, best-effort. Any failure -> {} so PM
    enrichment is simply skipped this tick and odds collection never regresses
    (correct-or-absent: null PM fields beat a wrong/crashing capture)."""
    try:
        idx = build_pm_index()
        logger.info(f"pm_index keys={len(idx)}")
        return idx
    except Exception as e:  # noqa: BLE001 — never let PM capture break odds collection
        logger.warning(f"pm_index_failed err={type(e).__name__} — writing null PM fields")
        return {}


def collect(path: Path) -> Dict[str, int]:
    loader = PinnOddsLoader.from_env()
    rows = loader.fetch_rows(event_types=("live", "prematch"))
    pm_index = _safe_build_pm_index()
    captured_at = datetime.now(timezone.utc).isoformat()
    records = build_snapshot_records(rows, captured_at, pm_index)
    matched = sum(1 for r in records if r.get("condition_id"))
    written = append_jsonl(records, path)
    total = sum(1 for _ in open(path, encoding="utf-8")) if path.exists() else written
    logger.info(f"collected snapshots={written} pm_matched={matched} "
                f"captured_at={captured_at} file={path} total_lines={total}")
    return {"written": written, "total_lines": total, "pm_matched": matched}


if __name__ == "__main__":
    out_path = Path(os.environ.get("PINNODDS_SNAPSHOT_PATH", _DEFAULT_PATH))
    result = collect(out_path)
    print(f"appended {result['written']} snapshots "
          f"(pm_matched {result.get('pm_matched', 0)}) -> {out_path} "
          f"(total {result['total_lines']} lines)")
