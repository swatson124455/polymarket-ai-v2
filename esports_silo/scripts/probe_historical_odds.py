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

REAL shape — LIVE-VERIFIED from a full /v4/odds payload (2026-07-03):
  {"fixtureId": "...", "bookmakerOdds": {"<slug>": {"suspended": false, "markets": {
      "171": {"marketActive": true, "outcomes": {          # market-type 171 = MATCH WINNER
          "171": {"players": {"0": {"price": 1.38, "changedAt": "...", ...}}},   # participant1
          "172": {"players": {"0": {"price": 2.80, ...}}}}}}}}                    # participant2
  Exchanges (polymarket/kalshi/betfair-ex) add exchangeMeta{back/lay+sizes}; price = best back.
  Historical payloads are expected to carry a series per player (odds/history list) — the
  extractor handles both a single price+changedAt and a series. Legacy list-shaped markets
  (the old documented shape) are still parsed as a fallback.
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
    # changedAt is the LIVE-VERIFIED key in the real /v4/odds payload
    for k in ("createdAt", "created_at", "changedAt", "timestamp", "line_time", "updatedAt"):
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


# LIVE-VERIFIED market/outcome type IDs (full /v4/odds payload, 2026-07-03):
# market "171" = match winner; its outcome "171" = participant1 (team_a), "172" = participant2.
MATCH_WINNER_MARKET = "171"
OUTCOME_TEAM_A, OUTCOME_TEAM_B = "171", "172"


def _player_points(outcome: Dict[str, Any]):
    """All (time, price) points for an outcome in the REAL schema.

    outcome = {"players": {"0": {...}}}. A player entry may carry a single price+changedAt
    (current /odds shape, live-verified) or a series under odds/history/prices (expected
    historical shape). Returns points from the first player entry.
    """
    players = outcome.get("players") or {}
    entry = None
    if isinstance(players, dict) and players:
        entry = players.get("0") or next(iter(players.values()))
    if not isinstance(entry, dict):
        return []
    series = entry.get("odds") or entry.get("history") or entry.get("prices")
    if isinstance(series, list) and series:
        return [(_time(p), _price(p)) for p in series if isinstance(p, dict)]
    pr = _price(entry)
    if pr is None:
        return []
    return [(_time(entry) or _ts(entry.get("bookmakerChangedAt")), pr)]


def _closing_from_points(points, before: Optional[datetime]):
    pts = [(t, pr) for t, pr in points if pr is not None]
    if not pts:
        return None, None, 0
    dated = [(t, pr) for t, pr in pts if t is not None]
    if before is not None:
        pre = [(t, pr) for t, pr in dated if t <= before]
        if pre:
            t, pr = max(pre, key=lambda x: x[0])
            return pr, t, len(pts)
    if dated:
        t, pr = max(dated, key=lambda x: x[0])
        return pr, t, len(pts)
    return pts[-1][1], None, len(pts)


def parse_historical_odds(payload: Dict[str, Any], team_a: str, team_b: str,
                          start_time: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Extract the CLOSING match-winner line per book → odds_raw-shaped rows. Pure.

    REAL schema (dict markets keyed by type ID — live-verified): alignment is deterministic —
    market 171, outcome 171→team_a / 172→team_b (participant order); no name matching needed.
    Legacy list-shaped markets fall back to the old name/positional alignment. A book that
    can't be parsed is returned with an `error` flag, never guessed.
    """
    before = _ts(start_time)
    books = payload.get("bookmakerOdds") or payload.get("bookmakers") or {}
    out: List[Dict[str, Any]] = []
    items = books.items() if isinstance(books, dict) else [
        (b.get("slug") or b.get("name") or b.get("bookmaker"), b) for b in books]
    for slug, bdata in items:
        bdata = bdata or {}
        markets = bdata.get("markets")
        if isinstance(markets, dict):  # REAL schema
            if bdata.get("suspended"):
                out.append({"book": slug, "error": "suspended"})
                continue
            m = markets.get(MATCH_WINNER_MARKET)
            if not isinstance(m, dict):
                m = next((mm for mm in markets.values() if isinstance(mm, dict)
                          and {OUTCOME_TEAM_A, OUTCOME_TEAM_B} <= set((mm.get("outcomes") or {}).keys())),
                         None)
            if not isinstance(m, dict):
                out.append({"book": slug, "error": "no match-winner (171) market"})
                continue
            oc = m.get("outcomes") or {}
            a_oc, b_oc = oc.get(OUTCOME_TEAM_A), oc.get(OUTCOME_TEAM_B)
            if not (isinstance(a_oc, dict) and isinstance(b_oc, dict)):
                out.append({"book": slug, "error": "missing 171/172 outcomes"})
                continue
            a_price, a_time, a_moves = _closing_from_points(_player_points(a_oc), before)
            b_price, b_time, b_moves = _closing_from_points(_player_points(b_oc), before)
        else:  # legacy list-shaped markets (old documented shape; kept for compatibility)
            market = _pick_market(markets or [])
            if market is None:
                out.append({"book": slug, "error": "no 2-outcome market found (SEAM)"})
                continue
            loc = market.get("outcomes") or []
            a_lc, b_lc = str(team_a or "").lower(), str(team_b or "").lower()
            a_oc = b_oc = None
            for o in loc:
                name = str(o.get("name") or o.get("outcome") or o.get("label") or "").lower()
                if name and (name in a_lc or a_lc in name):
                    a_oc = o
                elif name and (name in b_lc or b_lc in name):
                    b_oc = o
            if (a_oc is None or b_oc is None) and len(loc) == 2:
                a_oc, b_oc = a_oc or loc[0], b_oc or loc[1]
            if a_oc is None or b_oc is None:
                out.append({"book": slug, "error": "could not align outcomes to both teams (SEAM)"})
                continue
            a_price, a_time, a_moves = _closing(a_oc, before)
            b_price, b_time, b_moves = _closing(b_oc, before)
        if a_price is None and b_price is None:
            out.append({"book": slug, "error": "no prices found"})
            continue
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
    """GET with DUAL auth support (both are official OddsPapi routes):
      * direct:   key from oddspapi.io dashboard  -> ?apiKey=  on api.oddspapi.io/v4
      * RapidAPI: key from rapidapi.com odds-api1 -> X-RapidAPI-Key header on
                  odds-api1.p.rapidapi.com  (set RAPIDAPI_KEY to use this route)
    ODDSPAPI_BASE overrides the base URL in either mode (RapidAPI path prefix = SEAM)."""
    import os
    import aiohttp
    rapid_key = os.getenv("RAPIDAPI_KEY", "") or os.getenv("ODDSPAPI_RAPIDAPI_KEY", "")
    headers = {"Accept": "application/json"}
    if rapid_key:
        base = os.getenv("ODDSPAPI_BASE", "https://odds-api1.p.rapidapi.com")
        headers["X-RapidAPI-Key"] = rapid_key
        headers["X-RapidAPI-Host"] = base.split("//", 1)[-1]
    else:
        base = os.getenv("ODDSPAPI_BASE", BASE)
        direct_key = (getattr(CONFIG, "oddspapi_api_key", "") if CONFIG else "") \
            or os.getenv("ODDSPAPI_API_KEY", "")
        params = {**params, "apiKey": direct_key}
    try:
        async with session.get(f"{base}{path}", params=params, headers=headers,
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


async def list_fixtures(game: str, date_from: Optional[str] = None,
                        date_to: Optional[str] = None) -> None:
    import aiohttp
    from datetime import timedelta
    sid = ODDSPAPI_SPORT_IDS.get(game)
    # LIVE-VERIFIED 2026-07-03: params are camelCase `sportId` + REQUIRED `from`/`to`
    # (the API: "'to' and 'from' parameters are required when solely 'sportId' is provided.
    # They must be under 10 days apart."). Default window: 5 days back .. 4 days ahead.
    today = datetime.now(timezone.utc).date()
    f = date_from or str(today - timedelta(days=5))
    t = date_to or str(today + timedelta(days=4))
    async with aiohttp.ClientSession() as s:
        data, status = await _get(s, "/fixtures", {"sportId": sid, "from": f, "to": t})
    if not data:
        log.error("no fixtures (HTTP %s) — confirm key + esports coverage", status)
        return
    fixtures = data.get("data") or data.get("fixtures") or data if isinstance(data, dict) else data
    log.info("first raw fixtures payload: %.500s", str(data))
    # LIVE-VERIFIED 2026-07-03 fixture fields (full browser payload): fixtureId,
    # participant1Name/participant2Name (+ShortName/Abbr — names ARE present; an earlier
    # truncated log suggested otherwise), participant1Id/participant2Id, sportId,
    # tournamentId/tournamentName, statusId/statusName (0=Pre-Game, 2=Finished), hasOdds,
    # startTime/trueStartTime/trueEndTime, externalProviders{betradarId, pinnacleId,
    # opticoddsId, oddinId, mollybetId, ...}. hasOdds appeared True mostly on UPCOMING
    # fixtures in the sample — may mean "odds currently available", not "history exists";
    # the historical-odds probe on a FINISHED fixture settles that.
    fixtures = sorted(fixtures or [], key=lambda f: not f.get("hasOdds"))
    n_odds = sum(1 for f in fixtures if f.get("hasOdds"))
    print(f"  ({len(fixtures)} fixtures in window; {n_odds} with hasOdds=True)")
    for f in fixtures[:20]:
        pin = (f.get("externalProviders") or {}).get("pinnacleId")
        print(f"  fixtureId={f.get('fixtureId')}  hasOdds={f.get('hasOdds')}  "
              f"{f.get('participant1Name')} vs {f.get('participant2Name')}  "
              f"start={f.get('startTime')}  status={f.get('statusName')}  pinnacleId={pin}")


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
    ap.add_argument("--from", dest="date_from", default=None,
                    help="fixtures window start YYYY-MM-DD (default: 5 days ago; max 10-day span)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="fixtures window end YYYY-MM-DD (default: 4 days ahead)")
    ap.add_argument("--fixture-id", help="pull + parse historical odds for one fixture")
    ap.add_argument("--books", default="pinnacle,singbet,sbobet")  # live-verified slugs
    ap.add_argument("--team-a", default="", help="team_a name (to align outcomes)")
    ap.add_argument("--team-b", default="", help="team_b name")
    ap.add_argument("--start-time", default=None, help="match start ISO (picks the closing line)")
    ap.add_argument("--out", default="historical_odds_probe.jsonl")
    args = ap.parse_args()
    if args.list_fixtures:
        asyncio.run(list_fixtures(args.list_fixtures, args.date_from, args.date_to))
    elif args.fixture_id:
        asyncio.run(probe_fixture(args.fixture_id, [b.strip() for b in args.books.split(",")],
                                  args.out, args.team_a, args.team_b, args.start_time))
    else:
        raise SystemExit("pass --list-fixtures GAME or --fixture-id ID")


if __name__ == "__main__":
    main()
