#!/usr/bin/env python3
"""esports_silo — OddsPapi HISTORICAL-ODDS probe + parser (the calibrator-backfill seam).

WHY: OddsPapi's /v4/historical-odds returns the full pre-match line-move history per book, and
(WEB-SOURCED) historical odds are FREE for esports. If real, we can backfill CLOSING lines for
our 28k already-labelled matches and FIT the calibrator on day one instead of waiting weeks —
while the skill gate still proves on forward data (a backtest fits; it does not prove).

TWO MODES (operator, on the box — public v4 API, needs ODDSPAPI_API_KEY):
  --list-fixtures GAME   GET /v4/fixtures for a game → prints fixtureId + teams + start, so you
                         can grab a REAL fixtureId to test with.
  --fixture-id ID        GET /v4/historical-odds?fixtureId=ID&bookmakers=<SHARP_BOOKS> → dumps
                         the raw payload (confirm the shape) AND runs the parser, printing the
                         closing line it extracted per book. Writes raw+parsed to --out JSONL.

WHAT TO CONFIRM (paste the raw dump back):
  1. Are esports fixtures actually free on historical-odds (or 402/403)?  2. How far back does
  esports history go?  3. Do pinnacle/singbet/thunderpick return data?  4. Do the real field
  names match `parse_historical_odds`'s assumptions below? (they are WEB-SOURCED, not verified.)

SEAM: the response shape is reconstructed from the public docs, NOT a live payload —
`parse_historical_odds` reads defensively and returns what it can, flagging the rest. The pure
parser is unit-tested against that documented shape; once you paste a real payload I lock it in
one pass and build the full backfill (reuses the matcher for fixture-mapping + odds_raw writers).

Assumed shape (documented; VERIFY):
  {"fixtureId": "...",
   "bookmakers": {"pinnacle": {"markets": [
        {"key": "moneyline",
         "outcomes": [{"name": "NAVI", "odds": [{"price": 1.85, "createdAt": "..T10:00Z"}, ...]},
                      {"name": "FaZe", "odds": [{"price": 2.10, "createdAt": "..T10:00Z"}, ...]}]}]}}}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from ..config import CONFIG, ODDSPAPI_SPORT_IDS
except ImportError:  # loose-file / pure-import fallback
    CONFIG = None  # type: ignore
    ODDSPAPI_SPORT_IDS = {"dota2": 16, "cs2": 17, "lol": 18, "cod": 56, "rl": 59, "valorant": 61}

log = logging.getLogger("probe_historical_odds")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

BASE = "https://api.oddspapi.io/v4"
_MONEYLINE_HINTS = ("moneyline", "match", "winner", "1x2", "ml", "h2h")


def _ts(v):
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _price(o: Dict[str, Any]) -> Optional[float]:
    for k in ("price", "odds", "decimal", "value"):
        if o.get(k) is not None:
            try:
                return float(o[k])
            except (TypeError, ValueError):
                return None
    return None


def _time(o: Dict[str, Any]):
    for k in ("createdAt", "created_at", "timestamp", "line_time", "updatedAt"):
        if o.get(k) is not None:
            return _ts(o[k])
    return None


def _pick_market(markets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The match-winner market: a hint match, else the first market with exactly 2 outcomes."""
    for m in markets or []:
        key = str(m.get("key") or m.get("name") or m.get("type") or "").lower()
        if any(h in key for h in _MONEYLINE_HINTS) and len(m.get("outcomes") or []) == 2:
            return m
    for m in markets or []:
        if len(m.get("outcomes") or []) == 2:
            return m
    return None


def _closing(outcome: Dict[str, Any], before: Optional[datetime]):
    """(closing_price, closing_time, n_moves) — last price at/adjacent to the match start."""
    series = outcome.get("odds") or outcome.get("history") or outcome.get("prices") or []
    pts = [(_time(p), _price(p)) for p in series]
    pts = [(t, pr) for t, pr in pts if pr is not None]
    if not pts:
        # some payloads may carry a single flat price on the outcome itself
        pr = _price(outcome)
        return (pr, None, 0) if pr is not None else (None, None, 0)
    dated = [(t, pr) for t, pr in pts if t is not None]
    if before is not None:
        pre = [(t, pr) for t, pr in dated if t <= before]
        if pre:
            t, pr = max(pre, key=lambda x: x[0])
            return pr, t, len(pts)
    if dated:
        t, pr = max(dated, key=lambda x: x[0])
        return pr, t, len(pts)
    return pts[-1][1], None, len(pts)  # no timestamps — take the last listed


def parse_historical_odds(payload: Dict[str, Any], team_a: str, team_b: str,
                          start_time: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Extract the CLOSING line per book → odds_raw-shaped rows. Pure; SEAM field names.

    Aligns the two outcomes to team_a/team_b by name (substring, either direction). A book
    whose outcomes can't be aligned to both teams is returned with an `error` flag, never guessed.
    """
    before = _ts(start_time)
    a_lc, b_lc = str(team_a or "").lower(), str(team_b or "").lower()
    books = payload.get("bookmakers") or payload.get("bookmakerOdds") or {}
    out: List[Dict[str, Any]] = []
    # bookmakers may be a dict {slug: {...}} or a list [{slug/name, markets}]
    items = books.items() if isinstance(books, dict) else [
        (b.get("slug") or b.get("name") or b.get("bookmaker"), b) for b in books]
    for slug, bdata in items:
        market = _pick_market((bdata or {}).get("markets") or [])
        if market is None:
            out.append({"book": slug, "error": "no 2-outcome market found (SEAM)"})
            continue
        oc = market.get("outcomes") or []
        a_oc = b_oc = None
        for o in oc:
            name = str(o.get("name") or o.get("outcome") or o.get("label") or "").lower()
            if name and (name in a_lc or a_lc in name):
                a_oc = o
            elif name and (name in b_lc or b_lc in name):
                b_oc = o
        if a_oc is None or b_oc is None and len(oc) == 2:
            # fall back to positional alignment (outcome order = team order) if names didn't map
            a_oc, b_oc = a_oc or oc[0], b_oc or oc[1]
        if a_oc is None or b_oc is None:
            out.append({"book": slug, "error": "could not align outcomes to both teams (SEAM)"})
            continue
        a_price, a_time, a_moves = _closing(a_oc, before)
        b_price, b_time, b_moves = _closing(b_oc, before)
        line_time = max([t for t in (a_time, b_time) if t is not None], default=None)
        out.append({
            "book": str(slug or "").lower(),
            "team_a_odds": a_price, "team_b_odds": b_price,
            "line_time": line_time.isoformat() if line_time else None,
            "is_closing": True, "n_moves": max(a_moves, b_moves),
        })
    return out


# ======================================================================================
# I/O (operator's box)
# ======================================================================================
async def _get(session, path: str, params: dict):
    import aiohttp
    params = {**params, "apiKey": CONFIG.oddspapi_api_key}  # VERIFY auth param name
    try:
        async with session.get(f"{BASE}{path}", params=params,
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = await r.json()
            if r.status != 200:
                log.warning("%s HTTP %s: %.300s", path.lstrip("/"), r.status, str(body))
                return None, r.status
            return body, 200
    except Exception as e:  # noqa: BLE001
        log.warning("request failed: %s", e)
        return None, 0
    finally:
        await asyncio.sleep(5.5)  # documented 5000ms cooldown + margin


async def list_fixtures(game: str) -> None:
    import aiohttp
    sid = ODDSPAPI_SPORT_IDS.get(game)
    async with aiohttp.ClientSession() as s:
        data, status = await _get(s, "/fixtures", {"sport_id": sid, "days_back": 3})
    if not data:
        log.error("no fixtures (HTTP %s) — confirm key + esports coverage", status)
        return
    fixtures = data.get("data") or data.get("fixtures") or data if isinstance(data, dict) else data
    log.info("first raw fixtures payload: %.500s", str(data))
    for f in (fixtures or [])[:20]:
        print(f"  fixtureId={f.get('id') or f.get('fixture_id')}  "
              f"{f.get('home') or f.get('team_a')} vs {f.get('away') or f.get('team_b')}  "
              f"{f.get('start_time') or f.get('begin_at')}")


async def probe_fixture(fixture_id: str, books: List[str], out_path: str,
                        team_a: str, team_b: str, start_time: Optional[str]) -> None:
    import aiohttp
    async with aiohttp.ClientSession() as s:
        data, status = await _get(s, "/historical-odds",
                                  {"fixtureId": fixture_id, "bookmakers": ",".join(books)})
    if not data:
        log.error("no historical odds (HTTP %s). 402=quota, 403=not on plan, 404=no esports history",
                  status)
        return
    print("=== RAW PAYLOAD (confirm field names) ===")
    print(json.dumps(data, indent=2, default=str)[:2000])
    parsed = parse_historical_odds(data, team_a, team_b, start_time)
    print("\n=== PARSED CLOSING LINES (what the backfill would write to odds_raw) ===")
    for row in parsed:
        print(f"  {row}")
    with open(out_path, "a") as f:
        f.write(json.dumps({"fixture_id": fixture_id, "raw": data, "parsed": parsed}, default=str) + "\n")
    log.info("wrote raw+parsed -> %s", out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description="OddsPapi historical-odds probe/test")
    ap.add_argument("--list-fixtures", metavar="GAME", help="list recent fixture IDs for a game")
    ap.add_argument("--fixture-id", help="pull + parse historical odds for one fixture")
    ap.add_argument("--books", default="pinnacle,singbet,thunderpick")
    ap.add_argument("--team-a", default="", help="team_a name (to align outcomes)")
    ap.add_argument("--team-b", default="", help="team_b name")
    ap.add_argument("--start-time", default=None, help="match start ISO (picks the closing line)")
    ap.add_argument("--out", default="historical_odds_probe.jsonl")
    args = ap.parse_args()
    if args.list_fixtures:
        asyncio.run(list_fixtures(args.list_fixtures))
    elif args.fixture_id:
        asyncio.run(probe_fixture(args.fixture_id, [b.strip() for b in args.books.split(",")],
                                  args.out, args.team_a, args.team_b, args.start_time))
    else:
        raise SystemExit("pass --list-fixtures GAME or --fixture-id ID")


if __name__ == "__main__":
    main()
