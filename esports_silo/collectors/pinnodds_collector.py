#!/usr/bin/env python3
"""Append-only sharp-line collector for esports_silo — pinnodds (Pinnacle) source.

Replaces the OddsPapi collector. LIVE-VERIFIED 2026-07-06 on the box against the
real API + its /llms-full.txt reference:
  * base   https://pinnodds.com/kit/v1   auth header `x-portal-apikey: <key>`
  * ONE call `GET /markets?sport_id=11&event_type=prematch` returns EVERY esports
    event with odds embedded — no per-match fan-out (which tripped the ~20-req/window
    429 on the old OddsPapi two-call model).
  * event shape: {event_id, league_name, starts, home, away, is_have_odds,
    periods:{num_0:{money_line:{home,away,draw}}, num_1..: per-map}}. `num_0`
    ("Game") money_line = the MATCH-winner line; num_1.. are per-map. We take num_0.
  * odds are RAW decimal — stored as-is, NO de-vig (Cmd 2).

Game is in `league_name` (all esports share sport_id 11), so we classify it from
the league string; an unrecognised league is QUARANTINED (Cmd 4) — logged, never
written, never guessed.

Run:
  python -m esports_silo.collectors.pinnodds_collector --once --dry-run   # print current lines, no writes
  python -m esports_silo.collectors.pinnodds_collector --once             # append snapshots to odds_raw
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiohttp
import asyncpg
import structlog

try:
    from ..config import CONFIG, PINNODDS_ESPORTS_SPORT_ID
    from .sampling import should_poll, thresholds_crossed
except ImportError:  # allow running as a loose script
    from config import CONFIG, PINNODDS_ESPORTS_SPORT_ID  # type: ignore
    from sampling import should_poll, thresholds_crossed  # type: ignore

log = structlog.get_logger()

BOOK = "pinnacle"              # pinnodds forwards Pinnacle's own book, byte-identical
AGGREGATOR = "pinnodds"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
CLOSING_WINDOW_MIN = 30        # a line seen within 30 min of start is "closing"

# league_name -> silo game. Ordered; first hit wins. Verified sample league:
# "Valorant - Champions Tour: Game Changers LAN". Extend as new leagues appear
# (unknowns are logged, not silently dropped).
_GAME_MARKERS = (
    ("valorant", "valorant"),
    ("counter-strike", "cs2"), ("counter strike", "cs2"), ("cs2", "cs2"), ("cs:go", "cs2"), ("csgo", "cs2"),
    ("league of legends", "lol"), ("lol ", "lol"),
    ("dota", "dota2"),
    ("rocket league", "rl"),
    ("call of duty", "cod"), ("cod ", "cod"),
)


def classify_game(league_name: str) -> str | None:
    s = (league_name or "").lower()
    for marker, game in _GAME_MARKERS:
        if marker in s:
            return game
    return None


def _num0_moneyline(ev: dict) -> tuple[float | None, float | None]:
    """Match-winner (period 0) home/away decimal odds, or (None, None)."""
    per = (ev.get("periods") or {}).get("num_0") or {}
    ml = per.get("money_line") or {}
    home, away = ml.get("home"), ml.get("away")
    try:
        home = float(home) if home is not None else None
        away = float(away) if away is not None else None
    except (TypeError, ValueError):
        return None, None
    # sanity: decimal odds are > 1.0; anything else is not a usable price
    if home is not None and home <= 1.0:
        home = None
    if away is not None and away <= 1.0:
        away = None
    return home, away


async def _fetch_esports(session: aiohttp.ClientSession) -> list[dict]:
    """Single GET of all prematch esports events. Returns [] on any failure (never raises)."""
    url = f"{CONFIG.pinnodds_base}/markets"
    params = {"sport_id": PINNODDS_ESPORTS_SPORT_ID, "event_type": "prematch"}
    headers = {"x-portal-apikey": CONFIG.pinnodds_api_key, "accept": "application/json", "user-agent": UA}
    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.status == 429:
                log.warning("pinnodds rate limited (429) — back off; ~20 req/window")
                return []
            if r.status != 200:
                log.warning("pinnodds non-200", status=r.status, body=(await r.text())[:200])
                return []
            data = await r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("pinnodds request failed", error=str(e))
        return []
    events = data.get("events") if isinstance(data, dict) else data
    return events if isinstance(events, list) else []


def _is_closing(line_time: datetime, start_time: datetime) -> bool:
    try:
        return timedelta(0) <= (start_time - line_time) <= timedelta(minutes=CLOSING_WINDOW_MIN)
    except Exception:  # noqa: BLE001
        return False


def _parse_start(v):
    if isinstance(v, datetime):
        return v
    try:
        st = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return st if st.tzinfo else st.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def run_once(dry_run: bool, poll_all: bool = False) -> None:
    if not CONFIG.pinnodds_api_key:
        raise SystemExit("PINNODDS_API_KEY not set")
    pool = None
    if not dry_run:
        if not CONFIG.database_url:
            raise SystemExit("DATABASE_URL not set (or use --dry-run)")
        pool = await asyncpg.create_pool(CONFIG.database_url, min_size=1, max_size=2)

    now = datetime.now(timezone.utc)
    coverage: dict[str, int] = {}       # game -> usable match-winner lines
    unknown_leagues: dict[str, int] = {}
    no_line = 0
    sampled_out = 0
    printed = []                         # dry-run display rows

    async with aiohttp.ClientSession() as session:
        events = await _fetch_esports(session)
    log.info("pinnodds esports events", n=len(events))

    for ev in events:
        league = str(ev.get("league_name") or "")
        game = classify_game(league)
        if game is None:
            unknown_leagues[league] = unknown_leagues.get(league, 0) + 1
            continue
        if game not in CONFIG.games:
            continue  # game not in scope
        home_odds, away_odds = _num0_moneyline(ev)
        if home_odds is None or away_odds is None:
            no_line += 1
            continue
        start_time = _parse_start(ev.get("starts"))
        if start_time is None:
            log.warning("event missing/unparseable start — skipped",
                        event_id=ev.get("event_id"), starts=ev.get("starts"))
            continue

        event_id = ev.get("event_id")
        match_id = f"pinnodds_{event_id}"
        team_a = str(ev.get("home") or "?")   # pinnodds home -> silo team_a (self-consistent)
        team_b = str(ev.get("away") or "?")
        coverage[game] = coverage.get(game, 0) + 1

        # CLOSING-LINE SAMPLING (default): write a snapshot only when a time-to-start
        # threshold is newly due; odds_raw is the ledger. --poll-all bypasses.
        if not poll_all:
            if dry_run:
                if not thresholds_crossed(start_time, now):
                    sampled_out += 1
                    # still show it in dry-run so "current lines" is complete
            else:
                async with pool.acquire() as con:
                    lts = [r["line_time"] for r in await con.fetch(
                        "SELECT line_time FROM odds_raw WHERE match_id=$1", match_id)]
                due, _bucket = should_poll(start_time, now, lts)
                if not due:
                    sampled_out += 1
                    continue

        if dry_run:
            printed.append((game, team_a, team_b, home_odds, away_odds, start_time))
            continue

        async with pool.acquire() as con:
            await con.execute(
                """INSERT INTO matches (match_id, game, team_a, team_b, start_time, source)
                   VALUES ($1,$2,$3,$4,$5,'pinnodds')
                   ON CONFLICT (match_id) DO NOTHING""",
                match_id, game, team_a, team_b, start_time,
            )
            # APPEND-ONLY: plain INSERT, never UPSERT — preserves line movement.
            await con.execute(
                """INSERT INTO odds_raw
                   (match_id, book, aggregator, team_a_odds, team_b_odds, is_closing, line_time)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                match_id, BOOK, AGGREGATOR, home_odds, away_odds,
                _is_closing(now, start_time), now,
            )

    if pool:
        await pool.close()

    # ── report ──
    if dry_run and printed:
        print("\n=== current esports lines (pinnacle via pinnodds, RAW decimal) ===")
        print(f"{'game':<9}{'match':<44}{'home':>8}{'away':>8}   start (UTC)")
        for game, a, b, ho, ao, st in sorted(printed, key=lambda r: (r[0], r[5])):
            match = f"{a} vs {b}"[:42]
            print(f"{game:<9}{match:<44}{ho:>8.3f}{ao:>8.3f}   {st:%Y-%m-%d %H:%M}")
    log.info("=== coverage (game) -> usable match-winner lines ===")
    for game in CONFIG.games:
        n = coverage.get(game, 0)
        (log.info if n else log.warning)("coverage", game=game, lines=n)
    if no_line:
        log.info("events with no usable num_0 money_line (skipped)", n=no_line)
    if sampled_out and not dry_run:
        log.info("sampling: events skipped as not-due this run", skipped=sampled_out)
    if unknown_leagues:
        log.warning("UNRECOGNISED leagues (quarantined — extend _GAME_MARKERS)",
                    leagues=json.dumps(unknown_leagues)[:600])


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo pinnodds (Pinnacle) odds collector")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--dry-run", action="store_true", help="print current lines, no DB writes")
    ap.add_argument("--poll-all", action="store_true",
                    help="bypass closing-line sampling (snapshot every event)")
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented in the scaffold; schedule via cron/timer")
    asyncio.run(run_once(dry_run=args.dry_run, poll_all=args.poll_all))


if __name__ == "__main__":
    main()
