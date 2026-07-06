#!/usr/bin/env python3
"""esports_silo — forward RESULTS collector (PandaScore → matches.winner).

THE GAP THIS FILLS: the odds collector writes forward matches with NO winner, and the skill
gate can only prove the signal once matches settle. Without a winner feed, resolution (#8)
has nothing to score, `skill_proven` never turns true, and the halt can never lift. This
module is that feed: it polls PandaScore's finished matches and fills in winner + scores.

DESIGN (model A — PandaScore is the canonical match/result feed; verified: it's the one API
covering all four games under one free-tier key, and the historical import is keyed the same
way). Odds/markets link TO these rows via the matcher (a later wiring step — see HANDOFF).

Idempotent + settle-once: writes with ON CONFLICT DO UPDATE using COALESCE, so a winner/score
is filled when it first arrives and NEVER overwritten afterward (a settled result never flips).
`matches` is NOT append-only (unlike odds_raw) — it's ground truth, updated when a match ends.

Cmd discipline: winner names are mapped to team_a/team_b via the shared `map_winner` DEVIATION
(unmappable → NULL, logged, never guessed — Cmd 3/4). Only `status=finished` matches are written.

SEAM (unverifiable from a network-isolated session): the PandaScore results endpoint path +
JSON field names. `_fetch_finished` logs the first raw payload; `parse_result` reads several
candidate keys defensively and returns None on anything it can't parse. Pure parsing is tested.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from ..config import CONFIG, ODDSPAPI_SPORT_IDS  # ODDSPAPI_SPORT_IDS unused; import guarded
    from ..scripts.import_from_prior_bot import map_winner
except ImportError:  # loose-file / pure-import fallback
    try:
        from config import CONFIG  # type: ignore
    except ImportError:
        CONFIG = None  # type: ignore
    try:
        from scripts.import_from_prior_bot import map_winner  # type: ignore
    except ImportError:
        def map_winner(winner, team_a, team_b):  # type: ignore  # provenance: import_from_prior_bot
            if not winner:
                return None
            w, a, b = str(winner).strip(), str(team_a or "").strip(), str(team_b or "").strip()
            if w == a:
                return "team_a"
            if w == b:
                return "team_b"
            wl = w.lower()
            if b and wl in b.lower():
                return "team_b"
            if a and wl in a.lower():
                return "team_a"
            return None

log = logging.getLogger("results_collector")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

try:  # alias-aware team scorer (for attaching results to pinnodds match rows)
    from ..markets.match_matcher import team_present
except ImportError:  # loose-file fallback
    try:
        from markets.match_matcher import team_present  # type: ignore
    except ImportError:
        team_present = None  # type: ignore  # pure parse tests don't need it


def orient_winner(winner: Optional[str], ps_a: str, ps_b: str, row_a: str, row_b: str,
                  alias_map: Dict[str, List[str]], threshold: float = 80.0) -> Optional[str]:
    """Map a winner ('team_a'/'team_b' in PandaScore's team order) onto an EXISTING matches
    row's orientation (e.g. a pinnodds row for the same real-world match).

    Compares the PandaScore team names to the row's teams in BOTH orientations with the
    alias-aware scorer; the better orientation must clear `threshold` on its WEAKER side
    (both teams must correspond — one shared team with a different opponent scores low and
    returns None: different fixture, never guess). Returns 'team_a'/'team_b' in the ROW's
    orientation, flipped when the row stores the teams swapped, or None.
    """
    if winner not in ("team_a", "team_b") or team_present is None:
        return None
    ra, rb = (row_a or "").lower(), (row_b or "").lower()
    same = min(team_present(ps_a, ra, alias_map)[1], team_present(ps_b, rb, alias_map)[1])
    swapped = min(team_present(ps_a, rb, alias_map)[1], team_present(ps_b, ra, alias_map)[1])
    if max(same, swapped) < threshold:
        return None
    if same >= swapped:
        return winner
    return "team_b" if winner == "team_a" else "team_a"


async def _load_alias_map_safe(con, game: str) -> Dict[str, List[str]]:
    try:
        from ..markets.match_matcher import load_alias_map
    except ImportError:
        from markets.match_matcher import load_alias_map  # type: ignore
    return await load_alias_map(con, game)

# PandaScore videogame slug per silo game (SEAM: confirm slugs against a live payload).
_PS_SLUG = {"cs2": "cs-go", "lol": "league-of-legends", "dota2": "dota-2", "valorant": "valorant"}


def _ts(v):
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def parse_result(m: Dict[str, Any], game: str) -> Optional[Dict[str, Any]]:
    """One PandaScore finished-match payload → a matches-row dict, or None if unusable.

    Returns winner already mapped to 'team_a'|'team_b'|None. Pure + deterministic.
    """
    mid = m.get("id")
    if mid is None:
        return None
    opps = m.get("opponents") or []

    def opp(i, key):
        try:
            o = opps[i].get("opponent") or {}
            return o.get(key)
        except (IndexError, AttributeError, TypeError):
            return None

    team_a = str(opp(0, "name") or "").strip()
    team_b = str(opp(1, "name") or "").strip()
    if not team_a or not team_b:
        return None  # need both teams to map a winner (and for the matcher later)

    winner_obj = m.get("winner")
    winner_name = winner_obj.get("name") if isinstance(winner_obj, dict) else winner_obj
    winner = map_winner(winner_name, team_a, team_b)

    # scores: PandaScore `results` = [{team_id, score}]; map team_id → a/b via opponent ids.
    id_a, id_b = opp(0, "id"), opp(1, "id")
    score_a = score_b = None
    for r in (m.get("results") or []):
        try:
            tid, sc = r.get("team_id"), r.get("score")
        except AttributeError:
            continue
        if tid is not None and tid == id_a:
            score_a = sc
        elif tid is not None and tid == id_b:
            score_b = sc

    return {
        "match_id": f"ps_{mid}",
        "game": game,
        "team_a": team_a,
        "team_b": team_b,
        "winner": winner,
        "winner_name_raw": winner_name,
        "score_a": score_a,
        "score_b": score_b,
        "start_time": m.get("begin_at") or m.get("scheduled_at") or m.get("modified_at"),
        "status": str(m.get("status") or "").lower(),
    }


_logged = False


async def _fetch_finished(session, game: str, per_page: int = 50) -> List[Dict[str, Any]]:
    """SEAM: GET PandaScore finished matches for a game. Logs the first raw payload."""
    import aiohttp
    global _logged
    slug = _PS_SLUG.get(game)
    if slug is None:
        return []
    url = f"https://api.pandascore.co/{slug}/matches"  # VERIFY path
    params = {"filter[status]": "finished", "sort": "-begin_at", "per_page": per_page}
    headers = {"Authorization": f"Bearer {CONFIG.pandascore_api_key}", "Accept": "application/json"}
    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                log.warning("pandascore non-200 (%s) for %s", r.status, game)
                return []
            data = await r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("pandascore request failed for %s: %s", game, e)
        return []
    if not _logged:
        log.info("first raw pandascore payload — CONFIRM field mapping: %.600s", str(data))
        _logged = True
    return data if isinstance(data, list) else (data.get("data") or [])


async def run_once(dry_run: bool) -> None:
    import aiohttp
    pool = None
    if not dry_run:
        import asyncpg
        if not CONFIG.database_url:
            raise SystemExit("DATABASE_URL not set (or use --dry-run)")
        pool = await asyncpg.create_pool(CONFIG.database_url, min_size=1, max_size=2)

    filled = attached = skipped = unresolved = 0
    alias_maps: Dict[str, Dict[str, List[str]]] = {}
    async with aiohttp.ClientSession() as session:
        for game in CONFIG.games:
            finished = await _fetch_finished(session, game)
            log.info("finished matches fetched: %d (%s)", len(finished), game)
            for m in finished:
                rec = parse_result(m, game)
                if rec is None or rec["status"] != "finished":
                    continue
                st = _ts(rec["start_time"])
                if st is None:
                    skipped += 1
                    continue
                if rec["winner"] is None and rec["winner_name_raw"]:
                    unresolved += 1  # a stated winner we couldn't map — surfaced, left NULL
                if dry_run:
                    filled += 1
                    continue
                async with pool.acquire() as con:
                    # settle-once: fill winner/scores when they arrive, never overwrite.
                    await con.execute(
                        """INSERT INTO matches
                             (match_id, game, team_a, team_b, winner, score_a, score_b,
                              start_time, source)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pandascore')
                           ON CONFLICT (match_id) DO UPDATE SET
                             winner  = COALESCE(matches.winner,  EXCLUDED.winner),
                             score_a = COALESCE(matches.score_a, EXCLUDED.score_a),
                             score_b = COALESCE(matches.score_b, EXCLUDED.score_b)""",
                        rec["match_id"], game, rec["team_a"], rec["team_b"], rec["winner"],
                        rec["score_a"], rec["score_b"], st)
                    filled += 1
                    # ── ATTACH the winner to same-fixture rows from OTHER sources ──
                    # The odds collector writes pinnodds_* rows; predictions point at
                    # those. Without this, winners land only on the pandascore row and
                    # nothing ever resolves. Orientation-aware, settle-once, no guessing.
                    if rec["winner"] in ("team_a", "team_b"):
                        if game not in alias_maps:
                            alias_maps[game] = await _load_alias_map_safe(con, game)
                        cands = await con.fetch(
                            """SELECT match_id, team_a, team_b FROM matches
                                WHERE game = $1 AND winner IS NULL AND match_id <> $2
                                  AND start_time BETWEEN $3 - interval '36 hours'
                                                     AND $3 + interval '36 hours'""",
                            game, rec["match_id"], st)
                        for c in cands:
                            w = orient_winner(rec["winner"], rec["team_a"], rec["team_b"],
                                              str(c["team_a"]), str(c["team_b"]),
                                              alias_maps[game])
                            if w is None:
                                continue
                            sa, sb = rec["score_a"], rec["score_b"]
                            if w != rec["winner"]:  # row stores teams swapped — flip scores too
                                sa, sb = sb, sa
                            await con.execute(
                                """UPDATE matches SET
                                     winner  = COALESCE(winner, $2),
                                     score_a = COALESCE(score_a, $3),
                                     score_b = COALESCE(score_b, $4)
                                   WHERE match_id = $1 AND winner IS NULL""",
                                str(c["match_id"]), w, sa, sb)
                            attached += 1
                            log.info("winner attached to %s (%s vs %s) as %s",
                                     c["match_id"], c["team_a"], c["team_b"], w)
    if pool:
        await pool.close()
    log.info("results: filled/updated %d · attached-to-other-source %d · skipped(no time) %d "
             "· unmapped-winner %d", filled, attached, skipped, unresolved)


def main() -> None:
    ap = argparse.ArgumentParser(description="esports_silo forward results collector (PandaScore)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--dry-run", action="store_true", help="probe/parse only, no DB writes")
    args = ap.parse_args()
    if not args.once:
        raise SystemExit("only --once is implemented; schedule via the systemd timer / cron")
    asyncio.run(run_once(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
