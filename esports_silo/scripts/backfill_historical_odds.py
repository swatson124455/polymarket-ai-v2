#!/usr/bin/env python3
"""esports_silo — one-pass historical closing-line backfill from OddsPapi.

WHY: the archive lets us attach closing sharp lines to the already-labelled silo matches
and fit the calibrator on historical (score, outcome) pairs, while the skill gate still
proves on FORWARD data (a backfit fits; it does not prove).

⚠ DEPTH CAVEAT (operator-ruled 2026-07-05): the ≥6-month depth verification (2026-07-03,
BLAST fixture id1703162167643464, Fnatic vs Astralis) was measured on PINNACLE — which
OddsPapi carries on B2B plans ONLY and is therefore OUT of our book set (D3). Depth for
singbet/sbobet is UNVERIFIED. Probe with a small --max-requests first; if old matches
return ~nothing, skip the full sweep and fit the calibrator on forward-collected lines.

LIVE-VERIFIED caveats baked in (do not "fix" these away):
  * Old fixtures can be down-sampled to a single pre-start tick hours before start
    (Fnatic/Astralis: one tick at T-3h26m, then in-play only). We store the honest
    line_time; `is_closing` uses the same 30-min rule as the forward collector — a stale
    tick is NOT flagged as closing.
  * Soft books age out fast (~2 weeks — 1xbet/22bet returned nothing for January); in the
    probe only pinnacle survived months, and pinnacle is NOT on our plan. Expect thin or
    zero coverage on old rows until singbet/sbobet depth is measured.
  * Most archived points are IN-PLAY — the parser's look-ahead guard (last price at/before
    startTime) is what makes the data usable at all.

QUOTA: free tier is 250 req/month and every fixture costs ONE /historical-odds request
(plus one /fixtures request per ≤10-day window per game). `--max-requests` is a hard cap
(default 40). The run is RESUMABLE: fixtures whose linked match already has a pre-start
oddspapi row are skipped, so re-running the same command continues where quota stopped.

LINKING: a fixture only spends quota if it links to an existing LABELLED silo match
(winner set) — those are the calibrator's training rows. Linking canonicalizes names via
`team_aliases` then requires BOTH teams ≥ FUZZY_THRESHOLD in one orientation; if the
better orientation is swapped, the odds are swapped before writing so team_a_odds always
refers to the silo match's team_a. Unlinked fixtures are logged (and only pulled with
--include-unlinked, which inserts a new matches row keyed by the fixtureId).

Run (operator's box):
  python -m esports_silo.scripts.backfill_historical_odds --from 2026-01-01 --to 2026-01-20 \
      --games cs2 --max-requests 40            # real run (needs DATABASE_URL + key)
  ... --dry-run                                # no DB writes; STILL SPENDS API REQUESTS
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from ..config import CONFIG, ODDSPAPI_SPORT_IDS
    from ..markets.match_matcher import FUZZY_THRESHOLD, fuzzy_ratio
    from .probe_historical_odds import _get, _ts, parse_historical_odds
except ImportError:  # loose-file fallback (tests import the pure parts only)
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import CONFIG, ODDSPAPI_SPORT_IDS  # type: ignore
    from markets.match_matcher import FUZZY_THRESHOLD, fuzzy_ratio  # type: ignore
    from scripts.probe_historical_odds import _get, _ts, parse_historical_odds  # type: ignore

log = logging.getLogger("backfill_historical_odds")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

CLOSING_WINDOW_MIN = 30      # same rule as collectors/odds_collector.py — keep consistent
WINDOW_SPAN_DAYS = 9         # LIVE-VERIFIED: /fixtures from/to "must be under 10 days apart"
CANDIDATE_WINDOW_H = 24      # link candidates: silo matches within ±24h of fixture start


# ======================================================================================
# pure: window enumeration
# ======================================================================================
def windows(date_from: str, date_to: str, span_days: int = WINDOW_SPAN_DAYS
            ) -> List[Tuple[str, str]]:
    """[from, to] → consecutive (from, to) chunks each ≤ span_days apart (API hard limit).

    Chunks step by span_days and share boundary days; callers dedupe by fixtureId.
    """
    f = date.fromisoformat(date_from)
    t = date.fromisoformat(date_to)
    if t < f:
        raise ValueError(f"--to {date_to} is before --from {date_from}")
    out: List[Tuple[str, str]] = []
    cur = f
    while cur <= t:
        end = min(cur + timedelta(days=span_days), t)
        out.append((str(cur), str(end)))
        if end == t:
            break
        cur = end
    return out


# ======================================================================================
# pure: fixture → silo-match linking
# ======================================================================================
def canon(name: str, alias_to_canon: Dict[str, str]) -> str:
    n = (name or "").strip().lower()
    return alias_to_canon.get(n, n)


def team_score(a: str, b: str, alias_to_canon: Dict[str, str]) -> float:
    """100 when the canonicalized names are equal; else the matcher's fuzzy ratio."""
    ca, cb = canon(a, alias_to_canon), canon(b, alias_to_canon)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 100.0
    return fuzzy_ratio(ca, cb)


def link_fixture(fixture: Dict[str, Any], candidates: List[Dict[str, Any]],
                 alias_to_canon: Dict[str, str], threshold: float = FUZZY_THRESHOLD
                 ) -> Optional[Dict[str, Any]]:
    """Best labelled silo match for a fixture, or None.

    Returns {"match_id", "swapped", "score", "delta_s"}. `swapped=True` means the silo
    match's team_a is the fixture's participant2 — the caller MUST swap the parsed odds
    (outcome 171 is ALWAYS participant1) or every backfilled row is inverted.
    Rank: score first, then time proximity (defends same-teams rematches days apart).
    """
    p1 = str(fixture.get("participant1Name") or "")
    p2 = str(fixture.get("participant2Name") or "")
    fx_start = _ts(fixture.get("startTime"))
    best: Optional[Dict[str, Any]] = None
    for c in candidates:
        ta, tb = str(c.get("team_a") or ""), str(c.get("team_b") or "")
        direct = min(team_score(p1, ta, alias_to_canon), team_score(p2, tb, alias_to_canon))
        swapped = min(team_score(p1, tb, alias_to_canon), team_score(p2, ta, alias_to_canon))
        score, is_swapped = (direct, False) if direct >= swapped else (swapped, True)
        if score < threshold:
            continue
        c_start = _ts(c.get("start_time"))
        delta_s = abs((fx_start - c_start).total_seconds()) if fx_start and c_start else float("inf")
        cand = {"match_id": str(c.get("match_id")), "swapped": is_swapped,
                "score": score, "delta_s": delta_s}
        if best is None or (cand["score"], -cand["delta_s"]) > (best["score"], -best["delta_s"]):
            best = cand
    return best


# ======================================================================================
# pure: parsed rows → insertable odds_raw rows
# ======================================================================================
def shape_rows(parsed: List[Dict[str, Any]], books: List[str], swapped: bool,
               start_time: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(writable_rows, error_rows). Applies the orientation swap and the honest
    is_closing rule (line within CLOSING_WINDOW_MIN of start — same as the forward
    collector; a T-3h archived tick is stored with is_closing=False, never dressed up).
    """
    st = _ts(start_time)
    ok: List[Dict[str, Any]] = []
    bad: List[Dict[str, Any]] = []
    for r in parsed:
        book = str(r.get("book") or "").lower()
        if books and book not in books:
            continue
        if r.get("error"):
            bad.append(r)
            continue
        lt = _ts(r.get("line_time"))
        if lt is None or st is None:   # line_time is NOT NULL — skip loudly, never guess
            bad.append({**r, "error": "unparseable line_time/start_time"})
            continue
        a, b = r.get("team_a_odds"), r.get("team_b_odds")
        if swapped:
            a, b = b, a
        ok.append({
            "book": book, "team_a_odds": a, "team_b_odds": b, "line_time": lt,
            "is_closing": (st - lt) <= timedelta(minutes=CLOSING_WINDOW_MIN),
            "stale_min": max(0.0, (st - lt).total_seconds() / 60.0),
        })
    return ok, bad


# ======================================================================================
# I/O (operator's box)
# ======================================================================================
async def _fetch_window_fixtures(session, game: str, w_from: str, w_to: str) -> List[dict]:
    sid = ODDSPAPI_SPORT_IDS.get(game)
    if sid is None:
        return []
    data, status = await _get(session, "/fixtures", {"sportId": sid, "from": w_from, "to": w_to})
    if not data:
        log.warning("fixtures window %s..%s (%s): HTTP %s — window skipped", w_from, w_to, game, status)
        return []
    if isinstance(data, dict):
        data = data.get("data") or data.get("fixtures") or []
    return data if isinstance(data, list) else []


async def _load_alias_to_canon(conn, game: str) -> Dict[str, str]:
    rows = await conn.fetch(
        "SELECT alias, canonical FROM team_aliases WHERE game = $1 OR game = ''", game)
    return {str(r["alias"]).strip().lower(): str(r["canonical"]).strip().lower() for r in rows}


async def _load_candidates(conn, game: str, start_time, labelled_only: bool) -> List[Dict[str, Any]]:
    q = """SELECT match_id, team_a, team_b, start_time, winner FROM matches
            WHERE game = $1 AND start_time BETWEEN $2 - ($3 || ' hours')::interval
                                              AND $2 + ($3 || ' hours')::interval"""
    if labelled_only:
        q += " AND winner IS NOT NULL"
    rows = await conn.fetch(q, game, start_time, str(CANDIDATE_WINDOW_H))
    return [dict(r) for r in rows]


async def _already_backfilled(conn, match_id: str, books: List[str], start_time) -> bool:
    """Resume ledger: any pre-start oddspapi row for this match+books means a prior run
    (or the forward collector) already captured a pre-start line — don't respend quota."""
    row = await conn.fetchrow(
        """SELECT 1 FROM odds_raw
            WHERE match_id = $1 AND aggregator = 'oddspapi'
              AND book = ANY($2::text[]) AND line_time <= $3 LIMIT 1""",
        match_id, books, start_time)
    return row is not None


async def run(games: List[str], date_from: str, date_to: str, books: List[str],
              max_requests: int, dry_run: bool, include_unlinked: bool) -> None:
    import aiohttp
    if len(books) > 3:
        raise SystemExit("OddsPapi historical-odds allows max 3 bookmakers per request (live-verified)")
    pool = None
    if not dry_run:
        import asyncpg
        if not CONFIG.database_url:
            raise SystemExit("DATABASE_URL not set (or use --dry-run)")
        pool = await asyncpg.create_pool(CONFIG.database_url, min_size=1, max_size=2)
    elif not include_unlinked:
        log.warning("--dry-run without a DB cannot link fixtures to silo matches; "
                    "every fixture will report as unlinked (parse-check only)")

    spent = 0
    stats = {"fixtures_seen": 0, "linked": 0, "unlinked": 0, "ledger_skipped": 0,
             "pulled": 0, "not_found": 0, "rows_written": 0}
    coverage: Dict[str, int] = {}
    stale: List[float] = []

    async with aiohttp.ClientSession() as session:
        for game in games:
            alias_to_canon: Dict[str, str] = {}
            if pool:
                async with pool.acquire() as con:
                    alias_to_canon = await _load_alias_to_canon(con, game)
            for w_from, w_to in windows(date_from, date_to):
                if spent >= max_requests:
                    break
                fixtures = await _fetch_window_fixtures(session, game, w_from, w_to)
                spent += 1
                seen: set = set()
                targets: List[Tuple[dict, Optional[Dict[str, Any]]]] = []
                for fx in fixtures:
                    fid = str(fx.get("fixtureId") or "")
                    if not fid or fid in seen:
                        continue
                    seen.add(fid)
                    if str(fx.get("statusName") or "") != "Finished":
                        continue
                    stats["fixtures_seen"] += 1
                    link = None
                    if pool:
                        st = _ts(fx.get("startTime"))
                        if st is None:
                            continue
                        async with pool.acquire() as con:
                            cands = await _load_candidates(con, game, st, labelled_only=True)
                        link = link_fixture(fx, cands, alias_to_canon)
                    if link:
                        stats["linked"] += 1
                        targets.append((fx, link))
                    else:
                        stats["unlinked"] += 1
                        if include_unlinked:
                            targets.append((fx, None))
                        else:
                            log.info("unlinked (no labelled silo match): %s %s vs %s @ %s",
                                     fid, fx.get("participant1Name"), fx.get("participant2Name"),
                                     fx.get("startTime"))

                for fx, link in targets:
                    if spent >= max_requests:
                        log.warning("request budget exhausted (%d) — re-run the same command "
                                    "to resume (ledger skips completed fixtures)", max_requests)
                        break
                    fid = str(fx.get("fixtureId"))
                    st = _ts(fx.get("startTime"))
                    match_id = link["match_id"] if link else fid
                    if pool:
                        async with pool.acquire() as con:
                            if await _already_backfilled(con, match_id, books, st):
                                stats["ledger_skipped"] += 1
                                continue
                    data, status = await _get(session, "/historical-odds",
                                              {"fixtureId": fid, "bookmakers": ",".join(books)})
                    spent += 1
                    if not data:
                        stats["not_found"] += 1
                        log.info("no archive for %s (HTTP %s) — sharps absent or aged out", fid, status)
                        continue
                    stats["pulled"] += 1
                    parsed = parse_historical_odds(
                        data, str(fx.get("participant1Name") or ""),
                        str(fx.get("participant2Name") or ""), fx.get("startTime"))
                    rows, errs = shape_rows(parsed, books, bool(link and link["swapped"]),
                                            fx.get("startTime"))
                    for e in errs:
                        log.info("book skipped for %s: %s -> %s", fid, e.get("book"), e.get("error"))
                    if dry_run:
                        for r in rows:
                            log.info("DRY %s %s a=%.3f b=%.3f closing=%s stale=%.0fm", match_id,
                                     r["book"], r["team_a_odds"] or -1, r["team_b_odds"] or -1,
                                     r["is_closing"], r["stale_min"])
                        continue
                    async with pool.acquire() as con:
                        if link is None:  # --include-unlinked: fixture becomes its own match row
                            await con.execute(
                                """INSERT INTO matches (match_id, game, team_a, team_b, start_time,
                                                        source, raw_data)
                                   VALUES ($1,$2,$3,$4,$5,'aggregator',$6)
                                   ON CONFLICT (match_id) DO NOTHING""",
                                match_id, game, str(fx.get("participant1Name") or "?"),
                                str(fx.get("participant2Name") or "?"), st, json.dumps(fx))
                        for r in rows:
                            # APPEND-ONLY: plain INSERT, never UPSERT (schema commandment)
                            await con.execute(
                                """INSERT INTO odds_raw (match_id, book, aggregator, team_a_odds,
                                                         team_b_odds, is_closing, line_time)
                                   VALUES ($1,$2,'oddspapi',$3,$4,$5,$6)""",
                                match_id, r["book"], r["team_a_odds"], r["team_b_odds"],
                                r["is_closing"], r["line_time"])
                            stats["rows_written"] += 1
                            coverage[r["book"]] = coverage.get(r["book"], 0) + 1
                            stale.append(r["stale_min"])
                if spent >= max_requests:
                    break
            if spent >= max_requests:
                break
    if pool:
        await pool.close()

    log.info("=== backfill summary ===")
    log.info("requests spent: %d/%d", spent, max_requests)
    for k, v in stats.items():
        log.info("%s: %d", k, v)
    for b in books:
        (log.info if coverage.get(b) else log.warning)(
            "coverage book=%s rows=%d%s", b, coverage.get(b, 0),
            "" if coverage.get(b) else "  (expected for softs on old fixtures — they age out)")
    if stale:
        s = sorted(stale)
        log.info("line staleness (min before start): median=%.0f p90=%.0f max=%.0f  "
                 "(old fixtures are down-sampled — honest line_time is stored, "
                 "is_closing only within %dm)", s[len(s) // 2], s[int(len(s) * 0.9)], s[-1],
                 CLOSING_WINDOW_MIN)


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo historical closing-line backfill")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD (archive start)")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD (archive end)")
    ap.add_argument("--games", default=",".join(CONFIG.games),
                    help=f"comma list (default {','.join(CONFIG.games)})")
    ap.add_argument("--books", default=",".join(CONFIG.sharp_books),
                    help="max 3 (API limit); default the sharp trio")
    ap.add_argument("--max-requests", type=int, default=40,
                    help="hard API budget for this run (free tier: 250/month TOTAL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="no DB writes (STILL spends API requests)")
    ap.add_argument("--include-unlinked", action="store_true",
                    help="also pull fixtures with no labelled silo match (new matches rows)")
    args = ap.parse_args()
    asyncio.run(run([g.strip() for g in args.games.split(",") if g.strip()],
                    args.date_from, args.date_to,
                    [b.strip().lower() for b in args.books.split(",") if b.strip()],
                    args.max_requests, args.dry_run, args.include_unlinked))


if __name__ == "__main__":
    main()
