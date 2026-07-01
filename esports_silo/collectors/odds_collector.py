#!/usr/bin/env python3
"""Append-only sharp-line collector for esports_silo.

Pulls esports odds from ONE aggregator (default: OddsPapi) for the configured
books (Pinnacle + Circa + one Asian book) and writes RAW decimal odds to
`odds_raw`. INSERT-only — a re-observation of the same match/book is a NEW row,
so line movement is preserved and nothing is ever mutated.

Enforced here:
  * odds stored RAW — NO de-vig.
  * per-(game, book) coverage is logged EVERY run. A requested book that returns
    nothing is a loud WARNING, never a silent gap (defends the prior
    "assumed the aggregator had coverage" failure).

Run:
  python -m esports_silo.collectors.odds_collector --once
  python -m esports_silo.collectors.odds_collector --once --dry-run   # probe only, no writes

SEAM (could not be verified from a network-isolated session): the exact odds
endpoint path and the field names in its JSON response. `_fetch_odds` logs the
first raw payload it sees so you can confirm the mapping against a live response
before trusting the parsed values. Fixtures use the verified /fixtures shape.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp
import asyncpg
import structlog

try:
    from ..config import CONFIG, ODDSPAPI_SPORT_IDS
except ImportError:  # allow running as a loose script
    from config import CONFIG, ODDSPAPI_SPORT_IDS  # type: ignore

log = structlog.get_logger()

BASE_URL = "https://api.oddspapi.io/v4"
MIN_INTERVAL_S = 5.5           # verified: OddsPapi historical-odds 5s cooldown + margin
CLOSING_WINDOW_MIN = 30        # a line seen within 30 min of start_time is "closing"


async def _get(session: aiohttp.ClientSession, path: str, params: dict):
    """Rate-limited GET. Returns parsed JSON or None. Never raises."""
    url = f"{BASE_URL}{path}"
    params = {**params, "apiKey": CONFIG.oddspapi_api_key}  # confirm auth param vs docs
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 402:
                log.warning("aggregator quota exhausted (free tier is small)")
                return None
            if r.status == 429:
                log.warning("aggregator rate limited")
                return None
            if r.status != 200:
                log.warning("aggregator non-200", path=path, status=r.status)
                return None
            return await r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("aggregator request failed", path=path, error=str(e))
        return None
    finally:
        await asyncio.sleep(MIN_INTERVAL_S)


async def _fetch_fixtures(session, game: str) -> list[dict]:
    """Verified shape: /fixtures?sport_id=<id>. Returns list of fixture dicts."""
    sid = ODDSPAPI_SPORT_IDS.get(game)
    if sid is None:
        return []
    data = await _get(session, "/fixtures", {"sport_id": sid, "days_back": 3})
    if not data:
        return []
    # Response is either a list or {"data"/"fixtures": [...]}; handle defensively.
    if isinstance(data, dict):
        data = data.get("data") or data.get("fixtures") or []
    return data if isinstance(data, list) else []


_logged_shape = False


async def _fetch_odds(session, fixture: dict, books: list[str]) -> list[dict]:
    """SEAM: fetch per-book odds for a fixture.

    Returns normalized rows: {book, team_a_odds, team_b_odds, line_time}.
    The odds endpoint path + field names are UNVERIFIED — the first raw payload
    is logged so you can confirm the mapping. Until confirmed, treat parsed
    values as provisional.
    """
    global _logged_shape
    fixture_id = fixture.get("id") or fixture.get("fixture_id")
    if fixture_id is None:
        return []
    data = await _get(session, "/odds", {"fixture_id": fixture_id})  # VERIFY path
    if not data:
        return []
    if not _logged_shape:
        log.info("first raw odds payload — CONFIRM field mapping", sample=str(data)[:800])
        _logged_shape = True

    entries = data.get("data") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for e in entries:
        book = str(e.get("bookmaker") or e.get("book") or "").lower()
        if books and book not in books:
            continue
        rows.append(
            {
                "book": book,
                "team_a_odds": e.get("home_odds") or e.get("team_a_odds"),
                "team_b_odds": e.get("away_odds") or e.get("team_b_odds"),
                "line_time": e.get("updated_at") or e.get("line_time") or now,
            }
        )
    return rows


def _is_closing(line_time, start_time) -> bool:
    try:
        lt = line_time if isinstance(line_time, datetime) else datetime.fromisoformat(str(line_time).replace("Z", "+00:00"))
        st = start_time if isinstance(start_time, datetime) else datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        return st - lt <= timedelta(minutes=CLOSING_WINDOW_MIN)
    except Exception:  # noqa: BLE001
        return False


async def run_once(dry_run: bool) -> None:
    books = [b.lower() for b in CONFIG.sharp_books]
    coverage: dict[tuple[str, str], int] = {}
    pool = None
    if not dry_run:
        if not CONFIG.database_url:
            raise SystemExit("DATABASE_URL not set (or use --dry-run)")
        pool = await asyncpg.create_pool(CONFIG.database_url, min_size=1, max_size=2)

    async with aiohttp.ClientSession() as session:
        for game in CONFIG.games:
            fixtures = await _fetch_fixtures(session, game)
            log.info("fixtures", game=game, n=len(fixtures))
            for fx in fixtures:
                match_id = str(fx.get("id") or fx.get("fixture_id") or "")
                if not match_id:
                    continue
                start_time = fx.get("start_time") or fx.get("begin_at")
                odds_rows = await _fetch_odds(session, fx, books)
                for o in odds_rows:
                    coverage[(game, o["book"])] = coverage.get((game, o["book"]), 0) + 1
                    if dry_run:
                        continue
                    async with pool.acquire() as con:
                        await con.execute(
                            """INSERT INTO matches (match_id, game, team_a, team_b, start_time, source)
                               VALUES ($1,$2,$3,$4,$5,'aggregator')
                               ON CONFLICT (match_id) DO NOTHING""",
                            match_id, game,
                            str(fx.get("home") or fx.get("team_a") or "?"),
                            str(fx.get("away") or fx.get("team_b") or "?"),
                            start_time,
                        )
                        # APPEND-ONLY: plain INSERT, never UPSERT.
                        await con.execute(
                            """INSERT INTO odds_raw
                               (match_id, book, aggregator, team_a_odds, team_b_odds, is_closing, line_time)
                               VALUES ($1,$2,'oddspapi',$3,$4,$5,$6)""",
                            match_id, o["book"], o["team_a_odds"], o["team_b_odds"],
                            _is_closing(o["line_time"], start_time), o["line_time"],
                        )
    if pool:
        await pool.close()

    # Coverage report — the #1 risk made observable every run.
    log.info("=== coverage (game, book) -> observations ===")
    for game in CONFIG.games:
        for book in books:
            n = coverage.get((game, book), 0)
            (log.info if n else log.warning)("coverage", game=game, book=book, observations=n)
    missing = [f"{g}/{b}" for g in CONFIG.games for b in books if not coverage.get((g, b))]
    if missing:
        log.warning("BOOKS WITH ZERO COVERAGE — verify aggregator before trusting", missing=missing)


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo append-only odds collector")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--dry-run", action="store_true", help="probe coverage, no DB writes")
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented in the scaffold; schedule via cron/timer")
    asyncio.run(run_once(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
