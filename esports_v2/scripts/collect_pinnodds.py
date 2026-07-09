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
            odds_a, odds_b, event_type}.

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
from typing import Dict, List

from esports_v2.data.pinnodds_loader import PinnOddsLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("collect_pinnodds")

_DEFAULT_PATH = "data/odds/pinnodds_snapshots.jsonl"


def build_snapshot_records(rows: List[dict], captured_at: str) -> List[dict]:
    """Stamp each fetched row with the capture time. Pure — unit-testable."""
    out = []
    for r in rows:
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
        })
    return out


def append_jsonl(records: List[dict], path: Path) -> int:
    """Append records as JSON lines. Append-only — never rewrites history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def collect(path: Path) -> Dict[str, int]:
    loader = PinnOddsLoader.from_env()
    rows = loader.fetch_rows(event_types=("live", "prematch"))
    captured_at = datetime.now(timezone.utc).isoformat()
    records = build_snapshot_records(rows, captured_at)
    written = append_jsonl(records, path)
    total = sum(1 for _ in open(path, encoding="utf-8")) if path.exists() else written
    logger.info(f"collected snapshots={written} captured_at={captured_at} "
                f"file={path} total_lines={total}")
    return {"written": written, "total_lines": total}


if __name__ == "__main__":
    out_path = Path(os.environ.get("PINNODDS_SNAPSHOT_PATH", _DEFAULT_PATH))
    result = collect(out_path)
    print(f"appended {result['written']} snapshots -> {out_path} "
          f"(total {result['total_lines']} lines)")
