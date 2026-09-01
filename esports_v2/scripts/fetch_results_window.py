#!/usr/bin/env python3
"""Fetch finished-match RESULTS for a recent date window (GAP A closer).

The forward-collected odds+PM snapshots (GAP B) need same-window match RESULTS
to be labeled — the free results on disk end 2026-04-14, so the join yields 0
on forward data until fresh results exist (handoff GAP A). This script pulls
finished matches from PandaScore for the last N days across the games the
collector actually sees (Valorant / CS2 / LoL / Dota 2) and writes them in the
EXACT bulk-JSONL row shape ``results_join.load_bulk_results`` already consumes:

    {"match_id", "game", "team_a", "team_b", "winner", "match_date", "source"}

so the eval driver runs unchanged:
    python -m esports_v2.scripts.eval_sharp_line \
        --snapshots <snapshots.jsonl> --bulk <this script's --out> ...

STDLIB-ONLY (urllib): this is designed to run on the VPS system python (like
``deploy/vps/collect_pinnodds_standalone.py``) where third-party packages are
not guaranteed. ``PandaScoreLoader`` (requests-based, RawMatch-shaped) is the
deep-history sibling; this one is deliberately a thin, window-scoped fetch.

CORRECT-OR-ABSENT: a match is emitted only when it is finished with a match id,
two non-empty team names, a non-empty winner NAME, and a parseable date. A
missing/ambiguous winner -> the row is skipped, never guessed (the join's
bijective matcher + multi-winner ambiguity guard do the rest downstream).

Env:  PANDASCORE_API_KEY  (same key the live bot requires)
Usage:
    python -m esports_v2.scripts.fetch_results_window \
        --days-back 4 --games valorant,cs2,lol,dota2,r6 --out results_window.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("fetch_results_window")

_BASE_URL = "https://api.pandascore.co"
_PER_PAGE = 100
_MAX_PAGES = 10          # window is days, not years — 1000 matches/game is plenty
_REQ_DELAY = 1.2         # free tier: 1000 req/hr; stay far under it

# PandaScore API-path slugs (CS2 still lives under the legacy "csgo" slug, same
# as pandascore_loader.GAME_SLUGS; R6 path is "r6siege" — verified live
# 2026-07-10 against /r6siege/matches/past).
GAME_SLUGS = {
    "valorant": "valorant",
    "cs2": "csgo",
    "lol": "lol",
    "dota2": "dota2",
    "r6": "r6siege",
}

# Injectable HTTP GET: url -> parsed JSON list, or None on failure.
HttpGet = Callable[[str], Optional[list]]


def parse_match_row(raw: dict, game: str) -> Optional[dict]:
    """One PandaScore past-match dict -> a bulk-JSONL results row, or None.

    Field access mirrors ``PandaScoreLoader._parse_match`` (opponents[].opponent
    .name, winner.name, results[].score fallback) — correct-or-absent on any
    missing piece.
    """
    if not isinstance(raw, dict):
        return None
    match_id = raw.get("id")
    if not match_id:
        return None
    if str(raw.get("status") or "") not in ("finished",):
        return None

    opponents = raw.get("opponents") or []
    if len(opponents) < 2:
        return None
    a_data = opponents[0].get("opponent", {}) if isinstance(opponents[0], dict) else {}
    b_data = opponents[1].get("opponent", {}) if isinstance(opponents[1], dict) else {}
    team_a = str((a_data or {}).get("name") or "").strip()
    team_b = str((b_data or {}).get("name") or "").strip()
    if not team_a or not team_b:
        return None

    winner = None
    winner_data = raw.get("winner")
    if isinstance(winner_data, dict):
        winner = str(winner_data.get("name") or "").strip() or None
    if winner is None:
        # Score fallback: results[] entries carry their own team_id and are NOT
        # guaranteed to be in opponents[] order — map by id, never by position
        # (a positional guess can flip the winner, the exact label-corruption
        # the join is built to avoid).
        id_to_name = {}
        for od, name in ((a_data, team_a), (b_data, team_b)):
            tid = (od or {}).get("id")
            if tid is not None:
                id_to_name[tid] = name
        scores = {}
        for res in (raw.get("results") or []):
            if not isinstance(res, dict):
                continue
            tid = res.get("team_id")
            try:
                score = int(res.get("score"))
            except (ValueError, TypeError):
                continue
            if tid in id_to_name:
                scores[tid] = score
        if len(scores) == 2:
            (t1, s1), (t2, s2) = scores.items()
            if s1 != s2:
                winner = id_to_name[t1] if s1 > s2 else id_to_name[t2]
    if not winner:
        return None  # no clear winner -> never guess

    date_str = raw.get("begin_at") or raw.get("scheduled_at") or raw.get("end_at")
    if not date_str:
        return None

    return {
        "match_id": f"psw_{game}_{match_id}",
        "game": game,
        "team_a": team_a,
        "team_b": team_b,
        "winner": winner,
        "match_date": str(date_str),
        "source": "pandascore_window",
    }


def fetch_window(
    games: List[str],
    since: str,
    until: str,
    *,
    http_get: Optional[HttpGet] = None,
) -> List[dict]:
    """Fetch finished matches for each game in [since, until]; return rows.

    Filters server-side on ``end_at`` (a match SCHEDULED before the window but
    FINISHED inside it still counts as a result for the window)."""
    http_get = http_get or _default_http_get
    rows: List[dict] = []
    for game in games:
        slug = GAME_SLUGS.get(game)
        if slug is None:
            logger.warning(f"unknown game '{game}' — skipped (use {list(GAME_SLUGS)})")
            continue
        n_game = 0
        for page in range(1, _MAX_PAGES + 1):
            params = {
                "per_page": _PER_PAGE,
                "page": page,
                "sort": "-end_at",
                "range[end_at]": f"{since},{until}",
                "filter[status]": "finished",
            }
            url = f"{_BASE_URL}/{slug}/matches/past?{urllib.parse.urlencode(params)}"
            data = http_get(url)
            if not data or not isinstance(data, list):
                break
            for raw in data:
                row = parse_match_row(raw, game)
                if row:
                    rows.append(row)
                    n_game += 1
            if len(data) < _PER_PAGE:
                break
            time.sleep(_REQ_DELAY)
        logger.info(f"results_window game={game} rows={n_game}")
    return rows


def write_jsonl(rows: List[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def _default_http_get(url: str) -> Optional[list]:
    """Authorized GET -> JSON list, or None. 429 Retry-After respected."""
    # .env values sometimes arrive quoted ('KEY="abc"') or CRLF-tailed when a
    # shell exports them verbatim — strip both, they are never part of a token.
    key = os.environ.get("PANDASCORE_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise SystemExit("PANDASCORE_API_KEY not set in environment")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                ra = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait = min(int(ra), 60) if ra else 2 ** attempt
                except ValueError:
                    wait = 2 ** attempt
                logger.warning(f"pandascore_429 attempt={attempt} sleep={wait}s")
                time.sleep(max(1, wait))
                continue
            logger.error(f"pandascore_http_{e.code} url={url[:90]}")
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            logger.error(f"pandascore_transport {type(e).__name__}")
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a recent results window (PandaScore)")
    ap.add_argument("--days-back", type=int, default=4)
    ap.add_argument("--games", default="valorant,cs2,lol,dota2,r6",
                    help=f"csv of {list(GAME_SLUGS)}")
    ap.add_argument("--out", required=True, help="output bulk-shaped JSONL path")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=args.days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    games = [g.strip() for g in args.games.split(",") if g.strip()]

    rows = fetch_window(games, since, until)
    n = write_jsonl(rows, Path(args.out))
    print(f"wrote {n} result rows ({since} .. {until}, games={games}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
