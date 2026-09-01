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
            condition_id, yes_token_id, yes_outcome, market_price,
            best_bid, best_ask, bid_size, ask_size}.

The last four are the matched Polymarket match-winner reference (GAP B): the
bet-time PM price + the condition_id/yes_token_id needed for the flip-proof
orientation backfill. They enable the real ``edge = sharp_prob - PM_price``
backtest (``sharp_eval.edge_backtest``). They are ``None`` when no clean,
unambiguous PM match-winner market matches the odds row — correct-or-absent, and
never blocks odds collection (a Gamma failure just yields null PM fields).

Env:
  PINNACLE_ODDS_API_KEY   — PinnOdds key (already in /opt/pa2-shared/.env)
  PINNODDS_SNAPSHOT_PATH  — output JSONL (default: data/odds/pinnodds_snapshots.jsonl)
  EB_ALIASES_PATH         — optional aliases.json (esports_team_aliases dump, see
                            deploy/vps/eb_dump_aliases.sh); absent/malformed ->
                            matching runs exactly as before (correct-or-absent)

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

from esports_v2.data.alias_file import load_alias_expand
from esports_v2.data.pinnodds_loader import PinnOddsLoader
from esports_v2.data.pm_market_index import (
    PMMarketRef,
    TouchQuote,
    build_pm_index,
    fetch_touch_quotes,
    match_pm_ref,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("collect_pinnodds")

_DEFAULT_PATH = "data/odds/pinnodds_snapshots.jsonl"


def build_snapshot_records(
    rows: List[dict],
    captured_at: str,
    pm_index: Optional[Dict[str, PMMarketRef]] = None,
    touch_quotes: Optional[Dict[str, TouchQuote]] = None,
) -> List[dict]:
    """Stamp each fetched row with the capture time, and attach the matched
    Polymarket match-winner reference (GAP B) when one exists. Pure —
    unit-testable. ``pm_index`` maps ``match_key -> PMMarketRef``; a row with no
    match keeps ``None`` PM fields (correct-or-absent).

    ``touch_quotes`` (GAP C, 2026-07-13) maps ``yes_token_id -> TouchQuote``
    from the live CLOB book — the EXECUTABLE prices (``market_price`` is the
    mid by construction). A matched row whose book fetch failed/was empty keeps
    all four quote fields ``None``; unmatched rows always do."""
    pm_index = pm_index or {}
    touch_quotes = touch_quotes or {}
    out = []
    for r in rows:
        pm = pm_index.get(r.get("match_key"))
        tq = touch_quotes.get(pm.yes_token_id) if pm else None
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
            "best_bid": tq.best_bid if tq else None,
            "best_ask": tq.best_ask if tq else None,
            "bid_size": tq.bid_size if tq else None,
            "ask_size": tq.ask_size if tq else None,
        })
    return out


def append_jsonl(records: List[dict], path: Path) -> int:
    """Append records as JSON lines. Append-only — never rewrites history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def _safe_build_pm_refs() -> List[PMMarketRef]:
    """Build the PM match-winner ref list, best-effort. Any failure -> [] so PM
    enrichment is simply skipped this tick and odds collection never regresses
    (correct-or-absent: null PM fields beat a wrong/crashing capture)."""
    try:
        refs = build_pm_index()
        logger.info(f"pm_index refs={len(refs)}")
        return refs
    except Exception as e:  # noqa: BLE001 — never let PM capture break odds collection
        logger.warning(f"pm_index_failed err={type(e).__name__} — writing null PM fields")
        return []


def _resolve_pm_index(
    rows: List[dict], refs: List[PMMarketRef], alias_expand=None
) -> Dict[str, PMMarketRef]:
    """Match each odds row to its PM market via the conservative bijective matcher
    (``match_pm_ref``), keyed by the row's match_key. Unmatched/ambiguous rows are
    simply absent -> null PM fields (correct-or-absent)."""
    out: Dict[str, PMMarketRef] = {}
    for r in rows:
        ref = match_pm_ref(r.get("home"), r.get("away"), r.get("starts"), refs,
                           alias_expand=alias_expand)
        if ref is not None and r.get("match_key"):
            out[r["match_key"]] = ref
    return out


def collect(path: Path) -> Dict[str, int]:
    import time
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.monotonic()
    loader = PinnOddsLoader.from_env()
    # LATENCY: the odds fetch and the PM index build are both pure I/O — run
    # them concurrently. Halves tick wall-time and captures market_price closer
    # in time to the odds it is stored beside (price/line simultaneity).
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_refs = ex.submit(_safe_build_pm_refs)
        fut_rows = ex.submit(loader.fetch_rows, event_types=("live", "prematch"))
        refs = fut_refs.result()
        rows = fut_rows.result()
    alias_expand = load_alias_expand(os.environ.get("EB_ALIASES_PATH"))
    pm_index = _resolve_pm_index(rows, refs, alias_expand)
    # GAP C: executable prices — one CLOB book read per distinct matched market
    # (touch quotes are useless without a match; failures -> null quote fields,
    # never a blocked tick).
    try:
        quotes = fetch_touch_quotes(pm.yes_token_id for pm in pm_index.values())
    except Exception as e:  # noqa: BLE001 — quotes must never break odds capture
        logger.warning(f"touch_quotes_failed err={type(e).__name__} — null quote fields")
        quotes = {}
    captured_at = datetime.now(timezone.utc).isoformat()
    records = build_snapshot_records(rows, captured_at, pm_index, quotes)
    matched = sum(1 for r in records if r.get("condition_id"))
    quoted = sum(1 for r in records if r.get("best_bid") is not None
                 or r.get("best_ask") is not None)
    written = append_jsonl(records, path)
    total = sum(1 for _ in open(path, encoding="utf-8")) if path.exists() else written
    logger.info(f"collected snapshots={written} pm_matched={matched} books={quoted} "
                f"captured_at={captured_at} dur={time.monotonic() - t0:.1f}s "
                f"file={path} total_lines={total}")
    return {"written": written, "total_lines": total, "pm_matched": matched,
            "books": quoted}


if __name__ == "__main__":
    out_path = Path(os.environ.get("PINNODDS_SNAPSHOT_PATH", _DEFAULT_PATH))
    result = collect(out_path)
    print(f"appended {result['written']} snapshots "
          f"(pm_matched {result.get('pm_matched', 0)}) -> {out_path} "
          f"(total {result['total_lines']} lines)")
